# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND adapter seam over loomground-norm — the general normative-reasoning plane.

The workspaces boundary rule confines every direct import of an upstream
Loomground package to the ``adapters/`` seam (see
``tests/test_adapter_boundary.py``). This module is that seam for the
**norm-runtime** plane: rule extraction, obligation state, subsumption, the
span-norm registry, and the obligation scheduler.

loomground-norm is now domain-neutral. Anchoring (placing a norm onto the
instruments / jurisdictions / regulators that govern it) is a LEGAL-domain step
that moved OUT of norm into loomground-legal; RVND consumes it through the legal
seam (``adapters.legal``), never from norm. What norm still owns and this seam
re-exports: rule extraction, the deontic incident layer, obligation runtime,
subsumption (universal layer), the neutral span-norm ``RuleRegistry`` and the
UNGATED ``ObligationScheduler``.

This module does two things:

1. **re-exports** loomground-norm's pure public surface (``RuleFacet``,
   ``Obligation``, ``Subsumption``, ``SpanNorm``, …) so ``rvnd.rule_extractor``
   and its sibling twins consume the plane through here; and

2. **wires RVND's providers into the plane's ports** and **regains the
   product-concretes norm no longer carries** — the legal-domain anchoring (via
   ``adapters.legal``), the per-user ``~/.workspace`` mirror, the ``legal-corpus``
   subdir, the signed-mutation-log audit mapping, RVND's action-gate over the
   plane's now-ungated ``FollowUp``, and the regional (legal-family) layer of
   subsumption validation on top of norm's universal layer.

Two deliberate translations keep behavior byte-for-byte with the retired RVND
modules:

* **incident vocabulary.** deontic classifies an obligation's Hohfeld incident
  as ``"duty"``; RVND (and the solver ``norm_contract`` NT-14 vocabulary) name
  that position ``"claim-duty"``. The classification engine is deontic's; only
  the surface label is mapped back.
* **scheduler report shape.** the plane proposes an abstract, UNGATED
  ``FollowUp``; RVND's callers expect a ``Proposal`` carrying the governance
  ``GateDecision``. The sweep is the plane's, consumed whole; this seam maps the
  action class to its disclosure footprint, runs RVND's ``action_gate`` on its
  side, and re-shapes the report.

Nothing here re-implements extraction, obligation state, subsumption's universal
layer, the span-registry mechanics, or the scheduler sweep — those are
loomground-norm's, whole and entire; and nothing here re-implements anchoring —
that is loomground-legal's, consumed through ``adapters.legal``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import loomground_norm as _norm

# -- plain re-exports: the plane's pure public surface, unchanged -------------
from loomground_norm import (
    RuleFacet, FingerprintGate,
    Phase2Result, PHASE2_CONFIDENCE_CAP,
    Obligation, ObligationError, OPEN_STATES, TERMINAL_STATES,
    Step, Gap, Subsumption, ROLES, REQUIRED_ROLES,
    SpanNorm,
    FollowUp, RegionalPack,
    SourceInstrument, AuditSink, NullAuditSink,
)
from loomground_norm import build_subsumption as build            # subsumption_path.build
from loomground_norm import SubsumptionFinding as Finding         # subsumption_validator.Finding
from loomground_norm import target_state as _target_state        # scheduler pure-arithmetic
from loomground_norm.obligation_scheduler import DEFAULT_WARNING_WINDOW
from loomground_norm.rule_extractor import (                      # module-level helpers callers touch
    _detect_language, supported_languages, _is_agentless_passive, _segment,
)
from loomground_norm.obligation_runtime import _obligor_role      # test/consumer helper

# -- anchoring comes from the LEGAL seam, not from norm -----------------------
from .legal import (
    Anchor,
    anchor as _legal_anchor,
    place_legal_text as _legal_place_legal_text,
    as_package_world as _as_package_world,
)

# -- the closed incident vocabulary the WHOLE system validates against, plus
#    the reasoning-level enum for the regional subsumption layer. Sourced from
#    the consumed solver norm-contract (NT-14), not re-declared here.
from .solver.norm_contract import INCIDENTS, Level

