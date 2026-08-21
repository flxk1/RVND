# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The norm-theory contract, wired into the dispatch seam, actually forces.

dispatch(enforce_contract=True) runs every emitted pair through the contract.
Today's NDs do not yet attach source/temporal/applicability/jurisdiction, so a
class-C dispatch is REFUSED — which is the point: an ND is not 'done' until its
output clears the gate. A conforming ND passes and its escalations surface.
"""

from __future__ import annotations

import pytest

from rvnd.nd_routing import NDRouter, DefaultClassifier
from rvnd.norm_contract import ContractViolation


class _ConformingND:
    nd_id = "conforming"
    handles_types = ("normative", "unknown")

    def can_handle(self, classification):
        return True

    def extract(self, content, classification, *, source_document=None):
        return [{
            "id": "c1",
            "problem": {"id": "c1-p", "type": "rule", "facets": {
                "domain": "ai-act", "subject": "provider", "modal": "muss",
                "modal_phrase": "muss sicherstellen", "has_exception": False,
                "applicability": {"role": "provider"}, "jurisdiction": ["EU"]}},
            "solution": {"id": "c1", "problem_id": "c1-p",
                "body": "Der Anbieter muss ein Risikomanagementsystem einrichten.",
                "authority_tier": 1, "confidence": 0.93,
                "source": "CELEX:32024R1689 Art. 9",
                "temporal": {"status": "in-force", "in_force_from": "2026-08-02",
                             "date_source": "registry"}},
            "edges": [],
        }]


def _classify():
    # Route to AIActRuleND. The domain NDs are facet-gated (handles_facets =
    # ["ai-act"], handles_types = []) to avoid the ×4 over-fire where every
    # domain ND claimed every normative doc; so the classification must carry
    # the ai-act facet for the AI-Act ND to fire. The text below is Art. 9
    # risk-management language — genuinely ai-act content.
    c = DefaultClassifier().classify(
        "Der Anbieter muss ein Risikomanagementsystem einrichten.", file_path="x.txt")
    if "ai-act" not in c.facets:
        c.facets = [*c.facets, "ai-act"]
    return c


def test_dispatch_without_enforcement_is_unchanged():
    from rvnd.domain_nds import AIActRuleND
    r = NDRouter(); r.register(AIActRuleND())
    res = r.dispatch("The provider shall establish a risk management system.", _classify())
    assert res.contract_report is None        # opt-in: default off, nothing changes


def test_dispatch_refuses_noncompliant_nd_output_class_c():
    from rvnd.domain_nds import AIActRuleND
    r = NDRouter(); r.register(AIActRuleND())
    with pytest.raises(ContractViolation):
        r.dispatch("The provider shall establish a risk management system.",
                   _classify(), enforce_contract=True, risk_class="C")


def test_dispatch_passes_conforming_nd_and_carries_report():
    r = NDRouter(); r.register(_ConformingND())
    res = r.dispatch("Der Anbieter muss ein Risikomanagementsystem einrichten.",
                     _classify(), enforce_contract=True, risk_class="C")
    assert res.contract_report is not None
    assert res.contract_report.ok
