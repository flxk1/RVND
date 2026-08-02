# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Golden fixture for deterministic policy text to digital twin.

The tests cover cue extraction, classification, patch validation, netlist
round-tripping, no-write ingest behavior, facade parity, and residual backstops.
"""
from __future__ import annotations

import os

from workspaces import policy_ingest as P
from workspaces import loomground_lang as L
from workspaces import mcp_server as M

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

POLICY = """Automated hiring decisions must be reviewed by a compliance officer.
The AI system shall not use biometric categorisation of individuals.
Users must be informed when they are interacting with the AI system.
Individuals may appeal an automated decision and request human review.
All decisions must be logged and retained for two years.
The autonomy level for low-risk tasks is set by the deployment team."""

EXPRESS = [
    "reserve automated_hiring_decision by compliance_officer",
    "prohibit use_biometric_categorisation_of_individual",
    "obligation ai-interaction-disclosure on ai_interaction",
    "redress automated_decision by appeals",
]


def test_extract_classify_express():
    t = P.ingest(POLICY)
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == EXPRESS
    assert len(t["classification"]["host"]) == 1 and "logged" in t["classification"]["host"][0]
    assert len(t["classification"]["policy"]) == 1 and "autonomy level" in t["classification"]["policy"][0]
    assert t["classification"]["unmapped"] == []
    # high-risk floor inferred for the biometric gate
    gates = {n["id"]: n.get("risk_floor") for n in t["patch"]["nodes"] if n["class"] == "gate"}
    assert gates["use_biometric_categorisation_of_individual"] == "high"


def test_patch_validates_and_netlist_roundtrips():
    t = P.ingest(POLICY)
    assert L.validate(t["patch"])["ok"]
    rp = L.parse(t["netlist"])
    assert L.validate(rp)["ok"]
    assert L.project(rp) == t["projection"]
    # every gate has an egress path to the boundary
    assert {p["gate"] for p in t["paths"]} == {
        "automated_hiring_decision", "use_biometric_categorisation_of_individual",
        "ai_interaction", "automated_decision"}


def test_not_applied_until_confirmed():
    t = P.ingest(POLICY)
    assert t["applied"] is False
    assert "confirm" in t["note"].lower()


def test_empty_policy_fails_clean():
    r = P.ingest("")
    assert r["ok"] is False and "errors" in r


def test_facade_op_and_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = str(tmp_path / "org"); os.makedirs(ws)
    r = M.workspace_workflow(op="policy_ingest", params={"folder_context": ws, "policy_text": POLICY})
    assert r["ok"] and r["classification"]["express"] == EXPRESS and r["applied"] is False
    assert len(M._DECLARED_TOOLS) == 24


# ---- Host keyword must not swallow a co-located obligation -----------------

def test_p1_host_keyword_does_not_suppress_residual_obligation():
    # "logged" files under host, but the co-located "approved by the board"
    # reservation that the reserve regex can't parse (compound "logged and
    # approved") must STILL be surfaced in unmapped — not silently dropped.
    t = P.ingest("All decisions must be logged and approved by the board.")
    assert t["ok"], t.get("errors")
    cls = t["classification"]
    assert any("logged" in h for h in cls["host"])            # host classification kept
    assert any("approved" in u for u in cls["unmapped"])      # residual surfaced (additive)


def test_p1_pure_host_sentence_is_not_flooded_into_unmapped():
    # The additive backstop must NOT regress: a pure host hand-off (host cue, no
    # reservation/prohibition cue) stays host-only, not duplicated into unmapped.
    t = P.ingest("All access must be logged for two years.")
    assert t["ok"], t.get("errors")
    assert any("logged" in h for h in t["classification"]["host"])
    assert t["classification"]["unmapped"] == []


# ---- Inflected redress cues + bare-"may" backstop --------------------------

def test_p2_inflected_redress_is_extracted_not_dropped():
    # "appealed"/"reversed" were unstemmed pre-fix → the whole sentence dropped
    # with no trace. Now redress is extracted, with overturn from "reversed".
    t = P.ingest("Automated decisions may be appealed and reversed.")
    assert t["ok"], t.get("errors")
    assert any(e.startswith("redress ") for e in t["classification"]["express"])
    red = t["patch"].get("redress", [])
    assert red and red[0]["overturn"] is True
    assert t["classification"]["unmapped"] == []              # not dropped


def test_p2_overturn_and_annul_forms_extract_as_redress():
    # The reversal family (overturned / annulled) must extract as redress with
    # overturn=True — the cue and the overturn flag share one regex now.
    for txt in ("Users can request that decisions be overturned.",
                "Automated decisions may be annulled on request."):
        t = P.ingest(txt)
        assert t["ok"], (txt, t.get("errors"))
        assert any(e.startswith("redress ") for e in t["classification"]["express"]), txt
        assert t["patch"]["redress"][0]["overturn"] is True, txt


def test_p1_descriptive_non_modal_cue_does_not_false_extract():
    # A descriptive (non-deontic) use of a cue word must NOT extract a phantom
    # redress nor surface to unmapped — no modal, no grant.
    t = P.ingest("The system runs in contested environments with reversal handling.")
    assert t["ok"], t.get("errors")
    assert t["patch"].get("redress", []) == []
    assert t["classification"]["unmapped"] == []


def test_p1_past_tense_approval_is_not_an_obligation():
    # "was approved" is a factual statement, not a modal obligation → not unmapped.
    t = P.ingest("The board approved the deployment last quarter.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["unmapped"] == []


# ---- Quorum reserve targets (counted and coordinated approvers) -------------

def _roundtrips(t):
    rp = L.parse(t["netlist"])
    assert L.validate(rp)["ok"], rp
    assert L.project(rp) == t["projection"]


def test_quorum_numeric_m_of_n_form():
    # A stated count over a named role list drafts the canonical quorum target.
    t = P.ingest("High-risk deployments require approval by two of the following: "
                 "legal, security, ethics board.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == [
        "reserve high_risk_deployment by 2 of {legal, security, ethics_board}"]
    r = t["patch"]["reservations"][0]
    assert r["by"] == "2 of {legal, security, ethics_board}"
    humans = {n["id"] for n in t["patch"]["nodes"] if n["class"] == "human"}
    assert {"legal", "security", "ethics_board"} <= humans
    assert t["classification"]["unmapped"] == []
    _roundtrips(t)


def test_quorum_conjunction_two_distinct_roles():
    # "X and Y" names two distinct roles: both hands are reserved, both become
    # human nodes — the second approver is not dropped.
    t = P.ingest("Model releases must be approved by the CISO and the DPO.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == ["reserve model_release by ciso and dpo"]
    assert t["patch"]["reservations"][0]["by"] == "ciso and dpo"
    humans = {n["id"] for n in t["patch"]["nodes"] if n["class"] == "human"}
    assert {"ciso", "dpo"} <= humans
    _roundtrips(t)


def test_quorum_three_and_joined_roles_become_all_of():
    # The grammar caps "and" at two hands; three coordinated roles draft as an
    # all-of quorum.
    t = P.ingest("Access must be approved by the CISO, the DPO, and the CTO.")
    assert t["ok"], t.get("errors")
    assert t["patch"]["reservations"][0]["by"] == "3 of {ciso, dpo, cto}"
    _roundtrips(t)


def test_quorum_counted_same_role_two_hands():
    # A count of two over one role class drafts the conjunction of two hands of
    # that class ("2 of {officer}" would be unsatisfiable at validate).
    t = P.ingest("Two officers must approve production deployments.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == [
        "reserve production_deployment by officer and officer"]
    _roundtrips(t)


def test_quorum_four_eyes_principle_reserves_two_hands():
    # A named two-hand rule with no role stated reserves two generic reviewers.
    t = P.ingest("Wire transfers are subject to the four-eyes principle.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == [
        "reserve wire_transfer by human-reviewer and human-reviewer"]
    _roundtrips(t)


def test_single_approver_stays_single_not_quorum():
    # A bare single approver must not be inflated into a quorum target.
    t = P.ingest("Deployments must be approved by the release manager.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == ["reserve deployment by release_manager"]
    by = t["patch"]["reservations"][0]["by"]
    assert " and " not in by and "of {" not in by
    _roundtrips(t)


def test_unsatisfiable_counted_quorum_abstains_to_unmapped():
    # A stated count larger than the named role list is unsatisfiable; extraction
    # abstains and the sentence is surfaced for review, never drafted wrong.
    t = P.ingest("Deployments require approval by five of the following: legal, security.")
    assert t["ok"], t.get("errors")
    assert t["patch"].get("reservations", []) == []
    assert any("five of the following" in u for u in t["classification"]["unmapped"])


def test_temporal_count_is_not_a_quorum():
    # "Two weeks after ..." counts time, not approvers: no reservation, no
    # phantom "week_after_deployment_team and week_after_deployment_team" hands.
    t = P.ingest("Two weeks after deployment teams must review the audit log.")
    assert t["ok"], t.get("errors")
    assert t["patch"].get("reservations", []) == []
    assert t["classification"]["express"] == []


def test_deadline_complement_is_not_an_approver():
    # "requires approval by <deadline>" states when approval is due, not who
    # approves: no reservation is drafted and no phantom human node appears;
    # the residual backstop surfaces the sentence for review instead.
    for txt in ("This change requires approval by end of day.",
                "Expense reports require approval by Friday at the latest.",
                "Invoices require approval by two weeks after month end."):
        t = P.ingest(txt)
        assert t["ok"], (txt, t.get("errors"))
        assert t["patch"].get("reservations", []) == [], txt
        assert t["classification"]["express"] == [], txt
        humans = {n["id"] for n in t["patch"]["nodes"] if n["class"] == "human"}
        assert humans == set(), txt
        assert any("approval" in u for u in t["classification"]["unmapped"]), txt


def test_clock_time_deadline_is_not_an_approver():
    # "approved by 5pm" states a clock deadline, not an agent: a digit-led
    # clock token never becomes an approver role; the sentence falls to the
    # residual backstop for review.
    for txt in ("Changes must be approved by 5pm.",
                "Invoices require approval by 6 pm on the due date.",
                "Releases must be approved by 17:00."):
        t = P.ingest(txt)
        assert t["ok"], (txt, t.get("errors"))
        assert t["patch"].get("reservations", []) == [], txt
        assert t["classification"]["express"] == [], txt
        humans = {n["id"] for n in t["patch"]["nodes"] if n["class"] == "human"}
        assert humans == set(), txt
        assert any("approv" in u for u in t["classification"]["unmapped"]), txt


def test_passive_counted_two_hands_over_one_role():
    # "approved by two independent auditors" is the passive mirror of the
    # active counted form: a count of two over one role class drafts the
    # two-hand conjunction, never a single role with the count in its name.
    t = P.ingest("Deployments must be approved by two independent auditors.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == [
        "reserve deployment by auditor and auditor"]
    r = t["patch"]["reservations"][0]
    assert r["by"] == "auditor and auditor"
    _roundtrips(t)


def test_passive_counted_above_two_abstains():
    # Counts above two over one role class are not expressible as a target;
    # the sentence abstains to the residual backstop instead of drafting a
    # wrong quorum or a count-in-the-name role.
    t = P.ingest("Deletions must be approved by three senior engineers.")
    assert t["ok"], t.get("errors")
    assert t["patch"].get("reservations", []) == []
    assert t["classification"]["express"] == []
    assert any("approv" in u for u in t["classification"]["unmapped"])


def test_exemption_from_four_eyes_is_not_a_reservation():
    # Naming the four-eyes principle to exempt from it must not draft the
    # two-hand reservation it lifts.
    t = P.ingest("Low-value transfers are exempt from the four-eyes principle.")
    assert t["ok"], t.get("errors")
    assert t["patch"].get("reservations", []) == []
    assert t["classification"]["express"] == []


def test_negated_dual_control_is_not_a_reservation():
    # "no longer subject to dual control" lifts the rule; drafting two hands
    # would invert the stated exemption.
    t = P.ingest("Legacy jobs are no longer subject to dual control.")
    assert t["ok"], t.get("errors")
    assert t["patch"].get("reservations", []) == []
    assert t["classification"]["express"] == []


def test_negated_requires_approval_by_stays_unmapped():
    # "do not require approval by X" states an exemption: no reservation is
    # drafted; the sentence is surfaced to the residual backstop for review.
    t = P.ingest("Invoices under 50 euros do not require approval by the finance officer.")
    assert t["ok"], t.get("errors")
    assert t["patch"].get("reservations", []) == []
    assert t["classification"]["express"] == []
    assert any("do not require approval" in u for u in t["classification"]["unmapped"])


# ---- Temporal windows on reservations (duration <d> : <on-elapse>) ----------

def test_duration_window_defaults_to_halt():
    # A deadline bound to an approval drafts the duration clause with the
    # fail-closed default disposition: the action halts when the window elapses.
    t = P.ingest("Deletion requests must be approved by a data protection officer "
                 "within 30 days.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == [
        "reserve deletion_request by data_protection_officer duration 30d : halt"]
    r = t["patch"]["reservations"][0]
    assert r["duration"] == "30d" and r["on_elapse"] == "halt"
    p = t["projection"]["reservations"][0]
    assert p["duration"] == "30d" and p["on_elapse"] == "halt"
    _roundtrips(t)


def test_duration_explicit_proceed_on_expiry():
    # "deemed approved" states the action proceeds when the window elapses —
    # the only reading that drafts proceed instead of the halt default.
    t = P.ingest("Publishing requests must be approved by the editor within 30 days, "
                 "otherwise the request is deemed approved.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == [
        "reserve publishing_request by editor duration 30d : proceed"]
    r = t["patch"]["reservations"][0]
    assert r["duration"] == "30d" and r["on_elapse"] == "proceed"
    _roundtrips(t)


def test_duration_deadline_and_cooling_off_forms():
    # "no later than" reads as a window in hours; a cooling-off period reads as
    # a window in days. Neither pollutes the role: the window is cut off the
    # approver capture and carried as the duration clause.
    t = P.ingest("Refunds must be approved by a supervisor no later than 72 hours "
                 "after the request.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == [
        "reserve refund by supervisor duration 72h : halt"]
    _roundtrips(t)
    t = P.ingest("Account closures must be approved by an administrator after "
                 "a 14-day cooling-off period.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == [
        "reserve account_closure by administrator duration 14d : halt"]
    _roundtrips(t)


def test_unattached_window_is_host_not_express():
    # A deadline with no reservation in the sentence to attach to is a schedule
    # the host measures: classified host, never drafted as an express duration.
    t = P.ingest("Incident reports must be submitted to the regulator within 72 hours.")
    assert t["ok"], t.get("errors")
    assert t["classification"]["express"] == []
    assert t["patch"].get("reservations", []) == []
    assert any("within 72 hours" in h for h in t["classification"]["host"])
    assert t["classification"]["unmapped"] == []


def test_p2_bare_may_obligation_is_surfaced():
    # A bare-"may" obligation nothing else maps must reach the backstop (pre-fix
    # the trigger required must/shall/should/require or "may not", missing "may").
    t = P.ingest("Operators may override the recommendation.")
    assert t["ok"], t.get("errors")
    assert any("override" in u for u in t["classification"]["unmapped"])
