# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Shared chain-replay meters for read-only projections.

``verdict_tally`` counts gate verdicts attributable to one track — by acting
party or by connector pair id — for projections that render a per-track
meter (track_strip, decision_dossier). Events without a verdict do not
count; ``events`` is how many matched at all, so a sparse meter reads zero
instead of borrowing the workspace-wide tally.
"""
from __future__ import annotations

from typing import Any, Optional

from .mutation_log import MutationLog


def verdict_tally(folder_context: str, log_root, *, actor: Optional[str] = None,
                  pair_id: Optional[str] = None) -> dict[str, Any]:
    """Verdict counts over the chain events attributable to one track — by the
    acting party or by the connector's pair id."""
    log = MutationLog(folder_context, log_root=log_root)
    tally: dict[str, int] = {}
    matched = 0
    for evt in log.replay():
        if actor is not None and evt.actor != actor:
            continue
        if pair_id is not None and evt.pair_id != pair_id:
            continue
        matched += 1
        extra = evt.extra or {}
        v = extra.get("verdict") or extra.get("gate_verdict")
        if isinstance(v, str) and v:
            tally[v] = tally.get(v, 0) + 1
    return {"verdicts": tally, "events": matched}
