# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Universal governance chat — ONE input, routed to the right operation.

Not three UIs; one box. ``chat(text)`` classifies intent (``intent_router``) and dispatches to
the existing op — a policy document is ingested, a self-description fills the subject card, a
question is answered from the map — returning ``{intent, echo, kind, result}``. The inferred
intent is echoed so a wrong guess is visible and correctable (same fence as everywhere: the
router proposes, you can override, each op stays deterministic + audited).

Reuse only — it wires `policy_ingest` · `use_case_intake` · `governance_map` under one router; it
adds no new operation of its own.
"""
from __future__ import annotations

from typing import Any, Optional, Callable

from . import intent_router as _ir


def chat(text: str, *, policy_text: str = "", instrument: str = "policy",
         intent: Optional[str] = None, folder: Optional[str] = None,
         llm: Optional[Callable[[str, dict], dict]] = None) -> dict[str, Any]:
    """Route one input and run the matching op. ``intent`` overrides the router (the user
    correcting a wrong guess). ``policy_text`` is the session's accumulated rules — the corpus a
    question is answered against. ``folder`` is the workspace a policy's norms are placed in."""
    routed = _ir.route(text, llm=llm)
    chosen = intent if intent in _ir.OPS else routed["intent"]

    if chosen == "policy":
        # The rule graph is the policy pipeline: prose → norms on the map →
        # reasoning + variety ledger. Same twin shape the front door renders.
        from . import policy_resolve
        result, kind = policy_resolve.resolve(text, folder=folder), "twin"
    elif chosen == "intake":
        from . import use_case_intake
        # capture-first: the description lands on a neutral subject card; facets narrow later.
        card = use_case_intake.blank(description=text)
        result = card.to_dict()
        result["completeness"] = card.completeness()
        result["unknown_facets"] = use_case_intake.unknown_facets(card)
        kind = "card"
    else:  # ask
        from . import governance_map
        result, kind = governance_map.serve(question=text, policy_text=policy_text,
                                             instrument=instrument), "map"

    return {"intent": chosen, "echo": f"inferred: {chosen}" if intent is None else f"you chose: {chosen}",
            "why": routed["why"], "kind": kind, "result": result}
