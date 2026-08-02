# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fact intake — ask the user only what is genuinely unknown for *this* case.

When the pipeline hits a judgment slot it needs facts (the Tatbestand's elements,
the applicability facets). The naive design asks a fixed questionnaire every run —
unbearable for a workflow run a thousand times. This module computes the
**minimal delta form**: of the facts a decision needs, ask only those not already
known.

Two scopes of fact, and that distinction is the whole point:

  * **standing** — bound to the *entity* (the licensee's tax status, the org's
    role, the artist's IPI). Answered once, stored on the entity's SubjectCard,
    and **reused on every later run**. Never re-asked.
  * **per_case** — specific to *this* instance (this delivery's line items, this
    application's date). Supplied per run — ideally by a connector/data, not by a
    human typing — and never pulled from the entity store.

So a high-volume workflow asks: standing facts once (then never), and per-case
facts only when they aren't already supplied by the data feeding the run. The
form shrinks to the real unknowns, often to nothing.

Pure stdlib; the standing store is a `subject_card.SubjectCard` (or any dict).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

try:
    from .subject_card import UNKNOWN          # the "not told us this" sentinel
except Exception:                              # pragma: no cover - decouple for tests
    UNKNOWN = object()


_MISSING = (None, "", UNKNOWN)


@dataclass
class FactNeed:
    key: str
    prompt: str
    scope: str = "standing"          # "standing" (entity-bound, reused) | "per_case"
    required: bool = True
    values: tuple = ()               # allowed values, for a select field (optional)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "prompt": self.prompt, "scope": self.scope,
                "required": self.required, "values": list(self.values)}


@dataclass
class FormSpec:
    questions: list[FactNeed] = field(default_factory=list)   # the genuine unknowns
    prefilled: dict[str, Any] = field(default_factory=dict)   # auto-answered
    provenance: dict[str, str] = field(default_factory=dict)  # key -> "standing" | "this-run"

    @property
    def complete(self) -> bool:
        """No required question left — the run can proceed without asking anything."""
        return not any(q.required for q in self.questions)

    def to_dict(self) -> dict[str, Any]:
        return {"complete": self.complete,
                "questions": [q.to_dict() for q in self.questions],
                "prefilled": self.prefilled, "provenance": self.provenance}


def _has(store: dict, key: str) -> bool:
    return key in store and store[key] not in _MISSING


def build_form(needs: Iterable[FactNeed], *,
               standing: Optional[dict] = None,
               per_case_data: Optional[dict] = None) -> FormSpec:
    """Compute the minimal form.

    standing — the entity's known facts (a SubjectCard's ``facets`` dict, plus any
        workflow defaults the caller merged in). Satisfies *standing* needs.
    per_case_data — this run's supplied data (e.g. fetched from a connector).
        Satisfies *per_case* needs; standing needs may also be satisfied here.
    """
    standing = dict(standing or {})
    per_case_data = dict(per_case_data or {})
    spec = FormSpec()
    for n in needs:
        if n.scope == "standing":
            if _has(standing, n.key):
                spec.prefilled[n.key] = standing[n.key]; spec.provenance[n.key] = "standing"
            elif _has(per_case_data, n.key):
                spec.prefilled[n.key] = per_case_data[n.key]; spec.provenance[n.key] = "this-run"
            else:
                spec.questions.append(n)
        else:  # per_case — never reused from the entity store
            if _has(per_case_data, n.key):
                spec.prefilled[n.key] = per_case_data[n.key]; spec.provenance[n.key] = "this-run"
            else:
                spec.questions.append(n)
    return spec


def record_standing(needs: Iterable[FactNeed], answers: dict,
                    standing: Optional[dict] = None) -> dict:
    """Persist this run's *standing* answers back onto the entity store so they are
    never asked again. Per-case answers are deliberately NOT persisted. Returns the
    updated store (write it back to the SubjectCard)."""
    out = dict(standing or {})
    by_key = {n.key: n for n in needs}
    for key, val in (answers or {}).items():
        n = by_key.get(key)
        if n is not None and n.scope == "standing" and val not in _MISSING:
            out[key] = val
    return out
