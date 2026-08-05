# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND adapter seam over loomground-norm — the general normative-reasoning plane.

The workspaces boundary rule confines every direct import of an upstream
Loomground package to the ``adapters/`` seam (see
``tests/test_adapter_boundary.py``). This module is that seam for the
**norm-runtime** plane: rule extraction, obligation state, subsumption, the
span-norm registry, and the obligation scheduler. It does two things and only
these two:

1. **re-exports** loomground-norm's public surface (``RuleFacet``,
   ``Obligation``, ``Subsumption``, ``SpanNorm``, …) so
   ``workspaces.rule_extractor`` and its seven sibling twins can consume the
   plane through here rather than reaching upstream directly; and

2. **wires the injected ports** — loomground-norm declares thin Protocol ports
   (``anchor_resolver`` / ``urn_minter`` / ``audit_sink`` / ``provision_splitter``
   / ``host_anchor_resolver`` on the registry; ``ActionGate`` / ``InstrumentSource``
   on the scheduler; ``SourceInstrument`` / ``AuditSink`` on the runtime;
   ``FingerprintGate`` on the extractor; ``LegalSystemPack`` on the validator).
   The **providers** for those ports are RVND's legitimate second line —
   ``mutation_log`` (audit), ``contracts`` (instruments), ``action_gate``
   (governance verdict), ``legal_world`` + ``corpus.ingest`` + ``urn``
   (legal-domain anchoring), ``legal_systems`` (jurisdiction packs), ``nd_routing``
   (normative fingerprint). This module composes those providers into the
   callables/instances the plane declares, and hands consumers back objects with
   exactly RVND's historical signatures and behavior.

Two deliberate translations keep behavior byte-for-byte with the retired
RVND modules:

* **incident vocabulary.** loomground-deontic classifies an obligation's
  Hohfeld incident as ``"duty"``; RVND (and the consumed solver's
  ``norm_contract`` NT-14 vocabulary) name that position ``"claim-duty"``. The
  classification *engine* is deontic's, consumed whole; only the surface label
  is mapped back so the closed vocabulary the rest of the system validates
  against is unchanged.
* **scheduler report shape.** the plane proposes an abstract ``FollowUp`` and
  lets an injected ``ActionGate`` decide; RVND's callers expect a ``Proposal``
  carrying the governance ``GateDecision``. The sweep (date arithmetic, state
  machine, weekend/holiday caveats) is the plane's, consumed whole; this seam
  only wires RVND's ``action_gate.gate`` in and re-shapes the report.

Nothing here re-implements extraction, obligation state, subsumption, the
span-registry mechanics, or the scheduler sweep — those are loomground-norm's,
whole and entire.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import loomground_norm as _norm

# -- plain re-exports: the plane's public surface, unchanged -----------------
from loomground_norm import (
    RuleFacet, FingerprintGate,
    Phase2Result, PHASE2_CONFIDENCE_CAP,
    Obligation, ObligationError, OPEN_STATES, TERMINAL_STATES,
    Step, Gap, Subsumption, ROLES, REQUIRED_ROLES,
    ValidationReport,
    Anchor, SpanNorm,
    FollowUp,
    SourceInstrument, AuditSink, NullAuditSink, LegalSystemPack,
)
from loomground_norm import build_subsumption as build            # subsumption_path.build
from loomground_norm import SubsumptionFinding as Finding         # subsumption_validator.Finding
from loomground_norm import target_state as _target_state        # scheduler pure-arithmetic
from loomground_norm.obligation_scheduler import DEFAULT_WARNING_WINDOW
from loomground_norm.rule_extractor import (                      # module-level helpers callers touch
    _detect_language, supported_languages, _is_agentless_passive, _segment,
)
from loomground_norm.obligation_runtime import _obligor_role      # test/consumer helper

# -- the closed incident vocabulary the WHOLE system validates against -------
# Sourced from the consumed solver norm-contract (NT-14), not re-declared here.
from .solver.norm_contract import INCIDENTS

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
    # rule registry
    "Anchor", "SpanNorm", "RuleRegistry", "place_into_registry",
    # scheduler
    "ObligationScheduler", "SchedulerReport", "Proposal", "FollowUp",
    "DEFAULT_WARNING_WINDOW", "_target_state",
    # ports (re-exported for completeness)
    "SourceInstrument", "AuditSink", "NullAuditSink", "LegalSystemPack",
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


# ════════════════════════════════════════════════════════════════════════════
# rule_extractor_llm — consume the plane; keep RVND's incident vocabulary
# ════════════════════════════════════════════════════════════════════════════

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
# obligation_runtime — inject RVND's signed mutation log as the AuditSink
# ════════════════════════════════════════════════════════════════════════════

