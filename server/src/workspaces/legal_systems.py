# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Legal-system meta-layer — the switchable jurisdiction-family packs.

Between the domain-agnostic substrate (BM25, the 5D graph, the currency
pipeline, the gate runner) and a domain vertical (GDPR, the AI Act, music
rights) sits a layer the substrate owns but selects at runtime: the **legal
system**, i.e. the jurisdiction *family*. German civil law, UK/US common law and
EU supranational law share a domain-agnostic engine but differ in their
*meta-rules*:

  * the authority hierarchy (Normenhierarchie / constitutional supremacy),
  * the conflict-resolution principles (lex superior/specialis/posterior vs
    stare decisis / implied repeal),
  * citation forms,
  * temporal conventions (Fassung vs "as amended"; entry-into-force vs
    application),
  * the legal-equivalence vocabulary used for query expansion.

These are universal *within a family* and belong in the substrate — but as a
**switch**, not hard-coded. A folder (via its companion preset) selects a
``legal_system``; the substrate's generic mechanisms (retriever, contract,
authority ranking, conflict handling) read the active pack instead of assuming
German law. A vertical adds only domain content on top.

Invariant preserved: the contract still **records** a family's conflict
principles but never *auto-applies* them — ship the law, escalate the
collision. The pack tells the substrate which principles are even recognised in
that family; resolution stays with the human.

