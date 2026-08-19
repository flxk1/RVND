# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Bridge: a Loomground ``reserve`` declaration → an approvals request spec.

The engine emits a ``reserved`` verdict that carries the reservation's *target* (who, how
many, distinct) and optional *duration* (deadline + on-elapse policy), but it does not act
on them — that is the implementation's job (``approvals.py`` for the distinct-approver count,
``temporal.py`` for the clock). This module is the missing, PURE translation between the two:
``reservation → request spec``. No I/O, no clock, same inputs → same answer. See
the reservation/approval contract.
"""
from __future__ import annotations

import re
from typing import Any

from .controlforms import G_PRE_APPROVAL, G_TWO_APPROVERS

_QUORUM_RE = re.compile(r"^(\d+)\s+of\s+\{(.+)\}$")
_DURATION_RE = re.compile(r"^(\d+)([mhd])$")
_DURATION_UNIT = {"m": 60, "h": 3600, "d": 86400}

# on-elapse policy: halt = deny on elapse (the timeout-is-deny default, fail-closed);
# proceed = auto-act on elapse (fail-OPEN, opt-in, guarded — see the concept).
HALT = "halt"
PROCEED = "proceed"


def duration_to_seconds(duration: str) -> int:
    """Loomground duration token → seconds. ``"30d"`` → 2592000, ``"2h"`` → 7200.
    The grammar's duration is ``number ("m"|"h"|"d")`` (SYNTAX §3)."""
    m = _DURATION_RE.match((duration or "").strip())
    if not m:
        raise ValueError(f"bad duration {duration!r} (expected e.g. 30d, 2h, 15m)")
    return int(m.group(1)) * _DURATION_UNIT[m.group(2)]


def parse_target(by: str) -> tuple[int, list[str]]:
    """Reservation target → (quorum_m, roles).

    ``"2 of {legal, finance}"`` → ``(2, ["legal", "finance"])``
    ``"legal and finance"``      → ``(2, ["legal", "finance"])``
    ``"legal"``                  → ``(1, ["legal"])``
    """
    by = (by or "").strip()
    m = _QUORUM_RE.match(by)
    if m:
        roles = [r.strip() for r in m.group(2).split(",") if r.strip()]
        return int(m.group(1)), roles
    if " and " in by:
        roles = [r.strip() for r in by.split(" and ") if r.strip()]
        return len(roles), roles
    return (1, [by]) if by else (0, [])


def reservation_to_request(reservation: dict[str, Any]) -> dict[str, Any]:
    """Map a parsed Loomground reservation to an approvals request spec.

    ``reservation`` is the engine's parsed form: ``{kind, by, when?, duration?, on_elapse?}``.
    The returned spec selects the existing control-form and carries the quorum + elapse
    policy the approval layer enforces — it adds no new trust rule (distinctness, "an agent
    never counts", "the requester's own hand never counts", "the kill switch retro-invalidates"
    all stay in ``approvals.py``)."""
    m, roles = parse_target(reservation.get("by", ""))
    two_or_more = m >= 2
    on_elapse = reservation.get("on_elapse", HALT)
    # Defense in depth (mirrors the patchbay's requestSignoff guard): a reservation that
    # exists BECAUSE the law requires a human can never time out INTO action — force halt,
    # whatever it was authored as. `basis_kind` is an RVND annotation (the language is
    # role-based and carries no basis); when it is "law", proceed is downgraded to halt so
    # the guarantee holds on every path into the bridge, not only the UI one.
    if reservation.get("basis_kind") == "law" and on_elapse == PROCEED:
        on_elapse = HALT
    return {
        "kind": reservation.get("kind"),
        "control_form": "four_eyes" if two_or_more else "single_approver",
        "guarantee": G_TWO_APPROVERS if two_or_more else G_PRE_APPROVAL,
        "competence": roles,        # the routed competence(s) an approver must hold
        "quorum_m": m,              # distinct counting hands required
        "quorum_set": roles,        # drawn from this set
        "duration": reservation.get("duration"),      # None ⇒ no deadline
        "on_elapse": on_elapse,     # halt = deny on elapse; proceed = fail-open (never for law)
    }
