# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Per-folder policy — Phase B6.

Default: every folder has Privacy Lock and Oversight enabled. A user can
**explicitly opt out** of either, per folder, by writing a policy file at
the folder root. The opt-out requires an acknowledgement: the system never
silently lowers protection — it makes you say "I accept the risk".

Policy file format (``.workspace-policy.json`` at the folder root; the legacy
``.workspaceversum-policy.json`` is still read for back-compat and slated for
removal in 0.8):

.. code-block:: json

    {
      "privacy_lock_enabled": true,
      "oversight_enabled": true,
      "oversight_default_level": "approve",
      "acknowledgements": {
        "lock_disable": {
          "accepted_at": "...",
          "accepted_by": "...",
          "disclaimer_version": "1"
        }
      }
    }

When a user issues ``workspace-l0 policy disable-lock --i-accept-the-risk``,
the disclaimer text below is shown, the acknowledgement is recorded, the
policy file is rewritten, and an audit-log entry is appended (kind="policy").

Subsequent skills running in the folder MUST consult :func:`load_policy` to
decide whether to apply Privacy Lock + Oversight. The default-on stance
means callers that don't bother to check still get protection. Disabling is
opt-in for both the user and the implementation.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .mutation_log import LogEvent, MutationLog


POLICY_FILENAME = ".workspace-policy.json"
"""Canonical filename for the per-folder policy. Default: absent (implicit: full protection)."""

LEGACY_POLICY_FILENAME = ".workspaceversum-policy.json"
"""Pre-rename filename. Still read by :func:`load_policy` for back-compat;
:func:`save_policy` always writes :data:`POLICY_FILENAME`. Slated for removal in 0.8."""


LOCK_DISCLAIMER = """
You are about to DISABLE PRIVACY LOCK for this folder.

What this means:
  - Outbound LLM calls from skills running in this folder will NOT be
    scanned for PII (regex layer) or confidential context (KG layer).
  - The egress proxy will pass requests straight through to the cloud
    provider with no minimisation.
  - The audit log will record every call (you keep visibility), but no
    blocking, no minimisation, no user prompts.

When you might want this:
  - The folder contains only public material and you want zero friction.
  - You are knowingly experimenting with prompts that would otherwise
    be refused (regex matches, confidential terms).

When you do NOT want this:
  - The folder contains client data, personal data, confidential
    business context, or anything covered by GDPR / professional secrecy.
  - You're not sure whether the folder contains protected material.

You can re-enable Privacy Lock at any time with:
  workspace-l0 policy enable-lock --folder <path>

This action will be audit-logged in the folder's mutation log with the
timestamp + the disclaimer version. Future audits can verify what was
in effect when.
""".strip()


OVERSIGHT_DISCLAIMER = """
You are about to DISABLE OVERSIGHT for this folder.

What this means:
  - Skills running in this folder will NOT pause for APPROVE / SUPERVISED
    prompts. Decisions previously routed to you as the user will be made
    autonomously.
  - Privacy-class floors (sensitive → APPROVE; regulated → SUPERVISED)
    will not engage — every operation runs at AUTONOMOUS effectively.
  - The audit log still records every decision (you keep visibility),
    but no prompts will appear in your terminal / Cowork / Cursor sidebar.

When you might want this:
  - The folder is a scratch / dev workspace and prompts are friction.
  - You're running an unattended pipeline and want it to complete without
    interactive blocks.

When you do NOT want this:
  - Anything where you want the option to intervene before a side-effect.
  - Sensitive or regulated data flows.

You can re-enable Oversight at any time with:
  workspace-l0 policy enable-oversight --folder <path>

This action will be audit-logged.
""".strip()


CURRENT_DISCLAIMER_VERSION = "1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InvalidPolicy(ValueError):
    """Raised when a policy YAML/JSON declaration contains an invalid value
    (e.g. a ``local_llm.mode`` outside the three allowed states). The policy
    file is left untouched; the caller decides whether to fail-safe to default
    or surface the error to the user."""


# ---------------------------------------------------------------------------
# local_llm sub-policy (0.6.8.2 — air-gap commitment D22 + BYOM § 5 fail-over)
# ---------------------------------------------------------------------------

#: ``mode`` values for ``local_llm``.
LOCAL_LLM_MODE_CLOUD_ALLOWED   = "cloud-allowed"
LOCAL_LLM_MODE_LOCAL_ONLY      = "local-only"
LOCAL_LLM_MODE_CLOUD_FALLBACK  = "cloud-fallback"
VALID_LOCAL_LLM_MODES = frozenset({
    LOCAL_LLM_MODE_CLOUD_ALLOWED,
    LOCAL_LLM_MODE_LOCAL_ONLY,
    LOCAL_LLM_MODE_CLOUD_FALLBACK,
})

