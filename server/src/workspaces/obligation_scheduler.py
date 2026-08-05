# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-norm's obligation scheduler — RVND owns neither.

RVND's parallel obligation scheduler is RETIRED. The deterministic, replayable
``tick`` — deadline resolution, monotone state advancement, the weekend/
public-holiday caveats and jurisdiction-neutral ``deadline_shift`` handling —
now lives in ``loomground-norm`` (``loomground_norm.obligation_scheduler``);
this module re-exports it behind the historical import names
(``ObligationScheduler``, ``SchedulerReport``, ``Proposal``,
``DEFAULT_WARNING_WINDOW``, ``_target_state``) through the ``adapters/norm`` seam.

The plane proposes an abstract ``FollowUp`` and routes it through an injected
``ActionGate``; the seam wires RVND's ``action_gate.gate`` (autonomy grade ×
footprint × standing approvals × posture) and its ``ContractRegistry`` in, and
re-shapes the plane's report into RVND's ``SchedulerReport`` of ``Proposal``
objects carrying the governance ``GateDecision`` — so
``ObligationScheduler(folder, log_root=..., autonomy_grade=..., ...)`` keeps its
historical shape. No sweep logic lives here. Callers are unchanged; this shim is
deleted once they migrate to the plane directly.
"""
from __future__ import annotations

from .adapters.norm import (
    ObligationScheduler,
    SchedulerReport,
    Proposal,
    DEFAULT_WARNING_WINDOW,
    _target_state,
)

__all__ = ["SchedulerReport", "ObligationScheduler", "DEFAULT_WARNING_WINDOW"]
