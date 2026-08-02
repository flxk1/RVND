# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Class-C legal pipeline — the Validation Layer that refuses an autonomous answer.

This is the essay's strict rule wired as one gate: where law knows exceptions,
discretion and time-bound versions, a probabilistic ranking may not replace the
rechtsstaatliche Prüfung. A class-C (Verwaltungsakt) output may stand only if,
in order:

  1. CORPUS    — every document was actually read (corpus_coverage), not just the
                 top-k that fit attention;
  2. NEGATIVE  — the mandatory counter-categories were searched and documented
                 (negative_search): exceptions, transitional law, discretion,
                 counter-jurisprudence — absence recorded, not assumed;
  3. CHAIN     — the subsumption chain is built with no blocking gap
                 (subsumption_path): Norm → Tatbestand → Ausnahme → Auslegung →
                 Subsumtion → Ergebnis;
  4. VALIDATE  — the chain conforms to universal AND regional norm theory
                 (subsumption_validator);
  5. CONTRACT  — the emitted pairs clear the norm-theory contract
                 (norm_contract.gate).

Any blocked stage stops the output. Any escalation (discretion present, an
unresolved conflict, low confidence) routes to a human. The pipeline never
decides — it certifies that the structure was followed, or refuses.

Pure stdlib + workspaces internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .corpus import coverage as _cc
from . import negative_search as _ns
from . import subsumption_path as _sp
from . import subsumption_validator as _sv
from . import norm_contract as _nc


@dataclass
class StageResult:
    stage: str
    status: str              # "pass" | "blocked" | "escalate"
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    stages: list[StageResult] = field(default_factory=list)

    @property
    def blocked(self) -> list[StageResult]:
        return [s for s in self.stages if s.status == "blocked"]

    @property
    def escalations(self) -> list[StageResult]:
        return [s for s in self.stages if s.status == "escalate"]

    @property
    def ok(self) -> bool:
        """A class-C answer may stand only if NO stage is blocked."""
        return not self.blocked

    @property
    def must_escalate(self) -> bool:
        return bool(self.escalations)

    @property
    def verdict(self) -> str:
        if self.blocked:
            return "REFUSED"
        if self.escalations:
            return "ESCALATE-TO-HUMAN"
        return "CERTIFIED"

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "ok": self.ok,
                "must_escalate": self.must_escalate,
                "stages": [{"stage": s.stage, "status": s.status, "detail": s.detail}
                           for s in self.stages]}


def run_class_c(*, declared_docs: Iterable[str], processed_docs: Iterable[str],
                query: str, corpus: Iterable[dict],
                atoms: Iterable[dict], pairs: Iterable[dict],
                skipped: Optional[dict[str, str]] = None,
                edges: Optional[Iterable[dict]] = None,
                conflicts: Optional[Iterable[dict]] = None,
                legal_system: str = "DE", risk_class: str = "C") -> PipelineResult:
    """Run the five-stage class-C certification. Short-circuits on the first
    blocking stage (a later stage cannot cure an earlier refusal)."""
    res = PipelineResult()
    corpus = list(corpus)
    atoms = list(atoms)

    # 1. CORPUS — read everything.
    cov = _cc.assess(declared_docs, processed_docs, skipped=skipped)
    res.stages.append(StageResult(
        "corpus", "pass" if cov.complete else "blocked",
        {"total": cov.total, "unread": cov.unread, "ratio": round(cov.ratio, 4)}))
    if not cov.complete:
        return res

    # 2. NEGATIVE — search and document the counter-categories.
    rec = _ns.run(query, corpus)
    discretion = next((p for p in rec.probes if p.category == "discretion"), None)
    ns_status = "pass"
    if not rec.complete:
        ns_status = "blocked"
    elif discretion and discretion.hits:        # Ermessen present ⇒ human
        ns_status = "escalate"
    res.stages.append(StageResult("negative_search", ns_status,
                                  {"found_nothing": rec.found_nothing,
                                   "discretion_hits": discretion.hits if discretion else []}))
    if ns_status == "blocked":
        return res

    # 3. CHAIN — build the subsumption path; a blocking gap refuses.
    sub = _sp.build(atoms, edges=edges, conflicts=conflicts)
    res.stages.append(StageResult(
        "subsumption", "pass" if sub.complete else "blocked",
        {"render": sub.render(), "gaps": [g.to_dict() for g in sub.gaps]}))
    if not sub.complete:
        return res

    # 4. VALIDATE — universal + regional norm theory.
    vrep = _sv.validate(sub, legal_system=legal_system)
    v_status = "pass" if vrep.ok else "blocked"
    if vrep.ok and vrep.escalations:
        v_status = "escalate"
    res.stages.append(StageResult("validation", v_status,
                                  {"legal_system": vrep.legal_system,
                                   "findings": [f.to_dict() for f in vrep.findings]}))
    if v_status == "blocked":
        return res

    # 5. CONTRACT — emission conformance.
    try:
        creport = _nc.gate(pairs, risk_class=risk_class, legal_system=legal_system)
        c_status = "escalate" if creport.escalations else "pass"
        detail = {"escalations": [f.code for f in creport.escalations]}
    except _nc.ContractViolation as exc:
        c_status = "blocked"
        detail = {"violations": [v.code for v in exc.report.violations]}
    res.stages.append(StageResult("contract", c_status, detail))
    return res