#: ``on_insufficient`` values — what to do when the local model returns
#: ``INSUFFICIENT`` (the Tier C escalate-rather-than-guess signal).
LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD = "escalate-to-cloud"
LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_HUMAN = "escalate-to-human"
LOCAL_LLM_ON_INSUFFICIENT_REFUSE         = "refuse"
VALID_LOCAL_LLM_ON_INSUFFICIENT = frozenset({
    LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD,
    LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_HUMAN,
    LOCAL_LLM_ON_INSUFFICIENT_REFUSE,
})


@dataclass
class LocalLlmPolicy:
    """Per-folder local-LLM routing + fail-over policy.

    The three keys map to local model configuration:

    - ``route_by_kind``: maps a Workspace role (``validator``, ``lock-c``,
      ``intent-router``, ``code-fix``, …) to a registered model id. Empty
      mapping means "fall through to system-wide registry defaults".
    - ``mode``: ``cloud-allowed`` (default; local preferred, cloud fallback
      legal) / ``local-only`` (air-gap; refuse any cloud egress regardless
      of fail-over policy) / ``cloud-fallback`` (always-try-local-first
      with explicit cloud escalation on local failure).
    - ``on_insufficient``: ``escalate-to-cloud`` (default; gate the prompt
      and try cloud) / ``escalate-to-human`` (emit oversight event, wait)
      / ``refuse`` (return the INSUFFICIENT to the caller, no escalation).

    Validation:

    - ``mode`` and ``on_insufficient`` must be one of the documented values.
      Anything else raises :class:`InvalidPolicy` at load time so the user
      learns at write time, not at first call.
    """

    route_by_kind: dict[str, str] = field(default_factory=dict)
    mode: str = LOCAL_LLM_MODE_CLOUD_ALLOWED
    on_insufficient: str = LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_by_kind": dict(self.route_by_kind),
            "mode": self.mode,
            "on_insufficient": self.on_insufficient,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "LocalLlmPolicy":
        """Parse a ``local_llm`` block. Empty / missing block returns defaults.

        Raises :class:`InvalidPolicy` on unknown ``mode`` or
        ``on_insufficient``.
        """
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise InvalidPolicy(
                f"local_llm block must be a dict; got {type(d).__name__}"
            )
        raw_route = d.get("route_by_kind") or {}
        if not isinstance(raw_route, dict):
            raise InvalidPolicy(
                f"local_llm.route_by_kind must be a dict; got "
                f"{type(raw_route).__name__}"
            )
        route_by_kind = {str(k): str(v) for k, v in raw_route.items()}

        mode = str(d.get("mode", LOCAL_LLM_MODE_CLOUD_ALLOWED))
        if mode not in VALID_LOCAL_LLM_MODES:
            raise InvalidPolicy(
                f"local_llm.mode={mode!r} is not one of "
                f"{sorted(VALID_LOCAL_LLM_MODES)}"
            )

        on_insuf = str(d.get("on_insufficient",
                             LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD))
        if on_insuf not in VALID_LOCAL_LLM_ON_INSUFFICIENT:
            raise InvalidPolicy(
                f"local_llm.on_insufficient={on_insuf!r} is not one of "
                f"{sorted(VALID_LOCAL_LLM_ON_INSUFFICIENT)}"
            )

        return cls(
            route_by_kind=route_by_kind,
            mode=mode,
            on_insufficient=on_insuf,
        )


# ---------------------------------------------------------------------------
# Policy dataclass
# ---------------------------------------------------------------------------


@dataclass
class Acknowledgement:
    """One disable-acknowledgement record stored in the policy file."""

    accepted_at: str = ""       # ISO-8601 UTC
    accepted_by: str = ""       # actor identifier (user / agent id)
    disclaimer_version: str = ""
    reason: str = ""            # optional free-text reason

    @classmethod
    def from_dict(cls, d: dict) -> "Acknowledgement":
        return cls(
            accepted_at=str(d.get("accepted_at", "")),
            accepted_by=str(d.get("accepted_by", "")),
            disclaimer_version=str(d.get("disclaimer_version", "")),
            reason=str(d.get("reason", "")),
        )


# Three-state lock mode (privacy-by-default semantics):
#
#   "clean_room_with_algo"  ← DEFAULT. Privacy-by-default. Body never crosses
#                              to the cloud. Every triple + facet value goes
#                              through lock at ingest. PII findings drop
#                              the triple. Strongest protection.
#   "clean_room"            ← User reviews; algo doesn't. Body never crosses
#                              to the cloud. Triples flow as-extracted; no
#                              per-triple lock scrubbing. User decides
#                              what to send via HITL pre-flight.
#   "off"                   ← No guard. Body crosses to cloud alongside KG.
#                              For public folders where the user has affirmatively
#                              accepted the disclosure risk.

LOCK_MODE_CLEAN_ROOM_WITH_ALGO = "clean_room_with_algo"
LOCK_MODE_CLEAN_ROOM           = "clean_room"
LOCK_MODE_OFF                  = "off"
VALID_LOCK_MODES = frozenset({
    LOCK_MODE_CLEAN_ROOM_WITH_ALGO,
    LOCK_MODE_CLEAN_ROOM,
    LOCK_MODE_OFF,
})