# -- deontic classification engine (consumed; label mapped back below) -------
from deontic import (
    classify_incident as _deontic_classify_incident,
    extract_counterparty,
    classify_condition_kind,
)

__all__ = [
    # extractor
    "RuleFacet", "FingerprintGate", "extract_rules", "FINGERPRINT_GATE",
    "_detect_language", "supported_languages", "_is_agentless_passive", "_segment",
    # extractor (llm)
    "Phase2Result", "PHASE2_CONFIDENCE_CAP", "extract_rules_llm",
    # hohfeld
    "INCIDENTS", "attach_incidents", "classify_incident",
    "extract_counterparty", "classify_condition_kind",
    # obligation runtime
    "Obligation", "ObligationRegistry", "ObligationError",
    "OPEN_STATES", "TERMINAL_STATES", "_obligor_role",
    # subsumption
    "Step", "Gap", "Subsumption", "build", "ROLES", "REQUIRED_ROLES",
    "Finding", "ValidationReport", "validate",
    # rule registry (anchoring via adapters.legal)
    "Anchor", "SpanNorm", "RuleRegistry", "place_into_registry",
    # scheduler
    "ObligationScheduler", "SchedulerReport", "Proposal", "FollowUp",
    "DEFAULT_WARNING_WINDOW", "_target_state",
    # ports (re-exported for completeness)
    "SourceInstrument", "AuditSink", "NullAuditSink", "RegionalPack",
]


# ════════════════════════════════════════════════════════════════════════════
# rule_extractor — inject RVND's ND-routing normative fingerprint as the gate
# ════════════════════════════════════════════════════════════════════════════

def FINGERPRINT_GATE(content: str) -> bool:
    """RVND's normative fingerprint as loomground-norm's ``FingerprintGate``:
    ``score_normative(content) >= NORMATIVE_THRESHOLD``. The ND-routing layer is
    RVND's; the plane only asks whether content is worth extracting from."""
    from ..nd_routing import NORMATIVE_THRESHOLD, score_normative
    score, _ = score_normative(content)
    return score >= NORMATIVE_THRESHOLD


def extract_rules(content: str, *, gated_by_fingerprint: bool = True) -> list:
    """RVND's historical ``extract_rules`` signature over the consumed plane:
    when ``gated_by_fingerprint`` (default), inject RVND's normative fingerprint
    as the plane's ``fingerprint_gate``; otherwise run ungated. Extraction
    itself is loomground-norm's, unchanged."""
    return _norm.extract_rules(
        content, fingerprint_gate=FINGERPRINT_GATE if gated_by_fingerprint else None)


# ════════════════════════════════════════════════════════════════════════════
# hohfeld — consume deontic's classifiers; map the label back to RVND's vocab
# ════════════════════════════════════════════════════════════════════════════

# deontic names an obligation's incident "duty"; RVND + the solver's NT-14
# closed vocabulary name it "claim-duty". Only the surface label is mapped;
# every other incident (privilege / power / immunity / disability) is identical.
_INCIDENT_LABEL = {"duty": "claim-duty"}


def classify_incident(modal: str, action: str, raw: str) -> str:
    """deontic's deterministic Hohfeld classifier, with the ``duty`` label
    mapped back to RVND's ``claim-duty``. Abstains ('') exactly as deontic does."""
    inc = _deontic_classify_incident(modal, action, raw)
    return _INCIDENT_LABEL.get(inc, inc)


def attach_incidents(facets: list, roles=()) -> int:
    """Enrich RuleFacets with deontic's incident layer (consumed via the plane),
    then map the ``duty`` label back to ``claim-duty`` in place. Behavior — which
    facets are enriched, counterparty and condition-kind — is the plane's."""
    n = _norm.attach_incidents(facets, roles)
    for f in facets:
        inc = getattr(f, "incident", "")
        if inc in _INCIDENT_LABEL:
            f.incident = _INCIDENT_LABEL[inc]
    return n


