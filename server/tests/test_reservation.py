# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Reserved-act routing for legal and policy reservations.

The tests cover determinate issues that still owe a human act, who/what/why
metadata, mandatory legal reservations, additive policy reservations, no-op
free issues, deduplication, and determinism.
"""
from __future__ import annotations


from rvnd.reservation import (
    LEGAL_RESERVATIONS, reserved_acts_for,
)


def test_determinate_issue_with_reservation_still_routes_a_human():
    acts = reserved_acts_for(["co_determination"])
    assert len(acts) == 1
    # the analysis can be fully determinate; the works-council act is still owed
    assert acts[0]["act_type"] == "co-determine"


def test_reserved_act_names_who_what_why():
    act = reserved_acts_for(["ai_high_risk"])[0]
    assert act["reserved_to"]                       # WHO (a competence)
    assert act["act_type"] in ("sign", "approve", "authorize",
                               "co-determine", "review")          # WHAT
    assert act["basis_kind"] == "law"                              # WHY kind
    assert act["source"]                            # WHY citation


def test_legal_mandatory_policy_additive():
    base = reserved_acts_for(["legal_conclusion"])
    assert any(a["basis_kind"] == "law" or a["basis_kind"] == "professional"
               for a in base)
    # a company adds its own policy reservation on an otherwise-free issue
    with_policy = reserved_acts_for(
        ["pricing_change"],
        policy_reservations={"pricing_change": {
            "reserved_to": "finance-director", "act_type": "approve",
            "source": "internal policy FIN-03"}})
    assert len(with_policy) == 1
    assert with_policy[0]["basis_kind"] == "policy"
    # without the policy, the same issue is free — machine just does it
    assert reserved_acts_for(["pricing_change"]) == []


def test_free_issue_has_no_human_gate():
    assert reserved_acts_for(["formatting_fix"]) == []


def test_multiple_issues_dedup():
    acts = reserved_acts_for(["ai_high_risk", "co_determination",
                              "ai_high_risk"])
    triggers = [(a["trigger"], a["act_type"]) for a in acts]
    assert len(triggers) == len(set(triggers))      # no duplicates
    assert len(acts) == 2


def test_deterministic():
    a = reserved_acts_for(["ai_high_risk", "co_determination"])
    b = reserved_acts_for(["ai_high_risk", "co_determination"])
    assert a == b


def test_registry_covers_the_canonical_reservations():
    # the reserved acts a regulated company actually owes
    for itype in ("ai_high_risk", "automated_decision", "co_determination",
                  "data_transfer", "legal_conclusion"):
        assert itype in LEGAL_RESERVATIONS