class _MutationLogAuditSink:
    """loomground-norm ``AuditSink`` over RVND's signed mutation log. The plane
    hands ``log`` a ready event dict (event/folder_path/pair_id/channel/actor/
    extra); this appends it as a ``LogEvent`` on the folder's chain. Best-effort
    (never raises into the plane), exactly like the retired modules' ``_log``."""

    def __init__(self, folder, log_root=None):
        self.folder = folder
        self.log_root = log_root

    def log(self, event: dict) -> Optional[str]:
        try:
            from ..mutation_log import LogEvent, MutationLog
            log = MutationLog(self.folder, log_root=self.log_root)
            return log.append(LogEvent(
                event=event["event"], folder_path=event["folder_path"],
                pair_id=event["pair_id"], channel=event["channel"],
                actor=event.get("actor", "system"),
                extra=event.get("extra", {})))
        except Exception:                                          # noqa: BLE001
            return None


class ObligationRegistry(_norm.ObligationRegistry):
    """RVND's ``ObligationRegistry(folder, *, log_root=None)`` over the plane's
    registry, with the signed mutation log wired in as the ``AuditSink``."""

    def __init__(self, folder, *, log_root=None):
        super().__init__(folder, audit_sink=_MutationLogAuditSink(folder, log_root))
        self.log_root = Path(log_root) if log_root else None


# ════════════════════════════════════════════════════════════════════════════
# subsumption_validator — inject RVND's active legal-system pack (default DE)
# ════════════════════════════════════════════════════════════════════════════

def validate(sub: Subsumption, *, legal_system: str = "DE") -> ValidationReport:
    """RVND's historical ``validate(sub, legal_system="DE")``: resolve the
    jurisdiction family from ``legal_systems.get`` (satisfies the plane's
    ``LegalSystemPack`` port) and hand it to the plane's validator, so the
    regional layer always runs with the active pack."""
    from .. import legal_systems as _ls
    return _norm.validate_subsumption(sub, pack=_ls.get(legal_system))


# ════════════════════════════════════════════════════════════════════════════
# rule_registry — anchoring / urn / audit / provision providers stay RVND's
# ════════════════════════════════════════════════════════════════════════════

def _resolve_world(folder: Optional[str]):
    """The legal map to anchor against: the folder's persisted corpus if it has
    one, else the digital-law seed. Provider stays RVND (legal_world/legal_corpus)."""
    from ..legal_world import seed_world
    if folder:
        try:
            from ..legal_corpus import EntityRegistry
            reg = EntityRegistry(folder)
            if reg.entities:
                return reg.to_world_map()
        except Exception:                                          # noqa: BLE001
            pass
    return seed_world()


def _anchors_for(span_text: str, world) -> list:
    """The legal entities a span-norm is placed at — every instrument it cites,
    each instrument's jurisdiction, the regulators that enforce it. RVND's
    legal-domain anchoring (corpus.ingest recognition + world-map reach),
    returned as Anchor dicts."""
    from ..corpus.ingest import candidates_from_text
    from ..legal_connection import Connection
    out: list = []
    seen: set = set()

    def _add(entity: str, kind: str, relation: str, basis: str = "") -> None:
        key = (entity, relation)
        if key not in seen:
            seen.add(key)
            out.append(Anchor(entity, kind, relation, basis).to_dict())

    for cand in candidates_from_text(span_text):
        code = cand["code"]
        _add(code, "instrument", "cites", cand.get("pinpoint") or cand.get("name", ""))
        if cand.get("jurisdiction"):
            _add(cand["jurisdiction"], "jurisdiction", "governed_by", "owning order")
        ent = world.get(code)
        if ent is None:
            continue
        for ed in world.edges:
            if ed.subject == code and ed.connection is Connection.APPLIES_IN:
                _add(ed.object, "jurisdiction", "governed_by", ed.basis)
        for ed in world.edges:
            if ed.object == code and ed.connection is Connection.ENFORCES:
                _add(ed.subject, "regulator", "enforced_by", ed.basis)
    return out


def _host_instrument_anchors(code: str, world) -> dict:
    """Resolve a host instrument's jurisdiction(s) and enforcing regulators once."""
    from ..legal_connection import Connection
    jurisdictions: list = []
    regulators: list = []
    ent = world.get(code)
    if ent is not None and ent.jurisdiction:
        jurisdictions.append(ent.jurisdiction)
    for ed in world.edges:
        if ed.subject == code and ed.connection is Connection.APPLIES_IN and ed.object not in jurisdictions:
            jurisdictions.append(ed.object)
        if ed.object == code and ed.connection is Connection.ENFORCES and ed.subject not in regulators:
            regulators.append(ed.subject)
    return {"code": code, "jurisdictions": jurisdictions, "regulators": regulators}


def _host_anchor_dicts(host: dict, pinpoint: str) -> list:
    """Anchor dicts for a provision of the host instrument."""
    out = [Anchor(host["code"], "instrument", "cites", pinpoint).to_dict()]
    for j in host["jurisdictions"]:
        out.append(Anchor(j, "jurisdiction", "governed_by", "owning order").to_dict())
    for r in host["regulators"]:
        out.append(Anchor(r, "regulator", "enforced_by", "mandate").to_dict())
    return out


def _facet_anchor_resolver(world):
    """The plane's ``anchor_resolver`` port: ``facet -> anchors`` over RVND's
    world map (the facet's source sentence is the span text anchoring reads)."""
    return lambda facet: _anchors_for(getattr(facet, "raw_sentence", "") or "", world)