@dataclass
class FolderPolicy:
    """The policy declaration for one folder.

    Defaults assume full protection. ``acknowledgements`` carries the
    user's opt-out records — without an acknowledgement for a given
    protection, that protection is treated as enabled regardless of
    the boolean (defense against silently-flipped policy files).

    The boolean ``privacy_lock_enabled`` is the legacy on/off field.
    The new ``lock_mode_explicit`` field, when set, overrides the
    boolean and selects one of the three states above. When unset, the
    ``lock_mode`` property derives a tri-state from the legacy field.
    """

    privacy_lock_enabled: bool = True
    oversight_enabled: bool = True
    oversight_default_level: str = "approve"
    lock_mode_explicit: str = ""        # "" | one of VALID_LOCK_MODES
    # Per-folder confidence floor for lock findings. lock_classify_text
    # filters its output to findings with confidence >= this value. Default
    # 0.0 means "emit all findings" (no filter). Range [0.0, 1.0].
    lock_confidence_threshold: float = 0.0
    # Discipline gate — code/text conformance for the folder (the third dial
    # beside lock + oversight). Unlike those, it defaults OFF: it is opt-in
    # because not every workspaced folder is a code/skill tree, and a gate with no
    # manifest is noise. When enabled, ``discipline_manifest`` names the rule
    # file (a path relative to the folder, or absolute); empty means the engine
    # falls back to its built-in default manifest.
    discipline_enabled: bool = False
    discipline_manifest: str = ""
    # Policy matrix (autonomy grade × oversight level → light). None = inherit:
    # the workspace uses the global/ancestor matrix unless it sets its own (override
    # cascade, exactly like the lock — global top-down, nearest setting wins).
    # Stored as {grade: {oversight: "go"|"ask"|"block"}}. policy_matrix.py owns
    # the default + resolution; this field just persists a workspace's own grid.
    policy_matrix: dict | None = None
    acknowledgements: dict[str, Acknowledgement] = field(default_factory=dict)
    # 0.6.8.2 — local LLM routing + air-gap mode + insufficient-handling.
    # Absent local-model block → defaults.
    # (cloud-allowed, escalate-to-cloud, empty route map).
    local_llm: "LocalLlmPolicy" = field(default_factory=lambda: LocalLlmPolicy())
    # TDM opt-out (Art. 4 DSM shape): when True, egress whose task_scope
    # names AI-training/bulk-corpus use is refused for this folder. Default
    # False = not asserted (the mechanism ships first; a per-folder-class
    # default is a controller decision, not a runtime one).
    ai_training_optout: bool = False
    # Jurisdiction-pack stack (§ 4.5): pack names (shipped reference packs)
    # or file paths this folder declares. Ancestors' stacks cascade down and
    # compose strictest-wins (juris_packs.py owns validation + resolution);
    # this field just persists the folder's OWN declaration.
    juris_packs: list[str] = field(default_factory=list)
    # Cost cap: an optional spend ceiling in cents for this folder. None
    # = no cap (opt-in, like discipline). When set, the `operate` gate refuses
    # a run once the folder's recorded spend reaches
    # the cap — enforcement, not just the readable spend. A present-but-invalid
    # value (non-numeric or negative) is a policy error, surfaced as
    # InvalidPolicy at load (same discipline as the local_llm block) rather than
    # silently disabling the cap.
    cost_cap_cents: float | None = None
    # Tier-M moderation rules: a detective layer that runs beside the privacy
    # tiers on egress text. None/absent = no moderation (opt-in, like cost_cap).
    # Shape: {"banned_terms": [str], "banned_patterns": [regex str],
    #         "categories": [str], "backend": "<spec>"}. Deterministic terms/
    #         patterns always enforce; categories REQUIRE a classifier backend and
    #         fail closed when it is unavailable. tier_m.py owns validation at
    #         decision time; here it is just persisted (present-but-not-an-object
    #         is a policy error, surfaced at load like cost_cap).
    moderation_rules: dict | None = None
    # M5/A6: opt-in access control on governed reads. Default False = local-first
    # (the operator owns the folder; reads are open). When True, reads by a named
    # party are gated fail-closed against the party register (see authorization.py).
    access_control_enabled: bool = False

    @property
    def lock_mode(self) -> str:
        """Resolve the tri-state lock mode.

        Resolution order:
        1. If ``lock_mode_explicit`` is set to a valid value, use it.
        2. Otherwise derive from ``lock_is_active`` (legacy boolean):
             active   → "clean_room_with_algo"   (privacy by default)
             disabled → "off"
        """
        if self.lock_mode_explicit in VALID_LOCK_MODES:
            return self.lock_mode_explicit
        return (LOCK_MODE_CLEAN_ROOM_WITH_ALGO
                if self.lock_is_active else LOCK_MODE_OFF)

    @property
    def lock_is_active(self) -> bool:
        """True if Privacy Lock should run for this folder.

        Active iff ``privacy_lock_enabled`` is True OR no acknowledgement
        for an opt-out exists. Two acknowledgement keys count as opt-outs:

        - ``lock_disable`` (legacy binary toggle)
        - ``lock_mode_change_to_off`` (tri-state step-down to "off")

        Belt-and-braces: an attacker can flip the boolean by editing the file,
        but they can't manufacture an acknowledgement with a real timestamp
        without leaving a forensic trace in the audit log.

        Kept for back-compat with callers that pre-date ``lock_mode``.
        """
        if self.privacy_lock_enabled:
            return True
        if "lock_disable" in self.acknowledgements:
            return False
        if "lock_mode_change_to_off" in self.acknowledgements:
            return False
        return True

    @property
    def oversight_is_active(self) -> bool:
        """True if Oversight prompts should fire for this folder."""
        if self.oversight_enabled:
            return True
        return "oversight_disable" not in self.acknowledgements

    @property
    def discipline_is_active(self) -> bool:
        """True if the discipline gate should run for this folder.

        Opt-in (default off), so this is a plain boolean — no acknowledgement
        dance. Disabling a quality gate is not lowering a protection the way
        disabling lock/oversight is, so it carries no disclaimer.
        """
        return self.discipline_enabled

    def to_dict(self) -> dict[str, Any]:
        out = {
            "privacy_lock_enabled": self.privacy_lock_enabled,
            "oversight_enabled": self.oversight_enabled,
            "oversight_default_level": self.oversight_default_level,
            "acknowledgements": {
                k: asdict(v) for k, v in self.acknowledgements.items()
            },
        }
        if self.lock_mode_explicit:
            out["lock_mode_explicit"] = self.lock_mode_explicit
        # Only emit threshold when non-default so policy files stay clean.
        if self.lock_confidence_threshold > 0.0:
            out["lock_confidence_threshold"] = self.lock_confidence_threshold
        # Discipline: emit only when enabled or a manifest is named, so legacy
        # policy files don't grow keys for a dial they never set.
        if self.discipline_enabled:
            out["discipline_enabled"] = True
        if self.discipline_manifest:
            out["discipline_manifest"] = self.discipline_manifest
        # TDM opt-out: emit only when asserted, so legacy files stay clean.
        if self.ai_training_optout:
            out["ai_training_optout"] = True
        # Pack stack: emit only when this folder declares one.
        if self.juris_packs:
            out["juris_packs"] = list(self.juris_packs)
        # Cost cap: emit only when set, so legacy files stay clean.
        if self.cost_cap_cents is not None:
            out["cost_cap_cents"] = self.cost_cap_cents
        # Moderation rules (Tier M): emit only when set, so legacy files stay clean.
        if self.moderation_rules:
            out["moderation_rules"] = self.moderation_rules
        # Access control (M5/A6): emit only when opted in.
        if self.access_control_enabled:
            out["access_control_enabled"] = True
        # Emit the matrix only when this workspace has its OWN (override). None =
        # inherit, so legacy/most workspaces carry no key for it.
        if self.policy_matrix is not None:
            out["policy_matrix"] = self.policy_matrix
        # Only emit the local_llm block when it diverges from defaults so
        # legacy policy files don't grow new keys for no reason. Defaults:
        # empty route map, "cloud-allowed" mode, "escalate-to-cloud".
        ll = self.local_llm
        if (ll.route_by_kind
                or ll.mode != LOCAL_LLM_MODE_CLOUD_ALLOWED
                or ll.on_insufficient != LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD):
            out["local_llm"] = ll.to_dict()
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "FolderPolicy":
        acks_raw = d.get("acknowledgements") or {}
        acks = {
            k: Acknowledgement.from_dict(v)
            for k, v in acks_raw.items()
            if isinstance(v, dict)
        }
        # Defensive parse of the threshold: clamp to [0.0, 1.0], default 0.0.
        try:
            thr = float(d.get("lock_confidence_threshold", 0.0))
        except (TypeError, ValueError):
            thr = 0.0
        thr = max(0.0, min(1.0, thr))
        # local_llm block: missing → defaults; malformed → propagate
        # InvalidPolicy so the controller learns at write time.
        local_llm = LocalLlmPolicy.from_dict(d.get("local_llm"))
        # Cost cap: absent/None → no cap. Present-but-invalid (non-numeric or
        # negative) is a policy error, surfaced now rather than silently
        # dropping the cap (which would fail OPEN on a budget control).
        cost_cap = d.get("cost_cap_cents")
        if cost_cap is None:
            cost_cap_cents = None
        else:
            # bool is an int subclass; True/False as a budget is a mistake.
            if isinstance(cost_cap, bool):
                raise InvalidPolicy(
                    f"cost_cap_cents must be a number, got {cost_cap!r}")
            try:
                cost_cap_cents = float(cost_cap)
            except (TypeError, ValueError):
                raise InvalidPolicy(
                    f"cost_cap_cents must be a number, got {cost_cap!r}")
            # NaN/inf parse fine but would make `spend >= cap` always False —
            # i.e. silently disable the budget. Reject them (fail-safe).
            if not math.isfinite(cost_cap_cents):
                raise InvalidPolicy(
                    f"cost_cap_cents must be finite, got {cost_cap_cents}")
            if cost_cap_cents < 0:
                raise InvalidPolicy(
                    f"cost_cap_cents must be >= 0, got {cost_cap_cents}")
        # Moderation rules: absent → None (no moderation). Present-but-not-an-object
        # is a policy error surfaced now (fail-safe), rather than silently dropping a
        # moderation control the controller intended. Tier M validates the inner
        # shape at decision time and fails closed on rules it cannot enforce.
        mod = d.get("moderation_rules")
        if mod is not None and not isinstance(mod, dict):
            raise InvalidPolicy(
                f"moderation_rules must be an object, got {type(mod).__name__}")
        return cls(
            privacy_lock_enabled=bool(d.get("privacy_lock_enabled", True)),
            oversight_enabled=bool(d.get("oversight_enabled", True)),
            oversight_default_level=str(d.get("oversight_default_level", "approve")),
            lock_mode_explicit=str(d.get("lock_mode_explicit", "")),
            lock_confidence_threshold=thr,
            discipline_enabled=bool(d.get("discipline_enabled", False)),
            discipline_manifest=str(d.get("discipline_manifest", "")),
            ai_training_optout=bool(d.get("ai_training_optout", False)),
            juris_packs=[str(x) for x in (d.get("juris_packs") or [])
                         if isinstance(x, str) and x],
            policy_matrix=(d.get("policy_matrix")
                           if isinstance(d.get("policy_matrix"), dict) else None),
            acknowledgements=acks,
            local_llm=local_llm,
            cost_cap_cents=cost_cap_cents,
            moderation_rules=mod,
            access_control_enabled=bool(d.get("access_control_enabled", False)),
        )

    @classmethod
    def default(cls) -> "FolderPolicy":
        return cls()


