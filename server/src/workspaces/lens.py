# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The Lens — in vivo oversight (USP 2): governing what an agent *becomes*.

The workflow gates (action_gate) govern what an agent *does*; the Lens governs
what it *learns*. Its principle: **Workspace is a guard, not a teacher.** It computes
no gradients and decides nothing pedagogical; it is a membrane on the learning
stream. Every candidate learning object is collected (mandatory — audit floor);
only pre-agreed classes pass automatically; everything uncovered waits for a
human; forbidden classes are refused (taxonomy §4.6).

Reuses the existing lifecycle verbs verbatim — ``admit`` / ``hold`` / ``reject``
(mutation_log VALID_EVENTS) — so "can I learn that feedback?" resolves to an
admission verdict on a code path that already exists. This module computes the
verdict; the caller writes the LogEvent (same separation as decision_surface).

Four parts:
  * :class:`LearningScope` — what may be internalised: ``allow`` (auto-admit),
    ``aggregate_only`` (admit but never as an individual pair), ``forbid``
    (never — protected attributes, escalated-residual content, Lock-refused).
  * :class:`LearningObject` — one feedback item with provenance.
  * :func:`classify_admission` — admit / hold / reject. **Default-deny**: an
    object whose class is in no list waits (``hold``), never auto-admits.
  * :class:`Precedent` — a recorded human origination that MAY be declared
    learnable; only then may the agent follow it, at a similarity threshold,
    revocable and TTL'd (stare decisis for agents, §4.3).
  * :class:`UpdateBudget` — learning is deliberate drift; cumulative magnitude
    beyond a per-grade cap forces re-gate (§4.4).

Attribution honesty: an action taken under a precedent is stamped
``agent-under-lens(precedent:<id>)`` — never ``user``. The Lens transports the
human's judgment; it never forges the human's signature.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable, Optional, Sequence


class Admission(str, Enum):
    ADMIT = "admit"
    HOLD = "hold"            # default — waits for human review
    REJECT = "reject"        # forbidden class, never learnable


# Classes forbidden by default regardless of scope — the floor the user cannot
# accidentally open. Protected-attribute correlations, the content of escalated
# residuals (the human's reasoning is not training data), Lock-refused material.
_HARD_FORBID = frozenset({
    "protected-attribute", "escalated-residual", "lock-refused",
    "special-category-data",
})


@dataclass
class LearningScope:
    """Declares what an agent may become. The complement of the capability IR
    (which declares what it may *do*). Compiled from law/policy by Oversight ND
    as a fingerprint target — GDPR purpose limitation, AI Act Art. 10 data
    governance compile straight into ``forbid`` / ``aggregate_only``."""
    allow: frozenset[str] = field(default_factory=frozenset)
    aggregate_only: frozenset[str] = field(default_factory=frozenset)
    forbid: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # Hard floor is always forbidden, no matter what the scope says.
        object.__setattr__(self, "forbid",
                           frozenset(self.forbid) | _HARD_FORBID)
        overlap = (set(self.allow) & set(self.forbid)) | \
                  (set(self.aggregate_only) & set(self.forbid))
        if overlap:
            # Forbid wins on conflict — fail safe, but make it visible.
            object.__setattr__(self, "allow",
                               frozenset(self.allow) - self.forbid)
            object.__setattr__(self, "aggregate_only",
                               frozenset(self.aggregate_only) - self.forbid)

    def disposition(self, cls: str) -> Admission:
        if cls in self.forbid:
            return Admission.REJECT
        if cls in self.aggregate_only:
            return Admission.ADMIT          # admitted, but caller must aggregate
        if cls in self.allow:
            return Admission.ADMIT
        return Admission.HOLD               # default-deny

    def to_dict(self) -> dict[str, Any]:
        return {"allow": sorted(self.allow),
                "aggregate_only": sorted(self.aggregate_only),
                "forbid": sorted(self.forbid)}


@dataclass
class LearningObject:
    """One candidate learning object on the stream."""
    cls: str                                # learning-object class
    content_hash: str
    source_actor: str = ""                  # who produced the feedback
    channel_token: str = ""                 # capability token of the channel
    signature: str = ""                     # provenance signature
    confidence: Optional[float] = None
    magnitude: float = 1.0                  # contribution to the update budget
    payload_summary: str = ""

    def has_provenance(self) -> bool:
        """Provenance present = a named source AND a signature. No provenance
        → hold (anti-poisoning, F4): an unsigned teacher never auto-admits."""
        return bool(self.source_actor.strip()) and bool(self.signature.strip())


@dataclass
class AdmissionVerdict:
    admission: Admission
    cls: str
    reason: str
    aggregate_only: bool = False
    triggers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["admission"] = self.admission.value
        return d


