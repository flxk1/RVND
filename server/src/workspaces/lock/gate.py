# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""``gate_for_cloud()`` — bundles vault-load + lock_text + decisions-lookup.

This is the single entry point a doc-extractor (or any other Workspace
plugin) calls before a Tier-3 cloud-LLM fallback. It composes the three
pieces shipped earlier:

1. ``kg_context_for_vault(vault_path)`` — fetch the user's confidential terms.
2. ``lock_text(text, context=...)`` — run regex + local-model checks.
3. ``DecisionsStore`` — short-circuit if the user previously approved/blocked
   this exact text pattern.

The returned ``GateDecision`` adds one action over ``TextDecision``:
``ask_user``. When the lock says refuse and oversight is high enough, the
orchestrator surfaces the item to the user; the user's response can be
remembered via ``decisions.remember()`` so the next occurrence doesn't prompt
again.

This is the function the ``privacy-lock-orchestrator`` SKILL invokes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .core import AuditLog, Finding, Mode, TextDecision, lock_text
from .decisions import DecisionsStore
from .l0_bridge import try_load_policy
from .obsidian_kg import kg_context_for_vault
from .oversight import OversightLevel


@dataclass
class GateDecision:
    """Outcome of a gate_for_cloud() call.

    Actions:
        - "allow"     : text may pass to the cloud LLM unchanged.
        - "minimise"  : send ``redacted_text`` instead of the original.
        - "refuse"    : do not send. Mode/oversight too strict to override.
        - "ask_user"  : refuse pending user decision. Orchestrator must surface
                        the preview + reasons to the user and route their
                        decision back via decisions.remember().

    ``recalled_from_decisions`` indicates the short-circuit path was taken
    (a prior user decision matched). When True, ``findings`` may be empty.
    """

    action: str
    findings: list[Finding] = field(default_factory=list)
    redacted_text: str | None = None
    reason: str = ""
    source: str = "document"
    recalled_from_decisions: bool = False
    pattern_preview: str = ""    # for the orchestrator to show the user
    lock_bypassed: bool = False  # CL2: Privacy Lock off let a would-be refuse/minimise through
    would_have: str = ""         # the action the gate WOULD have taken ("refuse"/"minimise")


def gate_for_cloud(
    text: str,
    *,
    vault_path: str | Path | None = None,
    oversight: OversightLevel = OversightLevel.APPROVE,
    mode: Mode = Mode.STANDARD,
    decisions: DecisionsStore | None = None,
    audit: AuditLog | None = None,
    source: str = "document",
    task_id: str | None = None,
    folder_context: str | Path | None = None,
) -> GateDecision:
    """Run the full minimisation gate before a cloud-LLM call.

    Workflow:

    1. If ``decisions`` is provided, recall any prior user decision for this
       exact text pattern. If recalled allow → return allow immediately. If
       recalled block → return refuse immediately. Either way, the lock
       checks are skipped (the user's stored decision wins).
    2. If ``vault_path`` is provided, load confidential terms from the vault.
    3. Call ``lock_text(text, context=...)``.
    4. Translate the TextDecision into a GateDecision, honouring oversight:
       - allow / minimise → pass through.
       - refuse + oversight ∈ {APPROVE, SUPERVISED, MANUAL} → ``ask_user``.
       - refuse + oversight ∈ {AUTONOMOUS, NOTIFY, REVIEW} → ``refuse``.

    Never raises — best-effort. On any internal error, default to refuse.

    Args:
        text: content about to leave the local boundary.
        vault_path: optional Obsidian vault path for confidential-context.
        oversight: six-level dial. APPROVE+ allows interactive override.
        mode: lock_text mode (STANDARD/STRICT/PERMISSIVE/AUDIT_ONLY).
        decisions: optional persisted-decisions store for short-circuit.
        audit: optional audit log.
        source: tag for audit (e.g. "document", "triple", "freeform").
        task_id: optional task identifier for audit correlation.
    """
    pattern_preview = _short_preview(text)

    # 0. Folder-policy: Privacy Lock disabled for this folder
    #    (`workspace-l0 policy disable-lock --i-accept-the-risk`). Lock-off disables
    #    ENFORCEMENT, NOT DETECTION (CL2): the gate previously returned `allow`
    #    immediately — before any check, with no audit — so policy alone turned a
    #    would-be refuse into a silent auto-allow. Now detection still runs; a
    #    would-be refuse routes to a person (ask_user at APPROVE+), every bypass
    #    is audited, and only a clean text passes silently.
    # One policy read serves both the lock-off check and Tier-M moderation rules.
    snapshot = try_load_policy(folder_context) if folder_context is not None else None
    lock_off = (snapshot is not None and not snapshot.lock_is_active)

    # 1. Recall any prior user decision. (Skipped under lock-off — recall is an
    #    enforcement short-circuit; lock-off re-detects every time.)
    if not lock_off and decisions is not None:
        recalled = decisions.recall(text)
        if recalled == "allow":
            return GateDecision(
                action="allow",
                reason="user previously approved this pattern",
                source=source,
                recalled_from_decisions=True,
                pattern_preview=pattern_preview,
            )
        if recalled == "block":
            return GateDecision(
                action="refuse",
                reason="user previously blocked this pattern",
                source=source,
                recalled_from_decisions=True,
                pattern_preview=pattern_preview,
            )

    # 2. Load confidential context from the vault if requested.
    context = ""
    if vault_path is not None:
        try:
            context = kg_context_for_vault(vault_path)
        except (FileNotFoundError, NotADirectoryError, OSError):
            # If the vault is unreachable, fall through with no context.
            # The lock still runs Tier B + the PII half of Tier C.
            context = ""

    # 3. Run lock_text. Tier-M moderation rules ride on the policy snapshot loaded
    #    above (None when no folder or none declared → Tier M is a no-op). Detection
    #    runs even under lock-off (CL2): a would-be moderation refuse still routes to
    #    a person.
    moderation_rules = snapshot.moderation_rules if snapshot is not None else None
    try:
        text_decision = lock_text(
            text,
            context=context,
            mode=mode,
            audit=audit,
            source=source,
            task_id=task_id,
            moderation_rules=moderation_rules,
        )
    except Exception as e:
        # Fail-closed: if lock breaks, refuse.
        return GateDecision(
            action="refuse",
            reason=f"lock_text raised unexpectedly: {e}",
            source=source,
            pattern_preview=pattern_preview,
        )

    # 4. Translate to gate action, honouring oversight on refuse.
    if lock_off:
        return _from_lockoff_decision(
            text_decision, oversight, pattern_preview,
            audit=audit, source=source, task_id=task_id,
            folder_context=folder_context)
    return _from_text_decision(text_decision, oversight, pattern_preview)