# ---------------------------------------------------------------------------
# Load + save
# ---------------------------------------------------------------------------


def policy_path(folder_path: str | Path) -> Path:
    """Where the canonical policy file lives for a folder.

    Always returns the path for the modern filename (:data:`POLICY_FILENAME`).
    The legacy filename (:data:`LEGACY_POLICY_FILENAME`) is read by
    :func:`load_policy` if present, but ``policy_path`` does not surface it —
    callers that need both should check
    ``folder / POLICY_FILENAME`` first, then ``folder / LEGACY_POLICY_FILENAME``.
    """
    return Path(folder_path).expanduser().resolve() / POLICY_FILENAME


def load_policy(folder_path: str | Path) -> FolderPolicy:
    """Read the folder's policy. Returns the default policy if no file exists.

    Tries :data:`POLICY_FILENAME` first, then falls back to the legacy
    :data:`LEGACY_POLICY_FILENAME` for back-compat with installs that predate
    the namespace rename. The next ``save_policy`` will write the new name,
    silently superseding the legacy file (but not deleting it — manual cleanup
    is honest, automatic deletion is not).

    Malformed policy files (unreadable / not valid JSON / not a dict) are
    treated as the default policy (fail-safe to full protection rather than
    silently dropping protection on a corrupt file).

    Files with structurally valid JSON but semantically invalid values
    (e.g. ``local_llm.mode`` outside the documented set) raise
    :class:`InvalidPolicy` so the controller learns at parse time — silent
    fall-through on a bad ``mode`` would let the user believe air-gap is on
    when it isn't.
    """
    folder = Path(folder_path).expanduser().resolve()
    p = folder / POLICY_FILENAME
    if not p.exists():
        legacy = folder / LEGACY_POLICY_FILENAME
        if legacy.exists():
            p = legacy
        else:
            return FolderPolicy.default()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return FolderPolicy.default()
    if not isinstance(data, dict):
        return FolderPolicy.default()
    return FolderPolicy.from_dict(data)


