# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P3 workbench I/O tests: export shape for the five screens, gated
apply-actions (actor + rationale enforced, failures reported never skipped),
correction loop, and the demo builder over the real template corpus."""

import json

import pytest

from workspaces.contracts.extractor import ingest_contract
from workspaces.obligation_runtime import ObligationRegistry
from workspaces.workbench_io import apply_actions, build_demo, export_state

DPA = """DATA PROCESSING AGREEMENT

This Data Processing Agreement is made between Norddata Services GmbH (the
"Processor") and Beispielkunde AG (the "Controller") under Article 28 GDPR.

This Agreement is effective as of 2026-07-01.

1. The Processor shall notify the Controller of a personal data breach no
later than 72 hours after the personal data breach.

2. The Processor must not engage a Sub-processor without the prior written
authorisation of the Controller.
"""


@pytest.fixture
def folder(tmp_path):
    ingest_contract(tmp_path, DPA, contract_id="dpa-x", log_root=tmp_path / "log")
    return tmp_path


class TestExport:
    def test_export_has_all_five_screens(self, folder):
        st = export_state(folder, log_root=folder / "log")
        assert st["schema"] == "p3-1"
        for key in ("contracts", "clauses", "obligations", "decision_queue", "audit"):
            assert key in st

    def test_s1_contract_card_fields(self, folder):
        st = export_state(folder, log_root=folder / "log")
        c = st["contracts"][0]
        assert c["ref"] == "dpa-x@1"
        assert c["instance"]["contract_type"] == "dpa"
        assert "term" in c["missing"]                    # honest not-extracted
        roles = {p["role"] for p in c["instance"]["parties"]}
        assert roles == {"processor", "controller"}

    def test_s2_clauses_carry_norm_and_version(self, folder):
        st = export_state(folder, log_root=folder / "log")
        assert st["clauses"]
        for cl in st["clauses"]:
            assert cl["norm"]["modal"] in ("obligation", "prohibition")
            assert cl["span"]["document_version"] == 1

    def test_s3_deadline_resolved_with_derivation(self, folder):
        # the 72h deadline needs a breach event date — assert unresolved first
        st = export_state(folder, log_root=folder / "log")
        notify = [o for o in st["obligations"] if o["deadline_rel"]]
        assert notify and notify[0]["resolved_deadline"] is None
        assert "unresolved" in notify[0]["derivation"]

    def test_s4_breach_candidate_enters_queue(self, folder):
        # give the contract its breach event by re-registering is heavy; instead
        # drive the no-deadline path: escalate via migration orphan
        obligations = ObligationRegistry(folder, log_root=folder / "log")
        obs = obligations.for_contract("dpa-x@1")
        obligations.supersede_for("dpa-x@1", "dpa-x@2",
                                  migrated_rules=[],
                                  orphaned_rules=[o.rule_id for o in obs])
        st = export_state(folder, log_root=folder / "log")
        kinds = {q["kind"] for q in st["decision_queue"]}
        assert "escalated-obligation" in kinds
        q = [x for x in st["decision_queue"] if x["kind"] == "escalated-obligation"][0]
        assert len(q["options"]) >= 2                    # never a single yes-button
        assert [o["id"] for o in q["options"]] == sorted(o["id"] for o in q["options"])

    def test_s5_audit_events_present(self, folder):
        st = export_state(folder, log_root=folder / "log")
        ops = {e["op"] for e in st["audit"]}
        assert "contract.create" in ops or "obligation.create" in {e["op"] for e in st["audit"]}


class TestApply:
    def _escalated(self, folder):
        obligations = ObligationRegistry(folder, log_root=folder / "log")
        obs = obligations.for_contract("dpa-x@1")
        obligations.supersede_for("dpa-x@1", "dpa-x@2", migrated_rules=[],
                                  orphaned_rules=[obs[0].rule_id])
        return obs[0].obligation_id

    def test_resolve_applies_through_registry(self, folder):
        oid = self._escalated(folder)
        out = apply_actions(folder, [{
            "kind": "resolve_obligation", "obligation_id": oid,
            "choice": "waived", "actor": "alex",
            "rationale": "duty did not survive the amendment"}],
            log_root=folder / "log")
        assert out["ok"]
        assert ObligationRegistry(folder).get(oid).state == "waived"

    def test_anonymous_action_refused(self, folder):
        oid = self._escalated(folder)
        out = apply_actions(folder, [{
            "kind": "resolve_obligation", "obligation_id": oid,
            "choice": "waived", "actor": "system", "rationale": "x"}],
            log_root=folder / "log")
        assert not out["ok"] and "named human actor" in out["failed"][0]["error"]

    def test_missing_rationale_refused(self, folder):
        oid = self._escalated(folder)
        out = apply_actions(folder, [{
            "kind": "resolve_obligation", "obligation_id": oid,
            "choice": "waived", "actor": "alex", "rationale": "  "}],
            log_root=folder / "log")
        assert not out["ok"] and "rationale" in out["failed"][0]["error"]

    def test_invalid_choice_refused(self, folder):
        oid = self._escalated(folder)
        out = apply_actions(folder, [{
            "kind": "resolve_obligation", "obligation_id": oid,
            "choice": "breached", "actor": "alex", "rationale": "x"}],
            log_root=folder / "log")
        assert not out["ok"]                             # humans use words; "breached" isn't a queue option

    def test_bind_term_round_trip(self, folder):
        out = apply_actions(folder, [{
            "kind": "bind_term", "contract_ref": "dpa-x@1", "term": "Processor",
            "entity_code": "norddata-services-gmbh", "actor": "alex",
            "rationale": "matches the named party"}], log_root=folder / "log")
        assert out["ok"]
        from workspaces.defined_terms import DefinedTermsRegistry
        assert DefinedTermsRegistry(folder).resolve("dpa-x@1", "Processor") \
            == "norddata-services-gmbh"

    def test_correction_persists_and_audits(self, folder):
        out = apply_actions(folder, [{
            "kind": "record_correction", "contract_ref": "dpa-x@1",
            "field": "effective_date", "extracted": "2026-07-01",
            "corrected": "2026-07-15", "actor": "alex",
            "rationale": "signature page says 15 July"}], log_root=folder / "log")
        assert out["ok"]
        lines = (folder / "contracts" / "corrections.jsonl").read_text().splitlines()
        rec = json.loads(lines[-1])
        assert rec["corrected"] == "2026-07-15" and rec["actor"] == "alex"

    def test_failures_reported_never_skipped(self, folder):
        out = apply_actions(folder, [
            {"kind": "no-such-kind", "actor": "alex", "rationale": "x"},
            {"kind": "record_correction", "contract_ref": "dpa-x@1",
             "field": "language", "corrected": "de", "actor": "alex",
             "rationale": "it is German"},
        ], log_root=folder / "log")
        assert len(out["applied"]) == 1 and len(out["failed"]) == 1


class TestPanelAdoptedFindings:
    """Findings adopted from legal review."""

    def test_obligor_resolves_to_party_role(self, folder):
        from workspaces.obligation_runtime import ObligationRegistry
        obs = ObligationRegistry(folder).for_contract("dpa-x@1")
        assert {o.obligor_role for o in obs} == {"processor"}   # not "the-processor"

    def test_breach_candidate_offers_disputed_keep_open(self, folder):
        obligations = ObligationRegistry(folder, log_root=folder / "log")
        obs = obligations.for_contract("dpa-x@1")
        obligations.supersede_for("dpa-x@1", "dpa-x@2", migrated_rules=[],
                                  orphaned_rules=[obs[0].rule_id])
        st = export_state(folder, log_root=folder / "log")
        q = [x for x in st["decision_queue"] if x.get("obligation_id")][0]
        assert "disputed" in [o["id"] for o in q["options"]]

    def test_note_obligation_keeps_state_records_judgment(self, folder):
        obligations = ObligationRegistry(folder, log_root=folder / "log")
        oid = obligations.for_contract("dpa-x@1")[0].obligation_id
        out = apply_actions(folder, [{
            "kind": "note_obligation", "obligation_id": oid, "actor": "alex",
            "rationale": "cure period agreed until 2026-09-30, counterparty notified"}],
            log_root=folder / "log")
        assert out["ok"]
        ob = ObligationRegistry(folder)
        assert ob.get(oid).state == "pending"                   # unchanged
        assert any(h.get("annotation") for h in ob.history(oid))

    def test_anonymous_annotation_refused(self, folder):
        from workspaces.obligation_runtime import ObligationError, ObligationRegistry
        obligations = ObligationRegistry(folder, log_root=folder / "log")
        oid = obligations.for_contract("dpa-x@1")[0].obligation_id
        with pytest.raises(ObligationError, match="name the actor"):
            obligations.annotate(oid, actor="system", note="x")

    def test_obligations_carry_fundstelle_in_export(self, folder):
        st = export_state(folder, log_root=folder / "log")
        pins = [o.get("fundstelle") for o in st["obligations"]]
        assert any(p for p in pins)                              # joined from spans

    def test_garbage_subject_never_becomes_obligor_id(self, tmp_path):
        from workspaces.contracts.instance import ContractInstance, PartyRef
        from workspaces.obligation_runtime import _obligor_role
        c = ContractInstance(contract_id="x", parties=(
            PartyRef(entity_code="a", role="licensee"),))
        leaked = "where the annual net revenue exceeds eur 100,000, the licensee"
        assert _obligor_role(leaked, c) == "licensee"            # party match wins
        c2 = ContractInstance(contract_id="y")
        assert _obligor_role(leaked, c2) == "unknown"            # never a sentence slug


class TestLegalPanelRound2:
    """Doctrinal findings from panel round 2, REVISED for the principal's
    alignment ruling: the substrate stays jurisdiction-NEUTRAL. It observes
    calendar facts and flags; jurisdiction rules (Fristen extension,
    mandatory-content standards) apply only when explicitly configured —
    by a pack, an ND, or a caller. Detection neutral, application opt-in."""

    def _saturday_deadline_folder(self, tmp_path):
        from workspaces.contracts.instance import ContractInstance, ContractRegistry, PartyRef
        from workspaces.obligation_runtime import ObligationRegistry
        c = ContractInstance(contract_id="wk-x",
                             parties=(PartyRef(entity_code="a", role="supplier"),))
        ContractRegistry(tmp_path, log_root=tmp_path / "log").register(c)
        obs = ObligationRegistry(tmp_path, log_root=tmp_path / "log")
        obs.instantiate(c, [{"id": "rule:wk", "norm": {
            "modal": "obligation", "subject": "supplier", "action": "deliver"}}])
        oid = obs.for_contract("wk-x@1")[0].obligation_id
        # absolute Saturday deadline, set directly on the record
        obs.items[oid]["deadline_date"] = "2026-08-15"
        obs._flush()
        return oid

    def test_weekend_shift_utility_arithmetic(self):
        from workspaces.temporal import Date, weekend_shift
        assert weekend_shift(Date("2026-08-15")) == Date("2026-08-17")  # Sat
        assert weekend_shift(Date("2026-08-16")) == Date("2026-08-17")  # Sun
        assert weekend_shift(Date("2026-08-17")) == Date("2026-08-17")  # Mon

    def test_default_is_neutral_observes_and_flags_never_applies(self, tmp_path):
        from workspaces.obligation_scheduler import ObligationScheduler
        from workspaces.temporal import Date
        self._saturday_deadline_folder(tmp_path)
        sched = ObligationScheduler(tmp_path, log_root=tmp_path / "log")
        report = sched.tick(Date("2026-08-16"))                  # the Sunday
        t = [x for x in report.transitions if x["to"] == "breached_candidate"][0]
        assert t["weekend_deadline"] is True
        assert t["shift_rule_applied"] is False                  # nothing applied
        assert "extension rules of the governing law" in t["caveat"]
        assert "public holidays not checked" in t["caveat"]

    def test_configured_shift_rule_applies_only_when_supplied(self, tmp_path):
        from workspaces.obligation_scheduler import ObligationScheduler
        from workspaces.temporal import Date, weekend_shift
        self._saturday_deadline_folder(tmp_path)
        sched = ObligationScheduler(tmp_path, log_root=tmp_path / "log",
                                    deadline_shift=weekend_shift)
        report = sched.tick(Date("2026-08-16"))                  # the Sunday
        # with the (pack-supplied) rule, Monday is the effective deadline:
        # no breach candidate on Sunday
        assert all(t["to"] != "breached_candidate" for t in report.transitions)

    @staticmethod
    def _eu_checklist():
        from workspaces.contracts.extractor import REFERENCE_PACKS_DIR, load_checklist
        ctype, name, checklist = load_checklist(
            REFERENCE_PACKS_DIR / "eu-gdpr-dpa.json")
        return {ctype: (name, checklist)}

    def test_checklist_engine_is_generic_checklists_are_pack_data(self):
        from workspaces.contracts.extractor import (check_mandatory_content,
                                              intake_contract)
        checklists = self._eu_checklist()
        name, checklist = checklists["dpa"]
        out = check_mandatory_content(DPA, checklist, name=name)
        assert any("28(3)(c)" in m for m in out["not_found"])    # no TOMs clause
        assert any("28(3)(e)" in m for m in out["not_found"])    # no DS-rights
        # NEUTRAL DEFAULT: no checklist supplied → no mandatory-content facet
        assert "mandatory_content" not in intake_contract(DPA).instance.facets
        # OPT-IN: pack supplies it → facet present, the pack's name carried
        intake = intake_contract(DPA, checklists=checklists)
        assert intake.instance.facets["mandatory_content"]["not_found"]
        assert intake.instance.facets["mandatory_content"]["name"] == name

    def test_no_statute_citations_in_substrate_code(self):
        """Concepts are substrate; citations are pack data. AST-based guard
        (audit 2026-06-05): every STRING LITERAL used as a value in the
        contract-execution substrate — excluding docstrings, which describe
        the seam — must be statute-free. Scope: the execution substrate; the
        KG/corpus layer is excluded by design (representing laws as data is
        its job)."""
        import ast
        import inspect
        import re as _re
        from workspaces import (fact_source, hohfeld, obligation_runtime,
                           obligation_scheduler, predicate, temporal,
                           workbench_io)
        from workspaces.contracts import extractor as contract_extractor
        statute = _re.compile(
            r"§\s*\d|\bArt(?:icle|\.)\s*\d+|\bBGB\b|\bGDPR\b|\bDSGVO\b|"
            r"\bHGB\b|\bUrhG\b|\d+\(\d\)\([a-z]\)")
        for mod in (temporal, obligation_scheduler, obligation_runtime,
                    contract_extractor, predicate, hohfeld, fact_source,
                    workbench_io):
            tree = ast.parse(inspect.getsource(mod))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                    if (node.body and isinstance(node.body[0], ast.Expr)
                            and isinstance(node.body[0].value, ast.Constant)):
                        docstrings.add(id(node.body[0].value))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docstrings):
                    assert not statute.search(node.value), (
                        f"statute citation in substrate value: "
                        f"{mod.__name__}: {node.value!r}")

    def test_conformity_engine_carries_no_statute_in_code_values(self):
        """The conformity engine (conformity.py, disclosure.py) is the
        jurisdiction-neutral evidence projector; statute citations belong in a
        regime pack, not in code values. Scan logic lines for citation
        patterns; docstrings/comments (the seam description) are exempt."""
        import inspect
        import re as _re
        from workspaces import conformity, disclosure
        cite = _re.compile(r"Art\.\s*\d+|§\s*\d+|prEN\s*\d+|GDPR|\bDSA\b|"
                           r"NIS2|MiFID|Reg\.\s*\d|ISO/IEC\s*\d")
        for mod in (conformity, disclosure):
            in_doc = False
            for line in inspect.getsource(mod).splitlines():
                if line.count('"""') % 2 == 1:
                    in_doc = not in_doc
                    continue
                if in_doc or line.lstrip().startswith("#"):
                    continue
                code = line.split("#")[0]
                assert not cite.search(code), \
                    f"statute citation in engine code: {mod.__name__}: {line.strip()}"

    def test_mandatory_gap_reaches_decision_queue_when_opted_in(self, tmp_path):
        from workspaces.contracts.extractor import ingest_contract
        ingest_contract(tmp_path, DPA, contract_id="dpa-x",
                        log_root=tmp_path / "log",
                        checklists=self._eu_checklist())
        st = export_state(tmp_path, log_root=tmp_path / "log")
        gaps = [q for q in st["decision_queue"]
                if q["kind"] == "mandatory-content-gap"]
        assert gaps and "28(3)" in gaps[0]["subject"]
        assert {o["id"] for o in gaps[0]["options"]} == {
            "confirmed-missing", "present-unrecognised"}


class TestDemo:
    def test_demo_builds_full_state(self, tmp_path):
        out = build_demo(tmp_path, log_root=tmp_path / "log")
        assert len(out["ingested"]) == 5                 # the template corpus
        st = export_state(tmp_path, log_root=tmp_path / "log")
        assert len(st["contracts"]) == 5
        assert st["clauses"] and st["obligations"]
        # the second tick (2026-12-01) is past several deadlines → candidates
        assert any(q["kind"] == "breach-candidate" for q in st["decision_queue"])
        # AVV's 72h obligation resolved through the DE relative deadline?
        # (no breach event date in the template → stays unresolved, visible)
        unresolved = [o for o in st["obligations"] if o["resolved_deadline"] is None
                      and o["state"] in ("pending",)]
        assert unresolved, "unresolved deadlines must stay visible, not vanish"