def _from_lockoff_decision(
    td: TextDecision,
    oversight: OversightLevel,
    pattern_preview: str,
    *,
    audit: AuditLog | None,
    source: str,
    task_id: str | None,
    folder_context,
) -> GateDecision:
    """Privacy Lock is OFF for this folder. Detection still ran (``td``). A clean
    result passes; a would-be refuse/minimise is a BYPASS — always audited, and a
    would-be REFUSE routes to a person at APPROVE+ rather than auto-allowing."""
    if td.action == "allow":
        return GateDecision(
            action="allow", findings=td.findings,
            reason="lock disabled per folder policy; nothing flagged",
            source=source, pattern_preview=pattern_preview)

    # td.action is "refuse" or "minimise" → lock-off lets it through more
    # permissively than the gate wanted. Decide the final action FIRST so the
    # audit records what actually happened (ask_user vs allow) and the oversight
    # that drove it.
    final_action = "ask_user" if (td.action == "refuse" and oversight in (
        OversightLevel.APPROVE, OversightLevel.SUPERVISED, OversightLevel.MANUAL,
    )) else "allow"

    # Record the bypass — FAIL-CLOSED: if an audit sink is configured and the
    # write fails, do NOT let the bypass through unrecorded. Off disables
    # enforcement, never the audit trail (CL2).
    if audit is not None:
        try:
            audit.write_bypass(td.action, td, oversight=oversight,
                               final_action=final_action, source=source,
                               reason=f"lock disabled (folder={folder_context})",
                               task_id=task_id)
        except Exception:
            return GateDecision(
                action="refuse", findings=td.findings,
                reason="lock-off bypass could not be audited — refused (fail-closed)",
                source=source, pattern_preview=pattern_preview)

    if final_action == "ask_user":
        return GateDecision(
            action="ask_user", findings=td.findings,
            reason="lock disabled, but this would have been refused — a person must decide",
            source=source, pattern_preview=pattern_preview,
            lock_bypassed=True, would_have="refuse")

    # Lower oversight, or a would-be minimise: the bypass passes (lock-off = no
    # enforcement), but it is audited — never silent.
    return GateDecision(
        action="allow", findings=td.findings,
        reason=f"lock disabled per folder policy; would-be {td.action} bypassed (audited)",
        source=source, pattern_preview=pattern_preview,
        lock_bypassed=True, would_have=td.action)


def _from_text_decision(
    td: TextDecision,
    oversight: OversightLevel,
    pattern_preview: str,
) -> GateDecision:
    if td.action == "allow":
        return GateDecision(
            action="allow",
            findings=td.findings,
            reason=td.reason,
            source=td.source,
            pattern_preview=pattern_preview,
        )
    if td.action == "minimise":
        return GateDecision(
            action="minimise",
            findings=td.findings,
            redacted_text=td.redacted_text,
            reason=td.reason,
            source=td.source,
            pattern_preview=pattern_preview,
        )

    # td.action == "refuse"
    # If oversight allows interactive override, surface to the user.
    if oversight in (
        OversightLevel.APPROVE,
        OversightLevel.SUPERVISED,
        OversightLevel.MANUAL,
    ):
        return GateDecision(
            action="ask_user",
            findings=td.findings,
            reason=td.reason,
            source=td.source,
            pattern_preview=pattern_preview,
        )

    # Lower oversight = no interactive override.
    return GateDecision(
        action="refuse",
        findings=td.findings,
        reason=td.reason,
        source=td.source,
        pattern_preview=pattern_preview,
    )


def _short_preview(text: str, n: int = 80) -> str:
    """A short, REDACTED preview shown to the user — secrets/PII are stripped
    before truncation so the flagged content itself never rides along on the
    GateDecision (e.g. into an ask_user prompt). Fail-closed: if redaction is
    unavailable, return nothing rather than raw text."""
    if not text:
        return ""
    try:
        from .core import redact_for_capture
        return " ".join(redact_for_capture(text).split())[:n]
    except Exception:
        return ""
