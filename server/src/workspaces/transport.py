# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""transport — the Loomground transport primitive + reference patch evaluator.

This is the piece that makes Loomground a *language you run*, not just a netlist
you validate: one ``transport_bang`` evaluates an ENTIRE patch (every wired
use-case), not a single node, and resolves the master over all egress paths.

Layering (DECISION_2026-06-14): the **evaluation semantics** (how a whole patch
reduces to verdicts, and what the master does with them) and the **transport
primitive** (a run originates from ONE auditable trigger — nothing self-starts)
are *Loomground*: every conformant implementation must produce the same result,
which the conformance vectors pin. The concrete SCHEDULER (cron, frequency, the
grid UI) is *Rvnd*. This module is the reference semantics, deliberately small,
stdlib-only; it reuses ``operate`` (per-node disposition) and ``governance_graph``
(verdict projection) and adds only the trigger + the master fan-in.
"""
from __future__ import annotations

from typing import Any, Optional

from .governance_graph import governance_graph
from .operations import _journal, operate

# The master rule: a path reaches the world IFF its verdict is `auto`. The master
# never attenuates — it ACTS or WITHHOLDS. reserved/refused/human all withhold;
# reserved/refused withhold permanently, `human` withholds pending the human act.
_MASTER_ACTS = {"auto"}


def _split(node_id: str) -> str:
    return node_id.split(":", 1)[1] if ":" in node_id else node_id


def transport_bang(
    folder_context: str,
    inputs: Optional[dict[str, list[dict[str, Any]]]] = None,
    *,
    now_epoch: int = 0,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Fire the whole patch once.

    ``inputs`` maps ``use_case_id -> [issue dicts]`` (an issue is
    ``{issue_id, issue_type, completeness?}``). For every use-case with an egress
    cord to the master and an authority cord from an agent, we evaluate it with
    ``operate``; then the master decides each egress path. Returns the canonical
    transport run; every step is on the tape (TransportFired, the per-node run
    events, and one MasterDecision per path)."""
    inputs = inputs or {}
    g = governance_graph(folder_context, log_root=log_root)

    egress_ucs = sorted({_split(e["from"]) for e in g["edges"]
                         if e["kind"] == "egress"})
    agent_for: dict[str, str] = {}
    for e in g["edges"]:
        if e["kind"] == "authority":
            agent_for.setdefault(_split(e["to"]), _split(e["from"]))

    # The single auditable trigger. Nothing in the patch self-starts; this bang
    # is the only origin of a run.
    bang_id = _journal(folder_context, "transport", log_root, {
        "kind": "TransportFired", "now_epoch": now_epoch,
        "use_cases": egress_ucs})

    # Evaluate every wired use-case (the multi-node part). Unwired use-cases
    # (no authority agent) cannot run — the master will see them as unfired.
    for uc in egress_ucs:
        agent = agent_for.get(uc)
        if agent is None:
            continue
        operate(folder_context, use_case_id=uc, agent_id=agent,
                issues=inputs.get(uc, []), now_epoch=now_epoch, log_root=log_root)

    # Read resolved verdicts back from the tape (single source of truth), then
    # the master decides per path and records each decision.
    g2 = governance_graph(folder_context, log_root=log_root)
    egress = {_split(e["from"]): e for e in g2["edges"] if e["kind"] == "egress"}

    results: dict[str, dict[str, str]] = {}
    acted: list[str] = []
    withheld: list[dict[str, str]] = []
    for uc in egress_ucs:
        verdict = egress.get(uc, {}).get("verdict", "unfired")
        decision = "act" if verdict in _MASTER_ACTS else "withhold"
        _journal(folder_context, "master", log_root, {
            "kind": "MasterDecision", "transport_id": bang_id,
            "use_case_id": uc, "verdict": verdict, "decision": decision})
        results[uc] = {"verdict": verdict, "master": decision}
        if decision == "act":
            acted.append(uc)
        else:
            withheld.append({"use_case": uc, "verdict": verdict})

    return {"transport_id": bang_id, "results": results,
            "acted": sorted(acted),
            "withheld": sorted(withheld, key=lambda r: r["use_case"])}
