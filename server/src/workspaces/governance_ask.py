# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Natural-language ASK over the governance map — a question → a ``View`` (the contract's own
query), resolved deterministically.

The NL layer only PICKS a query; the map ENGINE gives the auditable answer. Two honesty rails:
  * it emits ONLY on-contract facets (reuses ``governance_map.FACETS`` + the map's ``facet_values``),
    so a question can never invent an axis the data doesn't have;
  * ``ask`` ECHOES the View it ran, so a wrong parse is visible and correctable, never silent.

Layer-1 is deterministic keyword→facet mapping (below). An optional LLM proposer can be fenced in
behind the same signature (``llm=``) for questions the keywords miss — off by default, and its
proposal is still validated into a ``View`` (on-contract or nothing).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from . import governance_map as _gm

# synonym phrase → (facet, canonical value). Applied only when the value is actually present in
# the map's facet_values (so we never filter on a value the data lacks). Instrument is exempt
# (names are stable). Direct matches against the map's own vocabulary are added first.
_SYN: dict[str, tuple[str, str]] = {
    "disclaimer": ("demand", "disclosure"), "disclosure": ("demand", "disclosure"),
    "notice": ("demand", "disclosure"),
    "documentation": ("demand", "record"), "record": ("demand", "record"),
    "management system": ("demand", "management_system"), "process": ("demand", "management_system"),
    "assessment": ("demand", "assessment"), "dpia": ("demand", "assessment"),
    "conformity": ("demand", "assessment"),
    "oversight": ("demand", "oversight"), "reviewer": ("demand", "oversight"),
    "technical": ("demand", "technical_measure"), "security": ("demand", "technical_measure"),
    "logging": ("demand", "technical_measure"),
    "appoint": ("demand", "appointment"), "designate": ("demand", "appointment"),
    "representative": ("demand", "appointment"),
    "registration": ("demand", "registration_notification"), "notify": ("demand", "registration_notification"),
    "prohibit": ("demand", "guard"), "prohibited": ("demand", "guard"), "banned": ("demand", "guard"),
    "forbidden": ("demand", "guard"), "guard": ("demand", "guard"),
    "high risk": ("risk", "high-risk"),        # hyphenation normalisation, not an instrument term
}
# NO instrument names here: instruments are matched from the map's OWN facet_values (step 1 in
# parse()) — whatever instruments the data carries are askable, none is baked into the engine.
# Instrument-specific acronyms (gpai, dpia, …) come from `jurisdiction_packs.ask_synonyms()`.
# status buckets (must match governance_map._facet_key('status') outputs) → trigger phrases
_STATUS_SYN: dict[str, tuple[str, ...]] = {
    "interpreter — needs a read": ("need a human", "needs a human", "needs a person", "human",
                                   "review", "ratify", "interpreter", "unread", "escalat"),
    "empty — needs evidence": ("empty", "gap", "gaps", "missing", "no evidence",
                               "not furnished", "unfurnished"),
    "furnished": ("furnished", "covered", "satisfied"),
}


def parse(question: str, *, facet_values: Optional[dict[str, list[str]]] = None,
          llm: Optional[Callable[[str, dict], dict]] = None) -> _gm.View:
    """Deterministically turn a question into a ``View``. Only on-contract facets/values."""
    q = (question or "").lower()
    fv = facet_values or {}
    filters: dict[str, set] = {}

    def add(facet: str, val: str) -> None:
        if facet in _gm.FACETS:
            filters.setdefault(facet, set()).add(val)

    # 1. direct matches against the map's OWN vocabulary (role/risk/demand/instrument/sector/room)
    for facet, values in fv.items():
        for v in values:
            if v and len(v) > 2 and v.lower() in q:
                add(facet, v)
    # 2. synonyms (neutral + registered instrument packs) — only if the value exists in the data
    from . import jurisdiction_packs as _jp
    for syn, (facet, val) in {**_SYN, **_jp.ask_synonyms()}.items():
        if syn in q and (not fv or val in (fv.get(facet) or [])):
            add(facet, val)
    # 3. status buckets
    for bucket, syns in _STATUS_SYN.items():
        if any(s in q for s in syns):
            add("status", bucket)

    group_by = "room"
    m = (re.search(r"group(?:ed)?\s+by\s+(\w+)", q)
         or re.search(r"\bby\s+(role|risk|room|demand|status|instrument)\b", q))
    if m and m.group(1) in _gm.FACETS:
        group_by = m.group(1)
    sort = "az" if re.search(r"a-?z|alphabet", q) else "count" if "most" in q else "gaps"

    if llm and not filters:                          # optional fenced fallback (off by default)
        try:
            return _gm.View.parse(llm(question, {"facets": fv}) or {})
        except Exception:
            pass
    return _gm.View(group_by=group_by, sort=sort,
                    filters={k: sorted(v) for k, v in filters.items()})


def ask(gm: "_gm.GovernanceMap", question: str, *, llm=None) -> dict[str, Any]:
    """Resolve a question against a built map → the contract payload, with the run View echoed
    (auditable) so the user can see and correct what query answered them."""
    view = parse(question, facet_values=gm.facet_values(), llm=llm)
    payload = gm.resolve(view)
    payload["question"] = question
    return payload