def extract_rules_llm(text: str, *, model_fn=None, defined_terms=(),
                      dedupe_against_phase1: bool = True) -> Phase2Result:
    """Phase-2 extraction, consumed from the plane. The plane stamps incidents
    with deontic's vocabulary internally; map the ``duty`` label back to
    ``claim-duty`` on the returned facets so RVND's vocabulary is preserved."""
    res = _norm.extract_rules_llm(text, model_fn=model_fn,
                                  defined_terms=defined_terms,
                                  dedupe_against_phase1=dedupe_against_phase1)
    for f in res.facets:
        inc = getattr(f, "incident", "")
        if inc in _INCIDENT_LABEL:
            f.incident = _INCIDENT_LABEL[inc]
    return res


# ════════════════════════════════════════════════════════════════════════════
# audit — map the plane's neutral event onto RVND's signed-mutation-log schema
# ════════════════════════════════════════════════════════════════════════════

# The plane's neutral event names differ from RVND's audit vocabulary. The
# span-registry emits ``event="place-span"`` (with ``rule_id``, no channel); the
# obligation runtime emits ``event="system"`` (with ``pair_id`` + ``channel``).
# RVND records both as ingest-class writes on the folder's chain, re-adding
# ``pair_id`` / ``channel`` on RVND's side exactly as the retired modules did.
_EVENT_MAP = {"place-span": "ingest"}


class _MutationLogAuditSink:
    """loomground-norm ``AuditSink`` over RVND's signed mutation log. Tolerant of
    both plane event shapes (span-placement and obligation-runtime) and
    best-effort (never raises into the plane), exactly like the retired modules'
    ``_log``."""

    def __init__(self, folder, log_root=None):
        self.folder = folder
        self.log_root = log_root

    def log(self, event: dict) -> Optional[str]:
        try:
            from ..mutation_log import LogEvent, MutationLog
            log = MutationLog(self.folder, log_root=self.log_root)
            raw = event.get("event", "")
            return log.append(LogEvent(
                event=_EVENT_MAP.get(raw, raw),
                folder_path=event.get("folder_path") or event.get("store")
                or str(self.folder),
                pair_id=event.get("pair_id") or event.get("rule_id") or "",
                channel=event.get("channel") or "document",
                actor=event.get("actor", "system"),
                extra=event.get("extra", {})))
        except Exception:                                          # noqa: BLE001
            return None


# ════════════════════════════════════════════════════════════════════════════
# obligation_runtime — inject RVND's signed mutation log as the AuditSink
# ════════════════════════════════════════════════════════════════════════════

class ObligationRegistry(_norm.ObligationRegistry):
    """RVND's ``ObligationRegistry(folder, *, log_root=None)`` over the plane's
    registry, with the signed mutation log wired in as the ``AuditSink``."""

    def __init__(self, folder, *, log_root=None):
        super().__init__(folder, audit_sink=_MutationLogAuditSink(folder, log_root))
        self.log_root = Path(log_root) if log_root else None


# ════════════════════════════════════════════════════════════════════════════
# subsumption_validator — universal from the plane; RVND regains the regional
# (legal-family) layer norm dropped when it went domain-neutral
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationReport:
    """RVND's historical two-layer report shape (``legal_system`` label, not the
    plane's neutral ``region``). Consumers read ``.ok`` / ``.escalations`` /
    ``.violations`` / ``.legal_system`` (e.g. ``legal_pipeline``)."""

    legal_system: str
    findings: list = field(default_factory=list)

    @property
    def violations(self) -> list:
        return [f for f in self.findings if f.level is Level.VIOLATION]

    @property
    def escalations(self) -> list:
        return [f for f in self.findings if f.level is Level.ESCALATE]

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict:
        return {"legal_system": self.legal_system, "ok": self.ok,
                "must_escalate": bool(self.escalations),
                "findings": [f.to_dict() for f in self.findings]}


def _citation_ok(source: str, markers: tuple) -> bool:
    s = (source or "")
    return any(m.lower() in s.lower() for m in markers)