def verified_cost_cap(folder_path: str | Path) -> tuple[float | None, bool]:
    """Resolve the cost cap for ENFORCEMENT, returning ``(cap_cents, verifiable)``.

    A budget control must fail CLOSED when it cannot trust its own source of
    truth, so this deliberately does NOT reuse :func:`load_policy` (which
    fail-safes a corrupt file to the default — i.e. *no* cap, the wrong
    direction for a budget). Instead it reads only the ``cost_cap_cents`` field:

      * no policy file at all                       → ``(None, True)``  — no cap declared
      * file present but unreadable / invalid JSON  → ``(None, False)`` — fail CLOSED
      * file present, not a dict / bad cap value    → ``(None, False)`` — fail CLOSED
      * NaN / inf / negative cap                    → ``(None, False)`` — fail CLOSED
      * valid finite cap                            → ``(cap, True)``
      * valid file, no cap key                      → ``(None, True)``  — no cap declared

    Reading only the cap field also scopes the failure: a malformed *unrelated*
    field (e.g. ``local_llm``) does not make ``operate`` refuse — only a problem
    with the cap or the file itself does.
    """
    folder = Path(folder_path).expanduser().resolve()
    p = folder / POLICY_FILENAME
    if not p.exists():
        legacy = folder / LEGACY_POLICY_FILENAME
        if not legacy.exists():
            return (None, True)          # no policy → no cap declared
        p = legacy
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # UnicodeDecodeError (a ValueError, not an OSError) fires on a non-UTF-8
        # / binary policy file — present but untrustworthy → fail closed.
        return (None, False)             # present but unreadable → fail closed
    if not isinstance(data, dict):
        return (None, False)
    raw = data.get("cost_cap_cents")
    if raw is None:
        return (None, True)              # valid file, no cap declared
    if isinstance(raw, bool):
        return (None, False)
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        return (None, False)             # bad cap value → fail closed
    if not math.isfinite(cap) or cap < 0:
        return (None, False)             # NaN/inf/negative → fail closed
    return (cap, True)


