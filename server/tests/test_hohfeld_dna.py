# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The juridical-primitive layer in the rule DNA (hohfeld.py): every ND
inherits these slots via RuleFacet → norm dict → obligations. Abstention
discipline throughout: '' beats a guessed incident, counterparty, or
condition kind."""


from rvnd.contracts.extractor import intake_contract
from rvnd.hohfeld import (INCIDENTS, attach_incidents, classify_condition_kind,
                           classify_incident, extract_counterparty)
from rvnd.rule_extractor import RuleFacet, extract_rules


class TestClassifyIncident:
    def test_obligation_is_claim_duty(self):
        assert classify_incident("obligation", "pay the fee", "") == "claim-duty"

    def test_prohibition_is_claim_duty(self):
        assert classify_incident("prohibition", "disclose information", "") == "claim-duty"

    def test_termination_permission_is_power(self):
        assert classify_incident("permission", "terminate this Agreement",
                                 "") == "power"

    def test_german_kuendigung_is_power(self):
        assert classify_incident("permission", "",
                                 "Der Kunde darf den Vertrag kündigen.") == "power"

    def test_consent_and_assignment_are_powers(self):
        assert classify_incident("permission", "approve sub-processors", "") == "power"
        assert classify_incident("right", "assign its claims", "") == "power"

    def test_plain_use_permission_is_privilege(self):
        assert classify_incident("permission", "use the Software", "") == "privilege"

    def test_no_variation_prohibition_is_immunity(self):
        assert classify_incident(
            "prohibition", "",
            "This Agreement may not be varied except in writing.") == "immunity"

    def test_prohibited_power_exercise_is_disability(self):
        # forbidding the EXERCISE OF A POWER removes the power (audit F1):
        # a no-assignment clause is a disability, not a conduct duty
        assert classify_incident("prohibition", "assign this Agreement",
                                 "The Licensee may not assign this Agreement.") \
            == "disability"
        assert classify_incident(
            "prohibition", "terminate for convenience",
            "Neither party may terminate for convenience.") == "disability"

    def test_conduct_prohibition_stays_claim_duty(self):
        assert classify_incident("prohibition", "disclose Confidential Information",
                                 "") == "claim-duty"

    def test_unknown_modal_abstains(self):
        assert classify_incident("", "do things", "") == ""

    def test_vocabulary_closed(self):
        assert set(INCIDENTS) == {"claim-duty", "privilege", "power",
                                  "immunity", "disability"}


class TestCounterparty:
    def test_obligee_from_action(self):
        assert extract_counterparty("notify the controller of a breach", "",
                                    {"controller", "processor"}) == "controller"

    def test_subject_never_its_own_counterparty(self):
        f = extract_rules("The Processor shall notify the Controller "
                          "without undue delay.")[0]
        attach_incidents([f], roles={"processor", "controller"})
        assert f.counterparty == "controller"      # not "processor"

    def test_unnamed_counterparty_abstains(self):
        assert extract_counterparty("keep records", "", {"controller"}) == ""


class TestConditionKind:
    def test_suspensive(self):
        assert classify_condition_kind("where the value exceeds the threshold") \
            == "suspensive"
        assert classify_condition_kind("sofern nicht anders vereinbart") \
            == "suspensive"

    def test_resolutive(self):
        assert classify_condition_kind("until revoked by the controller") \
            == "resolutive"
        assert classify_condition_kind("bis auf Widerruf") == "resolutive"

    def test_ambiguous_abstains(self):
        assert classify_condition_kind("as agreed between the parties") == ""
        assert classify_condition_kind("") == ""


class TestDnaPropagation:
    TEXT = ('AGREEMENT between A GmbH (the "Licensor") and B AG (the '
            '"Licensee").\n\n'
            "2. The Licensee shall report net revenue to the Licensor "
            "within 30 days of each calendar quarter.\n\n"
            "3. The Licensee may use the Software for its own productions.\n\n"
            "4. The Licensor may terminate this Agreement upon a material "
            "breach by the Licensee.\n")

    def test_intake_rules_carry_the_layer(self):
        rules = intake_contract(self.TEXT).rules
        {f.raw_sentence[:20]: f for f in rules}
        report = next(f for f in rules if "report" in f.raw_sentence)
        use = next(f for f in rules if "use the Software" in f.raw_sentence)
        term = next(f for f in rules if "terminate" in f.raw_sentence)
        assert report.incident == "claim-duty"
        assert report.counterparty == "licensor"
        assert use.incident == "privilege"
        assert term.incident == "power"            # not a mere permission

    def test_norm_dict_carries_the_layer(self, tmp_path):
        from rvnd.contracts.extractor import ingest_contract
        from rvnd.rule_registry import RuleRegistry
        ingest_contract(tmp_path, self.TEXT, contract_id="dna-x",
                        log_root=tmp_path / "log")
        norms = [r["norm"] for r in RuleRegistry(tmp_path).items.values()]
        assert all("incident" in n and "counterparty" in n
                   and "condition_kind" in n for n in norms)
        assert any(n["incident"] == "power" for n in norms)

    def test_obligations_inherit_obligee_and_incident(self, tmp_path):
        from rvnd.contracts.extractor import ingest_contract
        from rvnd.obligation_runtime import ObligationRegistry
        ingest_contract(tmp_path, self.TEXT, contract_id="dna-x",
                        log_root=tmp_path / "log")
        obs = ObligationRegistry(tmp_path).for_contract("dna-x@1")
        report = next(o for o in obs if "report" in o.summary)
        assert report.obligee_role == "licensor"
        assert report.facets["incident"] == "claim-duty"

    def test_powers_never_instantiate_as_duties(self, tmp_path):
        from rvnd.contracts.extractor import ingest_contract
        from rvnd.obligation_runtime import ObligationRegistry
        ingest_contract(tmp_path, self.TEXT, contract_id="dna-x",
                        log_root=tmp_path / "log")
        obs = ObligationRegistry(tmp_path).for_contract("dna-x@1")
        assert not any("terminate" in o.summary for o in obs)

    def test_existing_facets_default_empty_not_invented(self):
        f = RuleFacet(modal="obligation", subject="x", action="y")
        assert f.incident == "" and f.counterparty == "" and f.condition_kind == ""


class TestDnaCompleteness:
    """Audit round-3 completeness: EVERY path that creates a span-norm
    carries the layer — contract intake (role-aware), bare place_span
    (statute/ND path), and Phase-2 LLM facets."""

    def test_bare_place_span_enriches(self, tmp_path):
        from rvnd.rule_extractor import extract_rules
        from rvnd.rule_registry import RuleRegistry
        text = "The provider may terminate this agreement upon notice."
        facet = extract_rules(text, gated_by_fingerprint=False)[0]
        assert facet.incident == ""                  # born unenriched
        reg = RuleRegistry(tmp_path, log_root=tmp_path / "log")
        r = reg.place_span(text, facet=facet)        # the chokepoint enriches
        assert r["norm"]["incident"] == "power"

    def test_statute_duty_path_enriches(self, tmp_path):
        from rvnd.rule_registry import RuleRegistry
        reg = RuleRegistry(tmp_path, log_root=tmp_path / "log")
        r = reg.place_span("The provider shall inform the authority without delay.")
        assert r["norm"]["incident"] == "claim-duty"

    def test_phase2_llm_facets_enrich_deterministically(self):
        import json
        from rvnd.rule_extractor_llm import extract_rules_llm
        text = "The Provider may terminate this agreement at any time."
        reply = json.dumps([{
            "subject": "Provider", "modal": "permission", "modal_phrase": "may",
            "action": "terminate this agreement", "condition": "",
            "exception": "", "raw_sentence": text}])
        out = extract_rules_llm(text, model_fn=lambda p: reply)
        llm = [f for f in out.facets if "terminate" in f.raw_sentence]
        assert llm and llm[0].incident == "power"

    def test_nt14_rejects_invented_incident(self):
        from rvnd.norm_contract import check_incident_vocabulary
        pair = {"id": "p1", "problem": {"facets": {}},
                "solution": {"rule": {"incident": "super-right"}}, "edges": []}
        out = check_incident_vocabulary(pair)
        assert any(f.code == "NT-14" and f.level.value == "violation" for f in out)

    def test_nt14_accepts_abstention_and_vocabulary(self):
        from rvnd.norm_contract import check_incident_vocabulary
        for inc in ("", "power", "disability"):
            pair = {"id": "p1", "problem": {"facets": {}},
                    "solution": {"rule": {"incident": inc,
                                          "condition_kind": ""}}, "edges": []}
            assert all(f.level.value == "pass"
                       for f in check_incident_vocabulary(pair))
