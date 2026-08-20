# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P2 obligation-runtime tests: state machine, deterministic scheduler tick,
fact binding, and the plan's gate demo (DPA 72h notification → tick past
deadline → breach candidate surfaces, never auto-declared breach)."""

import pytest

from workspaces.contracts.instance import ContractInstance, ContractRegistry, PartyRef
from workspaces.fact_source import (UNKNOWN, CsvFactSource, Fact, ManualFactSource,
                               evaluate)
from workspaces.obligation_runtime import (ObligationError,
                                      ObligationRegistry)
from workspaces.obligation_scheduler import ObligationScheduler, _target_state
from workspaces.predicate import Predicate, parse_condition
from workspaces.temporal import Date, Duration


def dpa(version: int = 1, **kw) -> ContractInstance:
    base = dict(
        contract_id="dpa-acme", version=version, contract_type="dpa",
        parties=(PartyRef(entity_code="acme", role="processor"),
                 PartyRef(entity_code="kunde", role="controller")),
        effective_date=Date("2026-07-01"),
        events={"signing": Date("2026-06-15"),
                "personal_data_breach": Date("2026-08-10")},
        document_hash=f"sha256:{'a' * 31}{version}", language="en")
    base.update(kw)
    return ContractInstance(**base)


RULE_NOTIFY = {
    "id": "rule:notify72",
    "norm": {"modal": "obligation", "subject": "processor",
             "action": "notify the controller of a personal data breach",
             "condition": "no later than 72 hours after the personal data breach",
             "condition_struct": parse_condition(
                 "no later than 72 hours after the personal data breach").to_dict()},
}
RULE_DELETE = {
    "id": "rule:delete",
    "norm": {"modal": "obligation", "subject": "processor",
             "action": "delete all personal data upon termination",
             "condition": ""},
}
RULE_MAY = {
    "id": "rule:may-retain",
    "norm": {"modal": "permission", "subject": "processor",
             "action": "retain anonymised data", "condition": ""},
}


def setup_registry(tmp_path):
    contracts = ContractRegistry(tmp_path, log_root=tmp_path / "log")
    contracts.register(dpa())
    obligations = ObligationRegistry(tmp_path, log_root=tmp_path / "log")
    out = obligations.instantiate(dpa(), [RULE_NOTIFY, RULE_DELETE, RULE_MAY])
    return contracts, obligations, out


# ── instantiation ─────────────────────────────────────────────────────────────

class TestInstantiate:
    def test_only_duties_instantiate(self, tmp_path):
        _, _, out = setup_registry(tmp_path)
        assert len(out["created"]) == 2
        assert any(s["why"] == "modal=permission" for s in out["skipped"])

    def test_idempotent(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        again = obligations.instantiate(dpa(), [RULE_NOTIFY])
        assert again["created"] == [] and again["skipped"][0]["why"] == "exists"

    def test_deadline_from_condition_struct(self, tmp_path):
        _, obligations, out = setup_registry(tmp_path)
        obs = obligations.for_contract("dpa-acme@1")
        with_deadline = [o for o in obs if o.deadline_rel is not None]
        assert len(with_deadline) == 1
        assert with_deadline[0].deadline_rel.offset.iso == "PT72H"

    def test_no_deadline_tracked_not_invented(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        obs = {o.rule_id: o for o in obligations.for_contract("dpa-acme@1")}
        assert obs["rule:delete"].deadline_rel is None
        assert obs["rule:delete"].resolved_deadline(dpa()) is None

    def test_persisted_reload(self, tmp_path):
        setup_registry(tmp_path)
        fresh = ObligationRegistry(tmp_path)
        assert len(fresh.for_contract("dpa-acme@1")) == 2


# ── state machine ─────────────────────────────────────────────────────────────

class TestStateMachine:
    def _oid(self, obligations):
        return obligations.for_contract("dpa-acme@1")[0].obligation_id

    def test_machine_advance_path(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        oid = self._oid(obligations)
        obligations.advance(oid, "due_soon", reason="window")
        obligations.advance(oid, "due", reason="deadline day")
        rec = obligations.advance(oid, "breached_candidate", reason="passed")
        assert rec["state"] == "breached_candidate"

    def test_machine_cannot_declare_breach(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        oid = self._oid(obligations)
        with pytest.raises(ObligationError):
            obligations.advance(oid, "breached", reason="x")     # no such state
        with pytest.raises(ObligationError):
            obligations.advance(oid, "satisfied", reason="x")    # human-only

    def test_resolution_requires_named_actor(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        oid = self._oid(obligations)
        with pytest.raises(ObligationError, match="name the actor"):
            obligations.resolve(oid, "satisfied", actor="system", reason="done")

    def test_resolution_requires_rationale(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        oid = self._oid(obligations)
        with pytest.raises(ObligationError, match="rationale"):
            obligations.resolve(oid, "waived", actor="alex", reason="  ")

    def test_human_resolves_breach_candidate(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        oid = self._oid(obligations)
        obligations.advance(oid, "breached_candidate", reason="passed")
        rec = obligations.resolve(oid, "waived", actor="alex",
                                  reason="cure period agreed 2026-08-20")
        assert rec["state"] == "waived"

    def test_no_backward_machine_motion(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        oid = self._oid(obligations)
        obligations.advance(oid, "due", reason="deadline day")
        with pytest.raises(ObligationError):
            obligations.advance(oid, "due_soon", reason="rewind")

    def test_history_is_kept(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        oid = self._oid(obligations)
        obligations.advance(oid, "due_soon", reason="window")
        hist = obligations.history(oid)
        assert [h["state"] for h in hist] == ["pending", "due_soon"]
        assert all(h["reason"] for h in hist)


# ── version migration ─────────────────────────────────────────────────────────

class TestMigration:
    def test_migrate_and_escalate(self, tmp_path):
        contracts, obligations, _ = setup_registry(tmp_path)
        v2 = dpa(version=2, supersedes="dpa-acme@1",
                 document_hash="sha256:" + "b" * 32)
        contracts.supersede("dpa-acme@1", v2)
        out = obligations.supersede_for(
            "dpa-acme@1", "dpa-acme@2",
            migrated_rules=["rule:notify72"], orphaned_rules=["rule:delete"])
        assert len(out["migrated"]) == 1 and len(out["escalated"]) == 1
        assert out["escalate"] is True
        moved = obligations.for_contract("dpa-acme@2")
        assert len(moved) == 1 and moved[0].rule_id == "rule:notify72"
        assert obligations.in_state("escalated")[0].rule_id == "rule:delete"

    def test_closed_obligations_untouched_by_migration(self, tmp_path):
        contracts, obligations, _ = setup_registry(tmp_path)
        oid = [o for o in obligations.for_contract("dpa-acme@1")
               if o.rule_id == "rule:delete"][0].obligation_id
        obligations.resolve(oid, "satisfied", actor="alex", reason="confirmed deleted")
        out = obligations.supersede_for("dpa-acme@1", "dpa-acme@2",
                                        migrated_rules=["rule:notify72"],
                                        orphaned_rules=["rule:delete"])
        assert oid in out["untouched"]
        assert obligations.get(oid).state == "satisfied"

    def test_escalated_feeds_candidates(self, tmp_path):
        _, obligations, _ = setup_registry(tmp_path)
        obligations.supersede_for("dpa-acme@1", "dpa-acme@2",
                                  migrated_rules=[], orphaned_rules=["rule:delete"])
        assert any(o.rule_id == "rule:delete" for o in obligations.candidates())


# ── scheduler ─────────────────────────────────────────────────────────────────

class TestScheduler:
    def test_target_state_arithmetic(self):
        d = Date("2026-08-13")
        w = Duration.parse("P14D")
        assert _target_state(d, Date("2026-07-01"), w) == "pending"
        assert _target_state(d, Date("2026-08-01"), w) == "due_soon"
        assert _target_state(d, Date("2026-08-13"), w) == "due"
        assert _target_state(d, Date("2026-08-14"), w) == "breached_candidate"

    def test_plan_gate_demo_breach_candidate_surfaces(self, tmp_path):
        """The P2 gate scenario: DPA 72h-notification obligation, tick past
        the deadline → breach candidate on the decision-surface feed, never an
        auto-declared breach."""
        setup_registry(tmp_path)
        sched = ObligationScheduler(tmp_path, log_root=tmp_path / "log")
        # breach event 2026-08-10 → 72h → deadline 2026-08-10 (date-level: PT72H < 1 day shift? no: hours ignored for date)  # noqa: E501
        report = sched.tick(Date("2026-09-01"))
        assert len(report.candidates) == 1
        ob = sched.obligations.get(report.candidates[0])
        assert ob.state == "breached_candidate"          # candidate, not breach
        assert ob.rule_id == "rule:notify72"
        # the no-deadline obligation is visible as unresolved, untouched
        assert len(report.unresolved) == 1

    def test_tick_is_replay_safe(self, tmp_path):
        setup_registry(tmp_path)
        sched = ObligationScheduler(tmp_path, log_root=tmp_path / "log")
        first = sched.tick(Date("2026-09-01"))
        second = sched.tick(Date("2026-09-01"))
        assert len(first.transitions) == 1
        assert second.transitions == []                  # no duplicate motion

    def test_due_soon_window(self, tmp_path):
        setup_registry(tmp_path)
        sched = ObligationScheduler(tmp_path, log_root=tmp_path / "log")
        report = sched.tick(Date("2026-08-01"))          # 9 days before 8-10 deadline
        assert report.transitions[0]["to"] == "due_soon"

    def test_reminder_under_grade_is_no_go(self, tmp_path):
        """Default L2 scheduler may NOT send external reminders at all —
        external-publish requires grade ≥ 3. Nothing leaves silently."""
        setup_registry(tmp_path)
        sched = ObligationScheduler(tmp_path, log_root=tmp_path / "log")
        report = sched.tick(Date("2026-08-01"))
        reminders = [p for p in report.proposals if p.action_class == "remind-obligor"]
        assert reminders and reminders[0].decision.verdict.value == "NO-GO"

    def test_reminder_l3_needs_signoff(self, tmp_path):
        setup_registry(tmp_path)
        sched = ObligationScheduler(tmp_path, log_root=tmp_path / "log",
                                    autonomy_grade="L3")
        report = sched.tick(Date("2026-08-01"))
        reminders = [p for p in report.proposals if p.action_class == "remind-obligor"]
        assert reminders[0].decision.verdict.value == "CONDITIONAL"

    def test_reminder_goes_with_standing_approval(self, tmp_path):
        from workspaces.action_gate import StandingApproval
        setup_registry(tmp_path)
        sa = StandingApproval(agent="obligation-scheduler",
                              action_class="remind-obligor",
                              obligation_pair="pair:reminders-ok",
                              until="2027-01-01")
        sched = ObligationScheduler(tmp_path, log_root=tmp_path / "log",
                                    autonomy_grade="L3", standing_approvals=[sa])
        report = sched.tick(Date("2026-08-01"))
        reminders = [p for p in report.proposals if p.action_class == "remind-obligor"]
        assert reminders[0].decision.verdict.value == "GO"

    def test_surfacing_breach_candidate_is_benign_go(self, tmp_path):
        setup_registry(tmp_path)
        sched = ObligationScheduler(tmp_path, log_root=tmp_path / "log")
        report = sched.tick(Date("2026-09-01"))
        surf = [p for p in report.proposals
                if p.action_class == "surface-breach-candidate"]
        assert surf and surf[0].decision.verdict.value == "GO"


# ── fact binding ──────────────────────────────────────────────────────────────

class TestFactBinding:
    def test_csv_adapter(self, tmp_path):
        f = tmp_path / "facts.csv"
        f.write_text("subject_ref,value,unit,observed_at\n"
                     "amount,12000,EUR,2026-08-01T00:00:00Z\n", encoding="utf-8")
        src = CsvFactSource(f)
        fact = src.get("amount")
        assert fact.value == "12000" and fact.unit == "EUR"
        assert src.get("ghost") is None

    def test_manual_adapter_requires_named_actor(self, tmp_path):
        src = ManualFactSource(tmp_path, log_root=tmp_path / "log")
        with pytest.raises(ValueError, match="name the human"):
            src.assert_fact("delivery_accepted", "true", actor="system")

    def test_manual_adapter_roundtrip_latest_wins(self, tmp_path):
        src = ManualFactSource(tmp_path, log_root=tmp_path / "log")
        src.assert_fact("delivery_accepted", "false", actor="alex")
        src.assert_fact("delivery_accepted", "true", actor="alex")
        fresh = ManualFactSource(tmp_path)
        assert fresh.get("delivery_accepted").value == "true"

    def test_threshold_satisfied(self):
        p = Predicate(kind="threshold", subject_ref="amount", comparator=">",
                      value="10000", unit="EUR", confidence=0.9)
        out = evaluate(p, Fact("amount", "12000", unit="EUR"))
        assert out["verdict"] == "satisfied"

    def test_threshold_unsatisfied(self):
        p = Predicate(kind="threshold", subject_ref="amount", comparator=">",
                      value="10000", unit="EUR", confidence=0.9)
        assert evaluate(p, Fact("amount", "9000", unit="EUR"))["verdict"] == "unsatisfied"

    def test_no_fact_is_unknown_never_false(self):
        p = Predicate(kind="threshold", subject_ref="amount", comparator=">",
                      value="10000", confidence=0.9)
        assert evaluate(p, None)["verdict"] == UNKNOWN

    def test_unit_mismatch_is_unknown(self):
        p = Predicate(kind="threshold", subject_ref="amount", comparator=">",
                      value="10000", unit="EUR", confidence=0.9)
        out = evaluate(p, Fact("amount", "12000", unit="USD"))
        assert out["verdict"] == UNKNOWN and "unit mismatch" in out["reason"]

    def test_garbage_value_is_unknown(self):
        p = Predicate(kind="threshold", subject_ref="amount", comparator=">",
                      value="10000", confidence=0.9)
        assert evaluate(p, Fact("amount", "a lot"))["verdict"] == UNKNOWN

    def test_state_predicate(self):
        p = Predicate(kind="state", subject_ref="delivery_accepted", confidence=0.9)
        assert evaluate(p, Fact("delivery_accepted", "true"))["verdict"] == "satisfied"
        assert evaluate(p, Fact("delivery_accepted", "no"))["verdict"] == "unsatisfied"
        assert evaluate(p, Fact("delivery_accepted", "maybe"))["verdict"] == UNKNOWN

    def test_event_predicate_not_fact_evaluable(self):
        p = parse_condition("within 30 days of the signing")
        assert evaluate(p, Fact("signing", "2026-06-15"))["verdict"] == UNKNOWN