def is_air_gapped(folder_path: str | Path) -> bool:
    """Resolve, for ENFORCEMENT, whether ``folder`` forbids all cloud egress.

    An air-gap commitment (``local_llm.mode == "local-only"``, D22) is a privacy
    control, so — exactly like :func:`verified_cost_cap` — it must fail CLOSED
    when it cannot trust its own source of truth. It therefore does NOT reuse
    :func:`load_policy` (which fail-safes a corrupt file to the *default*,
    ``cloud-allowed`` — the wrong, leaky direction here). It reads only the
    ``local_llm.mode`` field, so a malformed *unrelated* field does not strand a
    folder offline:

      * no policy file at all                       → ``False`` — air-gap not declared
      * file present but unreadable / invalid JSON  → ``True``  — fail CLOSED (no cloud)
      * file present, not a dict                    → ``True``  — fail CLOSED
      * ``local_llm`` present but not a dict         → ``True``  — fail CLOSED
      * ``mode`` is an unrecognised token            → ``True``  — fail CLOSED
      * ``mode == "local-only"``                     → ``True``  — air-gapped
      * ``mode`` valid + not local-only / absent     → ``False`` — cloud egress legal

    Returning ``True`` means "drop every cloud rung and refuse cloud egress".
    """
    folder = Path(folder_path).expanduser().resolve()
    p = folder / POLICY_FILENAME
    if not p.exists():
        legacy = folder / LEGACY_POLICY_FILENAME
        if not legacy.exists():
            return False                 # no policy → air-gap not declared
        p = legacy
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # UnicodeDecodeError (a ValueError, not an OSError) fires on a non-UTF-8
        # / binary policy file — still "present but untrustworthy" → fail closed.
        return True                      # present but unreadable → fail closed
    if not isinstance(data, dict):
        return True                      # corrupt shape → fail closed
    block = data.get("local_llm")
    if block is None:
        return False                     # valid file, no local_llm → default cloud-allowed
    if not isinstance(block, dict):
        return True                      # malformed local_llm → can't verify → fail closed
    mode = block.get("mode", LOCAL_LLM_MODE_CLOUD_ALLOWED)
    if mode == LOCAL_LLM_MODE_LOCAL_ONLY:
        return True
    if mode not in VALID_LOCAL_LLM_MODES:
        return True                      # unrecognised mode → fail closed (don't trust it)
    return False                         # cloud-allowed / cloud-fallback → cloud egress legal


