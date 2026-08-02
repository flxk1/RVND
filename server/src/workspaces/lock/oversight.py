# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Six-level oversight dial.

Inherited from Federation Protocol 36 (human agency / undo). Per-operation
publisher-floor + per-deployment user-default. Effective level = strictest
of (user_default, op_floor).

The authoritative human-readable documentation for this dial lives at
``Tools/oversight/SIX-LEVEL-DIAL.md``. The interactive-prompt UX is at
``Tools/oversight/INTERACTIVE-CALLBACK.md``. This module is the runtime
implementation; the docs are where to look for the rationale and intended
use across the Workspace stack.

| # | Level | Cell behaviour | When user is asked |
|---|---|---|---|
| 1 | autonomous | Plans + executes silently | Never |
| 2 | notify     | Executes; surfaces summary | Post-execution |
| 3 | review     | Executes; waits before final | Before authoritative |
| 4 | approve    | Halts before side-effect op | Before execution |
| 5 | supervised | Step-by-step at every node | At every node |
| 6 | manual     | Suggests; user executes | Cell does not execute |
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class OversightLevel(IntEnum):
    """Six-level oversight dial. Lower = more autonomous, higher = more user-in-the-loop."""

    AUTONOMOUS = 1
    NOTIFY = 2
    REVIEW = 3
    APPROVE = 4
    SUPERVISED = 5
    MANUAL = 6

    @property
    def label(self) -> str:
        return self.name.lower()

    @property
    def description(self) -> str:
        return _DESCRIPTIONS[self]


_DESCRIPTIONS = {
    OversightLevel.AUTONOMOUS: "Plans + executes silently; user sees result only",
    OversightLevel.NOTIFY: "Executes; surfaces a notification with summary",
    OversightLevel.REVIEW: "Executes; waits before result is final until user reviews",
    OversightLevel.APPROVE: "Halts before any side-effect op; resumes on approval",
    OversightLevel.SUPERVISED: "Step-by-step; user confirms at every finding/decision",
    OversightLevel.MANUAL: "Cell suggests, user executes manually; Cell does not execute",
}


# Default per privacy class.
PRIVACY_CLASS_DEFAULTS = {
    "public": OversightLevel.NOTIFY,
    "pseudonymous": OversightLevel.REVIEW,
    "sensitive": OversightLevel.APPROVE,
    "regulated": OversightLevel.SUPERVISED,
}


# Map agent-tool-lock Mode to oversight level (rough correspondence).
# This is for backwards compatibility — Mode and OversightLevel coexist.
MODE_TO_OVERSIGHT = {
    "audit_only": OversightLevel.NOTIFY,
    "permissive": OversightLevel.NOTIFY,
    "standard": OversightLevel.APPROVE,
    "strict": OversightLevel.SUPERVISED,
}


@dataclass
class OversightDecision:
    """One user decision on one finding under the dial."""

    finding_id: str
    user_action: str  # "accept" | "reject" | "edit" | "waive" | "skip"
    reason: str = ""
    elapsed_ms: int = 0


def effective_level(
    *,
    user_default: OversightLevel,
    op_floor: OversightLevel | None = None,
    privacy_class: str | None = None,
) -> OversightLevel:
    """Compute effective oversight level. STRICTEST wins.

    - user_default: what the deployer requested
    - op_floor: minimum the operation publisher requires (publisher-floored)
    - privacy_class: data classification — looks up its default minimum

    User can override to a STRICTER level. Cannot override to a LESS strict
    level than the op or privacy class warrants (regulated ops cannot be
    autonomous, even if the user asks).
    """
    candidates = [user_default]
    if op_floor is not None:
        candidates.append(op_floor)
    if privacy_class in PRIVACY_CLASS_DEFAULTS:
        candidates.append(PRIVACY_CLASS_DEFAULTS[privacy_class])
    return max(candidates)


def asks_user_per_finding(level: OversightLevel) -> bool:
    """Returns True if the level requires per-finding user interaction."""
    return level in (OversightLevel.SUPERVISED, OversightLevel.MANUAL)


def asks_user_per_plan(level: OversightLevel) -> bool:
    """Returns True if the level requires plan-level user approval before execution."""
    return level in (OversightLevel.APPROVE, OversightLevel.SUPERVISED, OversightLevel.MANUAL)


def waits_for_review_after_execution(level: OversightLevel) -> bool:
    """Returns True if the level executes but holds the result for user review."""
    return level == OversightLevel.REVIEW


def notifies_user_post_execution(level: OversightLevel) -> bool:
    """Returns True if the level notifies the user post-execution (but doesn't wait)."""
    return level == OversightLevel.NOTIFY