def validate(sub: Subsumption, *, legal_system: str = "DE") -> ValidationReport:
    """RVND's historical two-layer subsumption validation. The UNIVERSAL layer
    (jurisdiction-agnostic norm theory) is consumed whole from the plane
    (``validate_subsumption(sub, pack=None)``); the REGIONAL layer — citation
    forms and collision principles a legal FAMILY recognises — is a legal-domain
    concrete the plane shed when it went domain-neutral, so RVND regains it here
    over its active ``legal_systems`` pack (default DE)."""
    from .. import legal_systems as _ls
    pack = _ls.get(legal_system)
    uni = _norm.validate_subsumption(sub, pack=None)
    rep = ValidationReport(legal_system=pack.code)
    rep.findings.extend(f for f in uni.findings if f.layer == "universal")

    # ── REGIONAL (the active legal-system pack) ─────────────────────────────
    for step in sub.steps:
        if step.source and not _citation_ok(step.source, pack.citation_markers):
            rep.findings.append(Finding(
                "regional", "R1-citation-form", Level.VIOLATION,
                f"step '{step.role}' cites {step.source!r}, not a "
                f"{pack.code} form ({', '.join(pack.citation_markers)})"))
    # a collision may be resolved only by a principle the family recognises —
    # otherwise it must escalate, never auto-resolve.
    if any(g.kind == "conflict" for g in sub.gaps):
        rep.findings.append(Finding(
            "regional", "R2-conflict-principle", Level.ESCALATE,
            f"resolve under {pack.code} principles "
            f"({', '.join(pack.conflict_principles)}) — human, not auto"))
    if not any(f.layer == "regional" for f in rep.findings):
        rep.findings.append(Finding(
            "regional", "R0", Level.PASS,
            f"regional ({pack.code}) norm theory satisfied"))
    return rep


# ════════════════════════════════════════════════════════════════════════════
# rule_registry — neutral plane registry + RVND's regained product-concretes:
# legal-domain anchoring (via adapters.legal), the per-user mirror, the
# legal-corpus subdir, and the identity-spine URN minter
# ════════════════════════════════════════════════════════════════════════════

def _urn_minter(seed: str) -> str:
    """The plane's ``urn_minter`` port: mint the span's canonical URN on RVND's
    identity spine. ``mint_canonical("rule-<hash>")`` reproduces the retired
    module's ``_span_urn`` exactly."""
    from ..urn import mint_canonical
    return mint_canonical(seed)


def _pkg_world(folder: Optional[str]):
    """The legal map to anchor against, as loomground-legal's native (string-edged)
    ``WorldMap``: the folder's persisted corpus if it has one, else the digital-law
    seed. The provider stays RVND's — ``legal_world`` (the seed facade) and
    ``legal_corpus`` (the persisted corpus) — and this converts the resulting
    enum-edged map to the package shape the anchoring mechanism reads."""
    from ..legal_world import seed_world
    if folder:
        try:
            from ..legal_corpus import EntityRegistry
            reg = EntityRegistry(str(folder))
            if reg.entities:
                return _as_package_world(reg.to_world_map())
        except Exception:                                          # noqa: BLE001
            pass
    return _as_package_world(seed_world())