def _host_anchor_resolver_for(world):
    """The plane's ``host_anchor_resolver`` port: ``(code, pinpoint) -> anchors``
    for a law's own provision, over RVND's world map."""
    return lambda code, pinpoint: _host_anchor_dicts(_host_instrument_anchors(code, world),
                                                     pinpoint)


def _urn_minter(seed: str) -> str:
    """The plane's ``urn_minter`` port: mint the span's canonical URN on RVND's
    identity spine. ``mint_canonical("rule-<hash>")`` reproduces the retired
    module's ``_span_urn`` exactly."""
    from ..urn import mint_canonical
    return mint_canonical(seed)


class RuleRegistry(_norm.RuleRegistry):
    """RVND's ``RuleRegistry(folder, *, user, user_root, log_root)`` over the
    plane's registry with every port wired to RVND's providers:

      * ``anchor_resolver`` / ``host_anchor_resolver`` — legal-domain anchoring
        (``legal_world`` + ``corpus.ingest``);
      * ``urn_minter`` — the identity spine (``urn.mint_canonical``);
      * ``audit_sink`` — the signed mutation log;
      * ``provision_splitter`` — the consumed ingest legal-norm splitter;
      * ``user_root`` — RVND's per-user ``~/.workspace/log`` mirror (default on).

    Placement, persistence, re-anchoring, orphan tracking, the reverse/search
    queries and the per-user mirror are the plane's, unchanged. Only the two
    extraction entry points are overridden, to keep RVND's normative-fingerprint
    gating and the ``claim-duty`` incident label."""

    def __init__(self, folder, *, user="", user_root=None, log_root=None):
        from ..folder_context import resolve_folder_context
        from ..mutation_log import LOG_ROOT_DEFAULT
        from ..adapters.ingest.governance import legal_norm_splitter as _lns
        folder = Path(resolve_folder_context(folder))
        world = _resolve_world(str(folder))
        super().__init__(
            folder, user=user,
            user_root=user_root if user_root is not None else LOG_ROOT_DEFAULT,
            anchor_resolver=_facet_anchor_resolver(world),
            urn_minter=_urn_minter,
            audit_sink=_MutationLogAuditSink(folder, log_root),
            provision_splitter=_lns.segment_provisions,
            host_anchor_resolver=_host_anchor_resolver_for(world))
        self.log_root = Path(log_root) if log_root else None

    def place_span(self, span_text, *, facet=None, **kw):
        """Pre-enrich the facet with RVND's incident vocabulary (``claim-duty``)
        before the plane persists it; the plane's ``attach_incidents`` then skips
        the already-enriched facet. Extraction of a bare span stays gated."""
        if facet is None:
            facets = extract_rules(span_text)
            facet = facets[0] if facets else RuleFacet(raw_sentence=span_text)
        attach_incidents([facet])
        return super().place_span(span_text, facet=facet, **kw)

    def place_document(self, content, *, source_document="", kind="rule",
                       source="ingest"):
        """Extract one norm per span (gated by RVND's normative fingerprint) and
        place each through the plane."""
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
# obligation_scheduler — inject RVND's action gate + contract instrument source
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Proposal:
    """One action the scheduler wants to take, with its governance verdict —
    RVND's report shape over the plane's abstract ``FollowUp``."""

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
    plane's scheduler.

    The sweep — deadline resolution, monotone state advancement, the weekend/
    public-holiday caveats and ``deadline_shift`` handling — is loomground-norm's,
    consumed whole. This seam wires the two ports the plane declares:

      * ``ActionGate`` — RVND's ``action_gate.gate`` (autonomy grade × footprint
        × standing approvals × posture), wrapped to accept a ``FollowUp``;
      * ``InstrumentSource`` — RVND's ``ContractRegistry``.

    and re-shapes the plane's report into RVND's ``SchedulerReport`` of
    ``Proposal`` objects carrying the governance ``GateDecision``."""

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
        stash: dict = {}

        def _action_gate(follow_up) -> dict:
            decision = gate(
                ActionRequest(agent="obligation-scheduler",
                              action_class=follow_up.action_class,
                              autonomy_grade=self.grade,
                              footprint=tuple(follow_up.footprint),
                              affected_parties=tuple(follow_up.affected_parties)),
                standing_approvals=self.standing, posture=self.posture)
            stash[(follow_up.obligation_id, follow_up.action_class)] = decision
            return decision.to_dict()

        inner = _norm.ObligationScheduler(
            self.obligations,
            instruments=_ContractInstrumentSource(self.contracts),
            warning_window=self.window, action_gate=_action_gate,
            deadline_shift=self.deadline_shift)
        rep = inner.tick(as_of)

        out = SchedulerReport(as_of=rep.as_of, transitions=rep.transitions,
                              unresolved=rep.unresolved, candidates=rep.candidates)
        for p in rep.proposals:
            fu = p["follow_up"]
            decision = stash.get((fu["obligation_id"], fu["action_class"]))
            if decision is None:                       # ungated (defensive; gate is always wired)
                continue
            out.proposals.append(Proposal(
                obligation_id=fu["obligation_id"], action_class=fu["action_class"],
                target_state=fu["target_state"], decision=decision))
        return out