def save_policy(folder_path: str | Path, policy: FolderPolicy) -> None:
    """Write the policy file atomically.

    Writes to a temp file in the same directory then renames, so a partial
    write doesn't leave a corrupt policy file (which would fail-safe to the
    default anyway, but still — no point creating noise).
    """
    p = policy_path(folder_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(policy.to_dict(), indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Disable / enable + audit-log integration
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def disable_lock(
    folder_path: str | Path,
    *,
    accepted_by: str,
    reason: str = "",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Disable Privacy Lock for this folder.

    Caller is responsible for confirming the user has read the disclaimer
    (e.g. by checking the ``--i-accept-the-risk`` CLI flag). This function
    does NOT show the disclaimer — it records the acceptance + writes the
    policy + emits an audit-log event.

    Raises ``ValueError`` if ``accepted_by`` is empty (refuse silent disables).
    """
    if not accepted_by:
        raise ValueError("accepted_by is required (refuse silent disables)")

    policy = load_policy(folder_path)
    policy.privacy_lock_enabled = False
    policy.acknowledgements["lock_disable"] = Acknowledgement(
        accepted_at=_now_iso(),
        accepted_by=accepted_by,
        disclaimer_version=CURRENT_DISCLAIMER_VERSION,
        reason=reason,
    )
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=accepted_by,
        extra={
            "policy_change": "lock_disabled",
            "disclaimer_version": CURRENT_DISCLAIMER_VERSION,
            "reason": reason,
        },
    ))
    return policy


def enable_lock(
    folder_path: str | Path,
    *,
    actor: str = "user",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Re-enable Privacy Lock for this folder. No disclaimer required —
    enabling protection is the safer direction."""
    policy = load_policy(folder_path)
    policy.privacy_lock_enabled = True
    policy.acknowledgements.pop("lock_disable", None)
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=actor,
        extra={"policy_change": "lock_enabled"},
    ))
    return policy


def disable_oversight(
    folder_path: str | Path,
    *,
    accepted_by: str,
    reason: str = "",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Disable Oversight prompts for this folder. Requires acknowledgement."""
    if not accepted_by:
        raise ValueError("accepted_by is required (refuse silent disables)")

    policy = load_policy(folder_path)
    policy.oversight_enabled = False
    policy.acknowledgements["oversight_disable"] = Acknowledgement(
        accepted_at=_now_iso(),
        accepted_by=accepted_by,
        disclaimer_version=CURRENT_DISCLAIMER_VERSION,
        reason=reason,
    )
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=accepted_by,
        extra={
            "policy_change": "oversight_disabled",
            "disclaimer_version": CURRENT_DISCLAIMER_VERSION,
            "reason": reason,
        },
    ))
    return policy


def set_lock_mode(
    folder_path: str | Path,
    mode: str,
    *,
    accepted_by: str = "",
    reason: str = "",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Explicitly set the tri-state lock mode for a folder.

    Args:
        folder_path: folder to update.
        mode: one of "clean_room_with_algo" / "clean_room" / "off".
        accepted_by: required when transitioning to a less-protective mode
            (i.e., away from "clean_room_with_algo"). Records who took
            responsibility for the change.
        reason: explanation persisted in the acknowledgement.
        log_root: optional override of mutation log location.

    Raises:
        ValueError on unknown mode, or on transitions to less-protective
        modes without ``accepted_by``.

    Returns the updated policy.
    """
    if mode not in VALID_LOCK_MODES:
        raise ValueError(f"unknown lock mode: {mode!r}; "
                         f"valid: {sorted(VALID_LOCK_MODES)}")
    policy = load_policy(folder_path)
    current = policy.lock_mode

    # Strictness order: clean_room_with_algo > clean_room > off
    strictness_rank = {
        LOCK_MODE_CLEAN_ROOM_WITH_ALGO: 3,
        LOCK_MODE_CLEAN_ROOM:           2,
        LOCK_MODE_OFF:                  1,
    }
    if strictness_rank[mode] < strictness_rank[current]:
        if not accepted_by:
            raise ValueError(
                f"reducing lock protection from {current!r} to {mode!r} "
                f"requires accepted_by (refuse silent step-downs)"
            )
        # Record the acknowledgement
        policy.acknowledgements[f"lock_mode_change_to_{mode}"] = Acknowledgement(
            accepted_at=_now_iso(),
            accepted_by=accepted_by,
            disclaimer_version=CURRENT_DISCLAIMER_VERSION,
            reason=reason,
        )
    # Also keep legacy boolean coherent: if mode == "off", legacy boolean
    # becomes False; otherwise True.
    policy.lock_mode_explicit = mode
    policy.privacy_lock_enabled = (mode != LOCK_MODE_OFF)
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=accepted_by or "system",
        extra={
            "policy_change": "lock_mode_set",
            "from_mode": current,
            "to_mode":   mode,
            "disclaimer_version": CURRENT_DISCLAIMER_VERSION,
            "reason": reason,
        },
    ))
    return policy


def enable_oversight(
    folder_path: str | Path,
    *,
    actor: str = "user",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Re-enable Oversight prompts for this folder."""
    policy = load_policy(folder_path)
    policy.oversight_enabled = True
    policy.acknowledgements.pop("oversight_disable", None)
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=actor,
        extra={"policy_change": "oversight_enabled"},
    ))
    return policy


OVERSIGHT_LEVELS = ("autonomous", "notify", "review", "approve",
                    "supervised", "manual")


def set_oversight_level(
    folder_path: str | Path,
    level: str,
    *,
    actor: str = "user",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Set the folder's default oversight level (the dial position).

    ``level`` is one of :data:`OVERSIGHT_LEVELS` (autonomous .. manual). Raising
    the dial (more human involvement) needs no disclaimer; this only moves the
    default position — it does NOT disable prompts (that is ``disable_oversight``,
    which carries the disclaimer). Raises ``ValueError`` on an unknown level.
    """
    lv = (level or "").strip().lower()
    if lv not in OVERSIGHT_LEVELS:
        raise ValueError(
            f"unknown oversight level {level!r}; choose one of "
            f"{', '.join(OVERSIGHT_LEVELS)}")
    policy = load_policy(folder_path)
    previous = policy.oversight_default_level
    policy.oversight_default_level = lv
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=actor,
        extra={"policy_change": "oversight_level_set",
               "from": previous, "to": lv},
    ))
    return policy