class RuleRegistry(_norm.RuleRegistry):
    """RVND's ``RuleRegistry(folder, *, user, user_root, log_root)`` over the
    plane's NEUTRAL span-norm registry, with RVND's product-concretes restored:

      * **anchoring** — legal-domain placement via ``adapters.legal.anchor`` /
        ``place_legal_text``, bridging RVND's ``corpus.ingest.candidates_from_text``
        recogniser in for byte-identical placements; the resulting
        ``Anchor.to_dict()`` shape is stored on each record;
      * **the per-user mirror** — the ``~/.workspace/log`` cross-project store;
      * **subdir** — ``"legal-corpus"`` (RVND's choice of persistence name);
      * **urn_minter** / **audit_sink** — the identity spine and the signed
        mutation log (audit event mapped to RVND's schema by the sink).

    Placement, persistence, re-pinning, orphan tracking and the search queries
    are the plane's; anchoring and the per-user mirror are RVND's, layered on."""

    def __init__(self, folder, *, user="", user_root=None, log_root=None):
        from ..folder_context import resolve_folder_context
        from ..mutation_log import LOG_ROOT_DEFAULT
        folder = Path(resolve_folder_context(folder))
        super().__init__(
            folder, user=user, subdir="legal-corpus",
            urn_minter=_urn_minter,
            audit_sink=_MutationLogAuditSink(folder, log_root))
        self.user_root = Path(user_root) if user_root else LOG_ROOT_DEFAULT
        self.log_root = Path(log_root) if log_root else None

    # ── per-user cross-project mirror (RVND product feature) ───────────────────
    def _user_path(self) -> Path:
        return self.user_root / "rule-registry.jsonl"

    def _mirror_user(self, rec: dict) -> None:
        """Append/refresh the rule in the per-user store, tagged with its
        workspace. Keyed by (user, workspace, id) so the same rule from two
        workspaces coexists. Best-effort."""
        import json
        try:
            up = self._user_path()
            up.parent.mkdir(parents=True, exist_ok=True)
            rows: dict = {}
            if up.exists():
                for line in up.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        r = json.loads(line)
                        rows[(r.get("user", ""), r.get("workspace", ""), r["id"])] = r
            rows[(rec.get("user", ""), rec.get("workspace", ""), rec["id"])] = rec
            up.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                    for r in rows.values()) + "\n", encoding="utf-8")
        except Exception:                                          # noqa: BLE001
            pass

    # ── anchoring: consumed from the legal seam ────────────────────────────────
    def _anchors_for_span(self, span_text: str) -> list:
        """The legal entities a span-norm is placed at — via the legal plane's
        ``anchor``, bridging RVND's richer instrument recogniser in through
        ``candidates=`` for byte-identical placements."""
        from ..corpus.ingest import candidates_from_text
        world = _pkg_world(str(self.folder))
        cands = candidates_from_text(span_text)
        return [a.to_dict() for a in _legal_anchor(span_text, world, candidates=cands)]

    # ── placement ─────────────────────────────────────────────────────────────
    def place_span(self, span_text, *, facet=None, anchors=None, **kw):
        """Place one span (= one norm), anchored onto the legal map. The plane
        persists the record; RVND pre-enriches the facet with its ``claim-duty``
        incident vocabulary, computes/attaches the legal anchors, and mirrors the
        record to the per-user store. ``anchors`` (precomputed) override
        text-derived anchoring — used when the host instrument is known."""
        if facet is None:
            facets = extract_rules(span_text)
            facet = facets[0] if facets else RuleFacet(raw_sentence=span_text)
        attach_incidents([facet])                    # claim-duty vocab, before persist
        if anchors is None:
            anchors = self._anchors_for_span(span_text)
        rec = super().place_span(span_text, facet=facet, **kw)
        stored = self.items[rec["id"]]
        if "anchors" not in stored:
            stored["anchors"] = anchors
            self._flush_workspace()
        elif anchors and not stored.get("anchors"):
            stored["anchors"] = anchors
            self._flush_workspace()
        self._mirror_user(stored)
        rec["anchors"] = stored["anchors"]
        return rec

    def place_document(self, content, *, source_document="", kind="rule",
                       source="ingest"):
        """Extract one norm per span (gated by RVND's normative fingerprint) and
        place each onto the legal map through the plane."""
        placed = []
        for f in extract_rules(content):
            span = f.raw_sentence or ""
            if not span.strip():
                continue
            idx = content.find(span)
            r = self.place_span(span, source_document=source_document,
                                start=idx if idx >= 0 else None,
                                end=(idx + len(span)) if idx >= 0 else None,
                                kind=kind, facet=f, source=source)
            placed.append({"id": r["id"], "status": r["status"],
                           "anchors": [a["entity"] for a in r["anchors"]]})
        return {"placed": placed, "count": len(placed),
                "created": sum(p["status"] == "created" for p in placed)}

    def place_legal_text(self, content, instrument_code, *, source_document="",
                         source="ingest"):
        """Ingest a law's own text: cut it into provisions and place each norm as
        an individual span anchored to the host instrument (``cites`` at the
        provision pinpoint) plus its jurisdiction and enforcing regulators. The
        provision cut + host anchoring are the legal plane's
        (``adapters.legal.place_legal_text``, RVND's legal-norm splitter
        injected); norm extraction per provision is the norm plane's."""
        from ..adapters.ingest.governance import legal_norm_splitter as _lns
        world = _pkg_world(str(self.folder))
        provs = _legal_place_legal_text(content, world, instrument_code,
                                        splitter=_lns.segment_provisions)
        placed = []
        for prov in provs:
            pinpoint = prov["pinpoint"]
            anchors = [a.to_dict() for a in prov["anchors"]]
            # one law, many norms: do NOT fingerprint-dedupe across provisions —
            # every article's operative norm must enter the map individually.
            for f in extract_rules(prov["text"], gated_by_fingerprint=False):
                span = (f.raw_sentence or "").strip()
                if not span:
                    continue
                r = self.place_span(span, source_document=source_document,
                                    kind="norm", facet=f, source=source,
                                    anchors=anchors, pinpoint=pinpoint)
                placed.append({"id": r["id"], "status": r["status"],
                               "pinpoint": pinpoint, "modal": r["norm"].get("modal")})
        return {"instrument": instrument_code, "placed": placed,
                "count": len(placed),
                "created": sum(p["status"] == "created" for p in placed),
                "provisions": len({p["pinpoint"] for p in placed})}

    # ── queries (anchor-aware; over the stored records) ────────────────────────
    def rules_at(self, entity_code: str) -> list:
        """Reverse index: every span-norm placed at a given legal entity."""
        return [r for r in self.items.values()
                if any(a["entity"] == entity_code for a in r.get("anchors", []))]

    def search(self, *, modal=None, relation=None) -> list:
        out = []
        for r in self.items.values():
            if modal and r["norm"].get("modal") != modal:
                continue
            if relation and not any(a["relation"] == relation
                                    for a in r.get("anchors", [])):
                continue
            out.append(r)
        return out

    def user_items(self, *, user=None) -> list:
        """Every span-norm in the per-user store (across workspaces), optionally
        filtered to one user."""
        import json
        up = self._user_path()
        if not up.exists():
            return []
        rows = [json.loads(x) for x in up.read_text(encoding="utf-8").splitlines()
                if x.strip()]
        return [r for r in rows if user is None or r.get("user", "") == user]