Pure stdlib; data only. Seeded: DE and EU in full; UK and US with the
structural meta-rules + English-law vocabulary (extend as needed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .source_classes import (Effect, Relation, SourceClass, max_effect,
                             self_executes, requires_incorporation)


@dataclass(frozen=True)
class LegalSystem:
    code: str                              # "DE" / "EU" / "UK" / "US"
    name: str
    family: str                            # "civil" | "common" | "supranational"
    language: str                          # ISO 639-1 of the rule_extractor profile
    # Authority hierarchy, highest first; index+1 is the rank a pair inherits
    # when its source type is identified (lower number = higher authority).
    authority_hierarchy: tuple[str, ...] = ()
    # Conflict-resolution principles the family recognises, in priority order.
    # RECORDED as provenance by the contract; NEVER auto-applied (escalate).
    conflict_principles: tuple[str, ...] = ()
    # Citation-form hints (substrings/markers) used to recognise a real cite.
    citation_markers: tuple[str, ...] = ()
    # Temporal-model conventions.
    version_label: str = "version"
    distinguishes_force_from_application: bool = True
    # Legal-equivalence vocabulary for query expansion: each cluster is a set of
    # near-equivalent operative terms a jurist treats as the same hook.
    equivalence_clusters: tuple[frozenset, ...] = ()
    # ── Applicable-source instances (pack data the universal map does NOT hold) ──
    # Supranational orders this system is a member of → their directly-applicable
    # law also governs. THIS is the datum "Germany is in the EU".
    supranational_overlay: tuple[str, ...] = ()
    # This family's ranking of source CLASSES (national spine; the cross-system
    # primacy edge is expressed as a Relation, not by interleaving — we do not
    # silently resolve EU-primacy vs constitutional-identity review).
    class_rank: tuple[SourceClass, ...] = ()
    # Classes this system self-executes BEYOND the universal default (e.g. DE
    # customary international law via Art. 25 GG).
    self_executing_extra: frozenset = frozenset()
    # Concrete incorporation rule per class: (class, human-readable rule). The
    # universal map says incorporation is REQUIRED; the pack says HOW.
    incorporation: tuple[tuple[SourceClass, str], ...] = ()

    def incorporation_rule(self, cls: SourceClass) -> Optional[str]:
        for c, note in self.incorporation:
            if c is cls:
                return note
        return None

    def authority_rank(self, source_type: str) -> int:
        """Rank a source type within this family's hierarchy (1 = highest).
        Unknown types get the weakest rank + 1 (never silently top-ranked)."""
        st = (source_type or "").strip().lower()
        for i, tier in enumerate(self.authority_hierarchy):
            if tier.lower() in st or st in tier.lower():
                return i + 1
        return len(self.authority_hierarchy) + 1


# ── Packs ───────────────────────────────────────────────────────────────────

_DE_CLUSTERS = (
    frozenset({"erlassen", "erlass", "absehen", "abgesehen", "abzusehen", "verzicht", "verzichten"}),
    frozenset({"härtefall", "härte", "unbillig", "unbilligkeit", "einzelfall", "besondere", "ausnahme"}),
    frozenset({"rückforderung", "rückzahlung", "einziehung", "erstattung"}),
    frozenset({"behörde", "verwaltung", "amt", "stelle"}),
    frozenset({"darf", "dürfen", "kann", "können", "erlaubt", "zulässig", "befugt"}),
    frozenset({"muss", "müssen", "verpflichtet", "pflicht", "hat"}),
    frozenset({"löschen", "löschung", "entfernen", "beseitigen"}),
    frozenset({"daten", "personenbezogen", "information"}),
)

_EN_CLUSTERS = (
    frozenset({"waive", "waiver", "dispense", "remit", "forgo", "refrain", "abstain"}),
    frozenset({"hardship", "undue", "unreasonable", "unconscionable", "exceptional", "inequitable", "unfair"}),
    frozenset({"recover", "recovery", "reclaim", "clawback", "repayment", "restitution"}),
    frozenset({"authority", "agency", "body", "office", "regulator"}),
    frozenset({"may", "can", "permitted", "entitled", "empowered", "discretion"}),
    frozenset({"shall", "must", "required", "obliged", "duty"}),
    frozenset({"erase", "erasure", "delete", "remove"}),
    frozenset({"data", "personal", "information"}),
)

_REGISTRY: dict[str, LegalSystem] = {
    "DE": LegalSystem(
        code="DE", name="German law", family="civil", language="de",
        authority_hierarchy=("Grundgesetz", "Bundesgesetz", "Rechtsverordnung",
                             "Satzung", "Verwaltungsvorschrift", "Rechtsprechung"),
        conflict_principles=("lex-superior", "lex-specialis", "lex-posterior"),
        citation_markers=("§", "Art.", "BGBl", "CELEX", "Rn."),
        version_label="Fassung",
        equivalence_clusters=_DE_CLUSTERS,
        supranational_overlay=("EU",),
        class_rank=(SourceClass.CONSTITUTION, SourceClass.NATIONAL_STATUTE,
                    SourceClass.NATIONAL_REGULATION, SourceClass.CASE_LAW),
        # Art. 25 GG: general rules of public international law are federal law,
        # self-executing, ranking above ordinary statute.
        self_executing_extra=frozenset({SourceClass.CUSTOMARY_INTERNATIONAL}),
        incorporation=(
            (SourceClass.INTERNATIONAL_TREATY,
             "Art. 59(2) GG: transformed by federal consent statute → rank of Bundesgesetz."),
            (SourceClass.CUSTOMARY_INTERNATIONAL,
             "Art. 25 GG: general rules are federal law, self-executing, above statute."),
            (SourceClass.SUPRANATIONAL_DIRECTIVE,
             "Binds via national transposition; operative text is the transposing statute (e.g. BDSG, NIS2-Umsetzung)."),
            (SourceClass.TECHNICAL_STANDARD,
             "Non-binding; a harmonised standard raises a presumption of conformity with the instrument it serves."),
        )),
    "EU": LegalSystem(
        code="EU", name="EU law", family="supranational", language="en",
        authority_hierarchy=("Primary law (TEU/TFEU/Charter)", "Regulation",
                             "Directive", "Delegated act", "Implementing act",
                             "CJEU case law"),
        conflict_principles=("primacy", "lex-specialis", "lex-posterior"),
        citation_markers=("CELEX", "Art.", "Recital", "OJ", "C-"),
        version_label="consolidated version",
        equivalence_clusters=_DE_CLUSTERS + _EN_CLUSTERS,
        class_rank=(SourceClass.SUPRANATIONAL_PRIMARY,
                    SourceClass.SUPRANATIONAL_REGULATION,
                    SourceClass.SUPRANATIONAL_DIRECTIVE, SourceClass.CASE_LAW),
        incorporation=(
            (SourceClass.INTERNATIONAL_TREATY,
             "Art. 216(2) TFEU: agreements concluded by the Union bind the institutions and the Member States; rank between primary and secondary law."),
            (SourceClass.SUPRANATIONAL_DIRECTIVE,
             "Art. 288 TFEU: binds Member States as to the result; transposition required; direct effect only vertically after the deadline."),
            (SourceClass.TECHNICAL_STANDARD,
             "Reg. 1025/2012: a harmonised standard cited in the OJ raises a presumption of conformity (e.g. AI Act, CRA); it is not binding law."),
        )),
    "UK": LegalSystem(
        code="UK", name="UK law", family="common", language="en",
        authority_hierarchy=("Constitutional statute", "Act of Parliament",
                             "Statutory Instrument", "Supreme Court precedent",
                             "Court of Appeal precedent", "High Court precedent"),
        conflict_principles=("parliamentary-sovereignty", "stare-decisis",
                             "implied-repeal", "generalia-specialibus-non-derogant"),
        citation_markers=("s.", "SI ", "UKSC", "EWCA", "EWHC", "[20"),
        version_label="as amended",
        equivalence_clusters=_EN_CLUSTERS,
        class_rank=(SourceClass.CONSTITUTION, SourceClass.NATIONAL_STATUTE,
                    SourceClass.NATIONAL_REGULATION, SourceClass.CASE_LAW),
        # Dualist: a treaty has no domestic effect without an incorporating Act.
        incorporation=(
            (SourceClass.INTERNATIONAL_TREATY,
             "Dualist: no domestic effect without an incorporating Act of Parliament."),
            (SourceClass.TECHNICAL_STANDARD,
             "Non-binding; may evidence reasonable practice / presumption under the relevant regime."),
        )),
    "US": LegalSystem(
        code="US", name="US federal law", family="common", language="en",
        authority_hierarchy=("US Constitution", "Federal statute (U.S.C.)",
                             "Federal regulation (C.F.R.)", "Supreme Court precedent",
                             "Circuit precedent", "District precedent"),
        conflict_principles=("constitutional-supremacy", "stare-decisis",
                             "implied-repeal", "lex-specialis"),
        citation_markers=("U.S.C.", "C.F.R.", "Pub. L.", "U.S.", "F.3d", "§"),
        version_label="as amended",
        equivalence_clusters=_EN_CLUSTERS,
        class_rank=(SourceClass.CONSTITUTION, SourceClass.NATIONAL_STATUTE,
                    SourceClass.NATIONAL_REGULATION, SourceClass.CASE_LAW),
        incorporation=(
            (SourceClass.INTERNATIONAL_TREATY,
             "Art. VI supremacy; self-executing vs non-self-executing treaty distinction (Medellín)."),
            (SourceClass.TECHNICAL_STANDARD,
             "Non-binding; may be incorporated by reference into a binding regulation."),
        )),
}

DEFAULT = "DE"


def get(code: Optional[str] = None) -> LegalSystem:
    """Return the pack for ``code`` (default DE). Unknown code → KeyError, so a
    typo never silently falls back to the wrong legal system."""
    code = (code or DEFAULT).upper()
    if code not in _REGISTRY:
        raise KeyError(f"unknown legal system {code!r}; available: {available()}")
    return _REGISTRY[code]


def available() -> list[str]:
    return sorted(_REGISTRY)


def register(system: LegalSystem) -> None:
    """Add or override a pack (e.g. a vertical contributing FR/IT)."""
    _REGISTRY[system.code.upper()] = system


# ── Applicable-law resolver: expand a selection into the full source set ──────

@dataclass(frozen=True)
class SourceEntry:
    """One governing source class for the active selection, with its force and
    incorporation rule resolved from the owning pack."""
    source_class: SourceClass
    origin: str                 # legal-system code that contributes this class
    effect: Effect              # the class ceiling in this family (pack ≤ universal)
    rank_in_origin: int         # 1 = highest within its own system
    self_executing: bool
    incorporation_rule: Optional[str]

    def to_dict(self) -> dict:
        return {"source_class": self.source_class.value, "origin": self.origin,
                "effect": self.effect.name, "rank_in_origin": self.rank_in_origin,
                "self_executing": self.self_executing,
                "incorporation_rule": self.incorporation_rule}


@dataclass(frozen=True)
class SourceRelation:
    subject: str
    relation: Relation
    object: str
    note: str = ""

    def to_dict(self) -> dict:
        return {"subject": self.subject, "relation": self.relation.value,
                "object": self.object, "note": self.note}


@dataclass(frozen=True)
class ApplicableLaw:
    """The full set of source classes that govern facts under a selection,
    plus the cross-system relations (membership, primacy). Conflicts between
    members are NOT resolved here — they escalate (norm-contract SC-5)."""
    systems: tuple[str, ...]
    sources: tuple[SourceEntry, ...]
    relations: tuple[SourceRelation, ...]

    def to_dict(self) -> dict:
        return {"systems": list(self.systems),
                "sources": [s.to_dict() for s in self.sources],
                "relations": [r.to_dict() for r in self.relations]}


def applicable_systems(code: Optional[str] = None) -> list[str]:
    """Closure of a jurisdiction selection over its supranational overlays.
    ``applicable_systems("DE") == ["DE", "EU"]`` — this is where 'Germany is in
    the EU' turns a single selection into the real applicable set."""
    seen: list[str] = []
    stack = [(code or DEFAULT).upper()]
    while stack:
        c = stack.pop(0)
        if c in seen or c not in _REGISTRY:
            continue
        seen.append(c)
        stack.extend(_REGISTRY[c].supranational_overlay)
    return seen


def applicable_law(code: Optional[str] = None,
                   in_scope: Optional[set] = None) -> ApplicableLaw:
    """Assemble every governing source class for a selection.

    - expands the selection over supranational overlays (DE → DE+EU);
    - for each system, lists its ranked source classes with effect ceiling,
      self-execution, and incorporation rule;
    - records the cross-system relations: ``member_of`` and the supranational
      ``OUTRANKS`` (primacy) edge — annotated, never auto-resolved;
    - if ``in_scope`` (a set of SourceClass) is given, filters to those classes.
      Scoping is the *companion's* call; the resolver only honours the filter.
    """
    systems = applicable_systems(code)
    sources: list[SourceEntry] = []
    relations: list[SourceRelation] = []

    for sys_code in systems:
        sys = _REGISTRY[sys_code]
        for i, cls in enumerate(sys.class_rank):
            if in_scope is not None and cls not in in_scope:
                continue
            sources.append(SourceEntry(
                source_class=cls, origin=sys_code, effect=max_effect(cls),
                rank_in_origin=i + 1,
                self_executing=self_executes(cls, sys.self_executing_extra),
                incorporation_rule=sys.incorporation_rule(cls)))

    primary = (code or DEFAULT).upper()
    for sys_code in systems:
        if sys_code == primary:
            continue
        parent = _REGISTRY[sys_code]
        relations.append(SourceRelation(primary, Relation.MEMBER_OF, sys_code,
                                         "directly-applicable law of the overlay also governs"))
        if parent.family == "supranational":
            relations.append(SourceRelation(
                sys_code, Relation.OUTRANKS, primary,
                "EU primacy over conflicting national law (reservation: "
                "constitutional-identity / ultra-vires review, BVerfG) — collision escalates, not auto-resolved"))

    return ApplicableLaw(tuple(systems), tuple(sources), tuple(relations))