def set_access_control(
    folder_path: str | Path,
    enabled: bool,
    *,
    actor: str = "user",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Opt the folder in/out of per-workspace access control (#58).

    When ON, governed writes that consult it (e.g. contract sign-off) require a
    registered, authorised party and fail closed otherwise; OFF (default) keeps
    the local-first free-text path. Enabling is a TIGHTENING, so it needs no
    disclaimer — but the flip is recorded on the chain either way.
    """
    policy = load_policy(folder_path)
    previous = bool(policy.access_control_enabled)
    policy.access_control_enabled = bool(enabled)
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=actor,
        extra={"policy_change": "access_control_set",
               "from": previous, "to": bool(enabled)},
    ))
    return policy


def set_ai_training_optout(
    folder_path: str | Path,
    enabled: bool,
    *,
    actor: str = "user",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Assert (or withdraw) the folder's TDM / AI-training opt-out.

    Asserting raises a protection — no disclaimer needed. Withdrawing
    lowers one; it is still permitted without a disclaimer because the
    default state of the field is not-asserted, but the change is audited
    either way so the assertion history is on the chain.
    """
    policy = load_policy(folder_path)
    policy.ai_training_optout = bool(enabled)
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=actor,
        extra={"policy_change": "ai_training_optout",
               "enabled": bool(enabled)},
    ))
    return policy


TDM_DECLARATION_FILENAME = "ai-training.txt"


def resolve_ai_training_optout(folder_path: str | Path) -> bool:
    """True when this folder OR any ancestor asserts the TDM opt-out.

    The assertion cascades downward (strictest-wins — the § 1.5
    monotonicity rule): a catalogue folder's reservation covers every
    release and track folder beneath it. Withdrawal happens at the
    asserting level; a sub-folder cannot silently un-reserve what its
    ancestor reserved. A malformed ancestor policy propagates loudly —
    the egress gate must not guess past a broken policy file.
    """
    p = Path(folder_path).expanduser().resolve()
    for _ in range(64):  # bounded walk; no filesystem is this deep
        if load_policy(p).ai_training_optout:
            return True
        if p.parent == p:
            break
        p = p.parent
    return False


def tdm_declare(
    folder_path: str | Path,
    *,
    actor: str = "user",
    log_root: str | Path | None = None,
) -> dict:
    """Write the folder's machine-readable TDM reservation file and assert
    the opt-out (declaring IS asserting). Returns
    ``{ok, declaration, asserted_now}``. The file makes the reservation
    externally visible (Art. 4(3) DSM shape); the chain records the
    assertion. Wording is a reservation statement, not a compliance claim —
    the legal position is the rights-holder's call.
    """
    policy = load_policy(folder_path)
    asserted_now = False
    if not policy.ai_training_optout:
        policy.ai_training_optout = True
        save_policy(folder_path, policy)
        asserted_now = True

    folder = Path(folder_path).expanduser().resolve()
    decl = folder / TDM_DECLARATION_FILENAME
    now = _now_iso()
    decl.write_text(
        "# AI training reservation (TDM opt-out)\n"
        "status: reserved\n"
        "basis: Art. 4(3) Directive (EU) 2019/790 (DSM); Sec. 44b UrhG\n"
        # Scope wording matches enforcement: resolve_ai_training_optout
        # walks ancestors, so the assertion covers the whole subtree.
        "scope: all content in this folder and its sub-folders\n"
        f"declared-at: {now}\n"
        f"declared-by: {actor}\n",
        encoding="utf-8",
    )

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(folder),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=actor,
        extra={"policy_change": "tdm_declaration",
               "file": TDM_DECLARATION_FILENAME,
               "asserted_now": asserted_now},
    ))
    return {"ok": True, "declaration": str(decl),
            "asserted_now": asserted_now}


def enable_discipline(
    folder_path: str | Path,
    *,
    manifest: str = "",
    actor: str = "user",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Turn the discipline gate ON for this folder (draw the third dial).

    ``manifest`` optionally names the rule file (relative to the folder, or
    absolute). Empty leaves the engine on its built-in default manifest. No
    disclaimer required — enabling a quality gate raises rigour, it does not
    lower a protection.
    """
    policy = load_policy(folder_path)
    policy.discipline_enabled = True
    if manifest:
        policy.discipline_manifest = manifest
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=actor,
        extra={"policy_change": "discipline_enabled", "manifest": manifest},
    ))
    return policy


def disable_discipline(
    folder_path: str | Path,
    *,
    actor: str = "user",
    log_root: str | Path | None = None,
) -> FolderPolicy:
    """Turn the discipline gate OFF for this folder. No disclaimer — a quality
    gate is not a protection, so disabling it carries no acknowledgement."""
    policy = load_policy(folder_path)
    policy.discipline_enabled = False
    save_policy(folder_path, policy)

    log = MutationLog(folder_path, log_root=log_root)
    log.append(LogEvent(
        event="system",
        folder_path=str(Path(folder_path).expanduser().resolve()),
        pair_id="policy-event",
        lifecycle_state="",
        channel="system",
        actor=actor,
        extra={"policy_change": "discipline_disabled"},
    ))
    return policy