def place_into_registry(folder, content, *, user="", source_document="",
                        log_root=None, source="ingest") -> dict:
    """RVND's ingest hook over the plane. If the document IS a legal instrument
    (host recognised), route to article-aware extraction; else treat it as a
    third-party document. Host detection + code normalisation stay RVND's,
    injected. Never raises into the caller."""
    try:
        reg = RuleRegistry(folder, user=user, log_root=log_root)
        from ..corpus.ingest import _CODE_ALIASES
        from ..crossref_extractor import infer_host_instrument
        host = infer_host_instrument(content)
        if host:
            code = _CODE_ALIASES.get(host, host)
            return reg.place_legal_text(content, code,
                                        source_document=source_document, source=source)
        return reg.place_document(content, source_document=source_document, source=source)
    except Exception as exc:                                       # noqa: BLE001
        return {"placed": [], "error": f"{type(exc).__name__}: {exc}"}


# ════════════════════════════════════════════════════════════════════════════
# obligation_scheduler — the plane proposes UNGATED; RVND classifies each
# proposal (footprint → action_gate verdict) on its side
# ════════════════════════════════════════════════════════════════════════════

# What disclosure footprint each action class carries — the governance concern
# the plane deliberately does NOT attach. A reminder goes OUT to the obligor
# ("external-publish"): without a standing approval the gate returns CONDITIONAL/
# NO-GO, so no message leaves silently. Surfacing a breach candidate is internal.
_ACTION_FOOTPRINT = {
    "remind-obligor": ("external-publish",),
    "surface-breach-candidate": (),
}