def classify_admission(
    obj: LearningObject,
    scope: LearningScope,
    *,
    confidence_floor: float = 0.85,
    known_teachers: Optional[Iterable[str]] = None,
) -> AdmissionVerdict:
    """Decide admit / hold / reject for one learning object.

    Order of refusal (strictest first):
      1. forbidden class                         → REJECT
      2. missing provenance                      → HOLD  (F4 anti-poisoning)
      3. confidence below floor                  → HOLD  (F2)
      4. novel teacher (not in known set)        → HOLD  (F4)
      5. scope disposition (allow/aggregate/—)   → ADMIT or HOLD (default-deny)

    Nothing here teaches; it only decides what is *allowed to stick*."""
    triggers: list[str] = []
    disp = scope.disposition(obj.cls)
    if disp is Admission.REJECT:
        return AdmissionVerdict(Admission.REJECT, obj.cls,
                                f"class {obj.cls!r} is forbidden by scope/floor")

    if not obj.has_provenance():
        triggers.append("no-provenance")
        return AdmissionVerdict(Admission.HOLD, obj.cls,
                                "missing provenance (named source + signature) "
                                "— held for review", triggers=triggers)

    if obj.confidence is not None and obj.confidence < confidence_floor:
        triggers.append(f"confidence {obj.confidence} < {confidence_floor}")
        return AdmissionVerdict(Admission.HOLD, obj.cls,
                                "confidence below floor — held", triggers=triggers)

    if known_teachers is not None and obj.source_actor not in set(known_teachers):
        triggers.append(f"novel teacher {obj.source_actor!r}")
        return AdmissionVerdict(Admission.HOLD, obj.cls,
                                "novel teacher — held for review",
                                triggers=triggers)

    if disp is Admission.HOLD:
        return AdmissionVerdict(Admission.HOLD, obj.cls,
                                f"class {obj.cls!r} not in any scope list — "
                                "default-deny, held for review")

    aggregate = obj.cls in scope.aggregate_only
    return AdmissionVerdict(
        Admission.ADMIT, obj.cls,
        ("admitted (aggregate-only — never store as an individual pair)"
         if aggregate else "admitted (covered by scope)"),
        aggregate_only=aggregate)


@dataclass
class Precedent:
    """A recorded human origination that has been declared learnable. By
    default an origination binds ONE case; only when ``learnable`` is set may
    the agent follow it in matching cases — at ``similarity_threshold``, until
    ``expires_at`` (epoch seconds), revocable. This is the judgment-transport
    mechanism: the residual stays human, but the human's answer may travel."""
    id: str
    query_features: dict[str, Any]
    chosen_option: str
    rationale: str
    actor: str                              # the human who originated
    learnable: bool = False
    similarity_threshold: float = 0.9
    expires_at: Optional[float] = None      # epoch seconds; None = no expiry
    revoked: bool = False

    def applies_to(self, features: dict[str, Any], similarity: float,
                   *, now: Optional[float] = None) -> bool:
        """Whether this precedent may guide a new case. Requires: declared
        learnable, not revoked, not expired, similarity ≥ threshold."""
        if not self.learnable or self.revoked:
            return False
        if self.expires_at is not None:
            import time
            if (now if now is not None else time.time()) >= self.expires_at:
                return False
        return similarity >= self.similarity_threshold

    def actor_stamp(self) -> str:
        """Attribution for an action taken under this precedent. NEVER the
        human's identity — the Lens transports judgment, it does not forge a
        signature (taxonomy §5; the log keeps `agent-under-lens` ≠ `user`)."""
        return f"agent-under-lens(precedent:{self.id})"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_precedent(
    features: dict[str, Any],
    candidates: Sequence[tuple[Precedent, float]],
    *,
    now: Optional[float] = None,
) -> Optional[tuple[Precedent, float]]:
    """Pick the applicable precedent with the highest similarity, or None.

    ``candidates`` is ``[(precedent, similarity), …]`` (similarity computed by
    the caller — the Lens does not own a similarity metric). Creep guard: a
    precedent matching ever-looser cases is the caller's to watch (F4); this
    function only returns precedents at or above their own threshold."""
    applicable = [(p, s) for p, s in candidates
                  if p.applies_to(features, s, now=now)]
    if not applicable:
        return None
    return max(applicable, key=lambda ps: ps[1])


@dataclass
class UpdateBudget:
    """Learning is self-modification; self-modification is deliberate drift.
    Cumulative admitted magnitude per period beyond ``cap`` forces a re-gate
    (a candidate substantial modification — Art. 3(23)). The counter is a
    replay of admitted-learning events, never hand state."""
    cap: float
    spent: float = 0.0

    def __post_init__(self) -> None:
        if self.cap <= 0:
            raise ValueError(f"UpdateBudget.cap must be > 0: {self.cap!r}")

    def would_exceed(self, magnitude: float) -> bool:
        return self.spent + magnitude > self.cap

    def consume(self, magnitude: float) -> bool:
        """Record an admitted update. Returns False (and does NOT spend) when
        the update would cross the cap — the caller must re-gate instead of
        silently learning past the budget."""
        if self.would_exceed(magnitude):
            return False
        self.spent += magnitude
        return True

    @classmethod
    def from_admitted(cls, cap: float,
                      admitted: Iterable[dict[str, Any]]) -> "UpdateBudget":
        """Replay admitted-learning events into a budget counter (deterministic;
        same history ⇒ same counter)."""
        b = cls(cap=cap)
        for ev in admitted:
            mag = ev.get("magnitude", 0.0)
            if isinstance(mag, (int, float)):
                b.spent += float(mag)
        return b
