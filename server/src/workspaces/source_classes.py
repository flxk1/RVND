# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Universal source-class map — the jurisdiction-AGNOSTIC half of applicable-law
theory.

The norm-theory contract (``norm_contract.py``) reasons about a *single* norm.
This module lifts the same discipline to the *graph of governing sources*: the
question "which sources of law govern these facts, with what force, and in what
order?" Every legal system answers it differently in the *particulars*, but the
*shape* of the answer is universal — and that universal shape belongs in the
substrate (it is an ND map), not in any jurisdiction pack and not in a companion.

What is universal (here):
  * the **taxonomy of source *kinds*** — enacted law, judge-made law,
    international law, soft-law-with-effect. Membership differs by system; the
    kinds do not.
  * the **relation-type vocabulary** between sources — ``member_of``,
    ``incorporates``, ``transposes``, ``presumes_conformity``, ``outranks``,
    ``supersedes``. These are the dimensioned edge types the ND layer emits, one
    level up.
  * the **effect ceilings** — a technical standard can *never* be binding (at
    most it raises a presumption of conformity); soft law is at most
    interpretive. Asserting otherwise is a contract violation in any system.
  * the **incorporation invariant** — a non-self-executing source (a directive,
    a treaty in a dualist system) does not bind the facts until it has an
    incorporation edge. *How* it is incorporated is a pack fact; *that* it must
    be is universal.

What is NOT here (it is pack data, in ``legal_systems.py``): the *ranking* of
classes within a family, which classes a given system self-executes, the
concrete incorporation rule (Art. 59(2) GG, Art. 216(2) TFEU), and membership
facts (DE ∈ EU). What is NOT here either (companion): which classes are *in
scope* for a folder's task.

Pure stdlib, data + checks only. Importable without ``mcp`` or any pack.

Internal by design: auditable knowledge consulted by the validators; no operator surface of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ── Effect: how much legal force a source carries (universal, ordered) ────────

class Effect(Enum):
    """Legal force, weakest→strongest. Comparable by ``.value``."""
    PERSUASIVE = 1     # foreign/secondary material; may inform, does not bind
    INTERPRETIVE = 2   # construes binding law (directive backdrop, soft guidance)
    PRESUMPTION = 3    # raises a presumption (harmonised standard → conformity)
    BINDING = 4        # governs the facts directly

    def __le__(self, other: "Effect") -> bool:
        return self.value <= other.value

    def __lt__(self, other: "Effect") -> bool:
        return self.value < other.value


# ── SourceClass: the universal taxonomy of KINDS of legal source ──────────────

class SourceClass(Enum):
    CONSTITUTION = "constitution"
    NATIONAL_STATUTE = "national_statute"
    NATIONAL_REGULATION = "national_regulation"      # delegated/secondary national
    CASE_LAW = "case_law"
    SUPRANATIONAL_PRIMARY = "supranational_primary"   # TEU/TFEU/Charter
    SUPRANATIONAL_REGULATION = "supranational_regulation"  # directly applicable
    SUPRANATIONAL_DIRECTIVE = "supranational_directive"    # binds via transposition
    INTERNATIONAL_TREATY = "international_treaty"
    CUSTOMARY_INTERNATIONAL = "customary_international"
    TECHNICAL_STANDARD = "technical_standard"         # EN/ISO/IEC/DIN
    SOFT_LAW = "soft_law"                             # guidance, recommendations


# ── Relation-type vocabulary: the universal edge types between sources ─────────

class Relation(Enum):
    MEMBER_OF = "member_of"                  # DE member_of EU
    INCORPORATES = "incorporates"            # a legal order incorporates a treaty
    TRANSPOSES = "transposes"                # national statute transposes a directive
    PRESUMES_CONFORMITY = "presumes_conformity"   # standard → instrument it serves
    OUTRANKS = "outranks"                    # higher source over lower (hierarchy)
    SUPERSEDES = "supersedes"                # later instrument over earlier (time)


VOCABULARY: frozenset = frozenset(r.value for r in Relation)


def is_relation(name: str) -> bool:
    """A relation label is admissible only if it is in the universal vocabulary —
    no ad-hoc edge types leak into the graph."""
    return name in VOCABULARY


# ── Universal class semantics: ceilings + self-execution defaults ─────────────

