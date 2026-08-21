# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Validate a multi-hop / subsumption output against norm theory — two layers.

A built subsumption chain (``rvnd.subsumption_path.Subsumption``) is only as
good as its conformance to legal reasoning rules. Those rules come in two layers:

  * UNIVERSAL norm theory — true in every legal system: every step is sourced
    (provenance); a result must be reached *through* a subsumption (you cannot
    conclude without subsuming); every step carries an authority weight; a
    retrieval or conflict gap voids the chain.
  * REGIONAL norm theory — the active ``legal_systems`` pack: citations must take
    a form the family recognises (§/CELEX for DE/EU; s./UKSC for UK; U.S.C. for
    US); a collision may only be resolved by a principle the family recognises,
    and otherwise must escalate (never auto-resolved); authority follows the
    family's hierarchy.

The validator REUSES the substrate: universal checks mirror the norm-theory
contract's invariants; regional checks read the legal-system pack. It returns a
layered report; it never repairs the chain or resolves a conflict — it judges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .norm_contract import Level                 # reuse PASS / VIOLATION / ESCALATE
from . import legal_systems as _ls
from .subsumption_path import Subsumption


@dataclass
class Finding:
    layer: str               # "universal" | "regional"
    code: str
    level: Level
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"layer": self.layer, "code": self.code,
                "level": self.level.value, "message": self.message}


@dataclass
class ValidationReport:
    legal_system: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.level is Level.VIOLATION]

    @property
    def escalations(self) -> list[Finding]:
        return [f for f in self.findings if f.level is Level.ESCALATE]

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {"legal_system": self.legal_system, "ok": self.ok,
                "must_escalate": bool(self.escalations),
                "findings": [f.to_dict() for f in self.findings]}


def _citation_ok(source: str, markers: tuple[str, ...]) -> bool:
    s = (source or "")
    return any(m.lower() in s.lower() for m in markers)


def validate(sub: Subsumption, *, legal_system: str = "DE") -> ValidationReport:
    pack = _ls.get(legal_system)
    rep = ValidationReport(legal_system=pack.code)

    # ── UNIVERSAL (jurisdiction-agnostic norm theory) ───────────────────────
    for step in sub.steps:
        if not step.source:
            rep.findings.append(Finding("universal", "U1-provenance", Level.VIOLATION,
                                        f"step '{step.role}' has no source (Quellenbindung)"))
        if not isinstance(step.authority_tier, int):
            rep.findings.append(Finding("universal", "U3-authority", Level.VIOLATION,
                                        f"step '{step.role}' carries no authority weight"))
    roles = [s.role for s in sub.steps]
    if "ergebnis" in roles and "subsumtion" not in roles:
        rep.findings.append(Finding("universal", "U4-subsumtion", Level.VIOLATION,
                                    "an Ergebnis without a Subsumtion — conclusion not reached through subsumption"))
    for g in sub.gaps:
        if g.kind == "retrieval":
            rep.findings.append(Finding("universal", "U2-retrieval-gap", Level.VIOLATION,
                                        f"chain broken: {g.detail}"))
        if g.kind == "conflict":
            rep.findings.append(Finding("universal", "U5-conflict", Level.ESCALATE,
                                        f"unresolved collision: {g.detail}"))
        if g.kind in ("context", "authority"):
            rep.findings.append(Finding("universal", f"U-{g.kind}", Level.ESCALATE,
                                        f"{g.kind} gap surfaced: {g.detail}"))
    if not any(f.layer == "universal" for f in rep.findings):
        rep.findings.append(Finding("universal", "U0", Level.PASS,
                                    "universal norm theory satisfied"))

    # ── REGIONAL (the active legal-system pack) ─────────────────────────────
    for step in sub.steps:
        if step.source and not _citation_ok(step.source, pack.citation_markers):
            rep.findings.append(Finding("regional", "R1-citation-form", Level.VIOLATION,
                                        f"step '{step.role}' cites {step.source!r}, not a "
                                        f"{pack.code} form ({', '.join(pack.citation_markers)})"))
    # conflicts must be resolvable only by a principle the family recognises.
    if any(g.kind == "conflict" for g in sub.gaps):
        rep.findings.append(Finding("regional", "R2-conflict-principle", Level.ESCALATE,
                                    f"resolve under {pack.code} principles "
                                    f"({', '.join(pack.conflict_principles)}) — human, not auto"))
    if not any(f.layer == "regional" for f in rep.findings):
        rep.findings.append(Finding("regional", "R0", Level.PASS,
                                    f"regional ({pack.code}) norm theory satisfied"))
    return rep