@dataclass
class Proposal:
    """One action the scheduler wants to take, with its governance verdict —
    RVND's report shape over the plane's abstract, ungated ``FollowUp``."""

    obligation_id: str
    action_class: str
    target_state: str
    decision: Any                                  # action_gate.GateDecision

    def to_dict(self) -> dict:
        return {"obligation_id": self.obligation_id, "action_class": self.action_class,
                "target_state": self.target_state, "decision": self.decision.to_dict()}


@dataclass
class SchedulerReport:
    as_of: str
    transitions: list = field(default_factory=list)
    proposals: list = field(default_factory=list)      # [Proposal]
    unresolved: list = field(default_factory=list)
    candidates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"as_of": self.as_of, "transitions": self.transitions,
                "proposals": [p.to_dict() for p in self.proposals],
                "unresolved": self.unresolved, "candidates": self.candidates}


class _ContractInstrumentSource:
    """The plane's ``InstrumentSource`` port over RVND's contract registry:
    resolve ``"cid@version"`` to the ``ContractInstance`` a deadline is relative
    to (satisfies ``SourceInstrument`` structurally)."""

    def __init__(self, contracts):
        self.contracts = contracts

    def get(self, ref: str):
        cid, _, ver = ref.partition("@")
        try:
            return self.contracts.get(cid, int(ver)) if ver else self.contracts.get(cid)
        except Exception:                                          # noqa: BLE001
            return None


class ObligationScheduler:
    """RVND's ``ObligationScheduler(folder, *, log_root, warning_window,
    autonomy_grade, standing_approvals, posture, deadline_shift)`` over the
    plane's UNGATED scheduler.

    The sweep — deadline resolution, monotone state advancement, the weekend/
    public-holiday caveats and ``deadline_shift`` handling — is loomground-norm's,
    consumed whole. The plane now only PROPOSES: it emits an ungated ``FollowUp``
    (what, when, who is affected) and attaches no verdict. This seam classifies
    each proposal on RVND's side — maps the action class to its disclosure
    footprint, runs ``action_gate.gate`` (autonomy grade × footprint × standing
    approvals × posture), and re-shapes the report into RVND's ``SchedulerReport``
    of ``Proposal`` objects carrying the governance ``GateDecision``."""

    def __init__(self, folder, *, log_root=None,
                 warning_window=DEFAULT_WARNING_WINDOW,
                 autonomy_grade: str = "L2",
                 standing_approvals=(),
                 posture: str = "balanced",
                 deadline_shift=None):
        from ..contracts.instance import ContractRegistry
        self.obligations = ObligationRegistry(folder, log_root=log_root)
        self.contracts = ContractRegistry(folder, log_root=log_root)
        self.window = warning_window
        self.grade = autonomy_grade
        self.standing = tuple(standing_approvals)
        self.posture = posture
        self.deadline_shift = deadline_shift

    def tick(self, as_of=None) -> SchedulerReport:
        from ..action_gate import ActionRequest, gate
        inner = _norm.ObligationScheduler(
            self.obligations,
            instruments=_ContractInstrumentSource(self.contracts),
            warning_window=self.window, deadline_shift=self.deadline_shift)
        rep = inner.tick(as_of)

        out = SchedulerReport(as_of=rep.as_of, transitions=rep.transitions,
                              unresolved=rep.unresolved, candidates=rep.candidates)
        for fu in rep.proposals:                       # ungated FollowUp dicts
            action_class = fu["action_class"]
            footprint = _ACTION_FOOTPRINT.get(action_class, ())
            decision = gate(
                ActionRequest(agent="obligation-scheduler",
                              action_class=action_class,
                              autonomy_grade=self.grade,
                              footprint=tuple(footprint),
                              affected_parties=tuple(fu.get("affected_parties", ()))),
                standing_approvals=self.standing, posture=self.posture)
            out.proposals.append(Proposal(
                obligation_id=fu["obligation_id"], action_class=action_class,
                target_state=fu["target_state"], decision=decision))
        return out