# The MOST force a class can ever carry, in ANY legal system. A pack may assign a
# weaker effect, never a stronger one. This is where "a standard is not law"
# becomes an enforceable invariant rather than a footnote.
_MAX_EFFECT: dict[SourceClass, Effect] = {
    SourceClass.CONSTITUTION:              Effect.BINDING,
    SourceClass.NATIONAL_STATUTE:          Effect.BINDING,
    SourceClass.NATIONAL_REGULATION:       Effect.BINDING,
    SourceClass.CASE_LAW:                  Effect.BINDING,
    SourceClass.SUPRANATIONAL_PRIMARY:     Effect.BINDING,
    SourceClass.SUPRANATIONAL_REGULATION:  Effect.BINDING,
    SourceClass.SUPRANATIONAL_DIRECTIVE:   Effect.BINDING,   # once transposed / vertical direct effect
    SourceClass.INTERNATIONAL_TREATY:      Effect.BINDING,   # once incorporated
    SourceClass.CUSTOMARY_INTERNATIONAL:   Effect.BINDING,
    SourceClass.TECHNICAL_STANDARD:        Effect.PRESUMPTION,  # ← never binding
    SourceClass.SOFT_LAW:                  Effect.INTERPRETIVE,
}

# Classes that apply to the facts WITHOUT a national incorporation step, by the
# very definition of the class. A pack may ADD to this (e.g. DE self-executes
# customary international law via Art. 25 GG) but the defaults below hold
# everywhere. Directives and treaties are deliberately NOT here: a directive
# binds via transposition; a treaty's self-execution is monist/dualist —
# jurisdiction-dependent, so the pack decides.
_SELF_EXECUTING: frozenset = frozenset({
    SourceClass.CONSTITUTION,
    SourceClass.NATIONAL_STATUTE,
    SourceClass.NATIONAL_REGULATION,
    SourceClass.CASE_LAW,
    SourceClass.SUPRANATIONAL_PRIMARY,
    SourceClass.SUPRANATIONAL_REGULATION,
})


def max_effect(cls: SourceClass) -> Effect:
    """The ceiling on a class's legal force, system-independent."""
    return _MAX_EFFECT[cls]


def self_executes(cls: SourceClass, extra: Optional[frozenset] = None) -> bool:
    """Does this class apply to facts without an incorporation step? Universal
    default, optionally widened by a pack's ``self_executing_extra``."""
    return cls in _SELF_EXECUTING or (extra is not None and cls in extra)


def requires_incorporation(cls: SourceClass, extra: Optional[frozenset] = None) -> bool:
    return not self_executes(cls, extra)


# ── Invariants (documented + enforced) ────────────────────────────────────────

INVARIANTS: tuple[tuple[str, str], ...] = (
    ("SC-1", "Every applicable source carries a known SourceClass and Effect — "
             "no unclassified source enters the applicable set."),
    ("SC-2", "A source's claimed Effect may not exceed its class ceiling "
             "(max_effect). A technical standard is never BINDING; soft law is "
             "never above INTERPRETIVE."),
    ("SC-3", "A non-self-executing source (directive, treaty in a dualist system) "
             "does not bind the facts until it has an incorporation edge "
             "(transposes / incorporates). How it incorporates is a pack fact; "
             "that it must is universal."),
    ("SC-4", "Edges between sources use only the universal Relation vocabulary."),
    ("SC-5", "Cross-class collision escalates and records the governing principle "
             "— it is never auto-resolved (inherits the norm-contract rule)."),
)


@dataclass(frozen=True)
class SourceFinding:
    invariant: str
    level: str            # "violation" | "escalate"
    message: str


def check_source(cls: SourceClass, *, claimed_effect: Effect,
                 has_incorporation_edge: bool = False,
                 self_executing_extra: Optional[frozenset] = None
                 ) -> list[SourceFinding]:
    """Apply SC-2 and SC-3 to one source. Returns findings (empty == clean).

    - SC-2: claimed effect above the class ceiling → violation.
    - SC-3: a non-self-executing source asserted as BINDING with no incorporation
      edge → violation (it cannot bind the facts yet).
    """
    out: list[SourceFinding] = []
    ceiling = max_effect(cls)
    if claimed_effect.value > ceiling.value:
        out.append(SourceFinding(
            "SC-2", "violation",
            f"{cls.value} claimed {claimed_effect.name} but its ceiling is "
            f"{ceiling.name} (a {cls.value} cannot carry more force)."))
    if (claimed_effect is Effect.BINDING
            and requires_incorporation(cls, self_executing_extra)
            and not has_incorporation_edge):
        out.append(SourceFinding(
            "SC-3", "violation",
            f"{cls.value} is not self-executing; asserted BINDING without an "
            f"incorporation edge (transposes/incorporates)."))
    return out


def catalogue() -> dict:
    """Self-describing dump of the universal map (for the ND docs / audit)."""
    return {
        "source_classes": [c.value for c in SourceClass],
        "relations": [r.value for r in Relation],
        "effects": [e.name for e in sorted(Effect, key=lambda e: e.value)],
        "ceilings": {c.value: max_effect(c).name for c in SourceClass},
        "self_executing_default": sorted(c.value for c in _SELF_EXECUTING),
        "invariants": [{"id": i, "text": t} for i, t in INVARIANTS],
    }
