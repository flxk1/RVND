# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Gate: the language↔enforcement handoff a conformance vector cannot see.

A conformance vector pins that the *engine* emits `reserved` for a quorum/temporal
reservation. It cannot pin that `reserved by 2 of {…}` actually routes to two DISTINCT
approvers, or that `duration 30d : halt` denies on elapse — that linkage lives in RVND's
app layer. This test pins the bridge that connects them.
"""
from __future__ import annotations

from workspaces import loomground_lang as L
from workspaces import reservation_bridge as B
from workspaces.controlforms import G_TWO_APPROVERS, G_PRE_APPROVAL, guarantees


def test_quorum_target_parses_to_m_of_n():
    assert B.parse_target("2 of {legal, finance}") == (2, ["legal", "finance"])
    assert B.parse_target("legal and finance") == (2, ["legal", "finance"])
    assert B.parse_target("legal") == (1, ["legal"])


def test_quorum_routes_to_two_distinct_approvers():
    req = B.reservation_to_request(
        {"kind": "loans", "by": "2 of {legal, finance}"})
    assert req["quorum_m"] == 2
    assert set(req["quorum_set"]) == {"legal", "finance"}
    # the selected control-form carries the DISTINCT-approver guarantee
    assert req["guarantee"] == G_TWO_APPROVERS
    assert G_TWO_APPROVERS in guarantees(req["control_form"])


def test_single_role_is_pre_approval_not_quorum():
    req = B.reservation_to_request({"kind": "loans", "by": "legal"})
    assert req["quorum_m"] == 1
    assert req["guarantee"] == G_PRE_APPROVAL
    assert G_TWO_APPROVERS not in guarantees(req["control_form"])


def test_duration_default_is_halt_deny_on_elapse():
    # no on_elapse declared ⇒ fail-closed default (timeout-is-deny)
    req = B.reservation_to_request({"kind": "loans", "by": "legal", "duration": "30d"})
    assert req["duration"] == "30d"
    assert req["on_elapse"] == B.HALT


def test_proceed_is_carried_explicitly():
    req = B.reservation_to_request(
        {"kind": "loans", "by": "legal", "duration": "30d", "on_elapse": "proceed"})
    assert req["on_elapse"] == B.PROCEED  # fail-open, surfaced — guardrails enforced upstream


def test_end_to_end_loom_reservation_to_request():
    """parse a .lg → take its reservation → bridge it: the full handoff in one hop."""
    patch = L.parse(
        "actor a\n"
        "gate decide risk high grant a\n"
        "reserve loans by 2 of { legal, finance } duration 30d : halt\n"
        "cord a -> decide\n"
        "cord decide -> master\n")
    assert L.validate(patch)["ok"]
    (resv,) = patch["reservations"]
    req = B.reservation_to_request(resv)
    assert req["quorum_m"] == 2 and req["guarantee"] == G_TWO_APPROVERS
    assert req["on_elapse"] == B.HALT and req["duration"] == "30d"
