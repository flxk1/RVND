# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Per-operation fixtures for the executable coverage harness.

Each supported operation needs a valid fixture reaching a schema-conforming
success and an invalid fixture that is refused, driven from a disposable
workspace through every channel its register status claims. No mock backends —
an operation whose success needs external inference (an LLM tier) is not proven
callable in a clean candidate and is deferred, not faked.

A ``check`` tightens success beyond "no error" where an impl wraps a logical
failure as ok=True (it must assert the intended dispatcher branch actually ran).
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from .coverage_harness import WS, Fixture, empty_result, with_parties
except ImportError:  # loaded top-level by the isolated subprocess runner
    from coverage_harness import WS, Fixture, empty_result, with_parties

ACTOR = "operator"
AGENT = "svc-bot"
FP = {"issue_type": "liability_cap"}


# --- defaults -------------------------------------------------------------

def default_valid(ws: WS, ctx: dict) -> dict:
    return {"folder_context": ws.folder, "actor": ACTOR}


def default_invalid(ws: WS, ctx: dict) -> dict:
    return {}


DEFAULT = Fixture(valid=default_valid, invalid=default_invalid,
                  setup=lambda ws: with_parties(ws) and {})


def _sees_nothing(resp):
    if isinstance(resp, dict) and resp.get("state") == "empty":
        return True
    return empty_result(resp)


def _p(ws: WS) -> dict:            # parties only
    with_parties(ws)
    return {}


# --- setup helpers: create the referenced entity, return its id -----------

def _use_case(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    M.workspace_workflow("use_case_register", {
        "folder_context": ws.folder, "use_case_id": "uc1", "name": "uc1",
        "fingerprint": FP, "risk": "low", "allowed_agents": [AGENT], "actor": ACTOR})
    return {"use_case_id": "uc1"}


def _governance_lane(ws: WS) -> dict:
    with_parties(ws)
    from rvnd.governance_lane import GovernanceLane, register_lane
    from rvnd.mcp_serving import set_request_principal

    register_lane(
        ws.folder,
        GovernanceLane(
            lane_id="lane-svc-bot",
            agent=AGENT,
            max_grade="L2",
            action_classes=("classify",),
            folder=ws.folder,
            policy_fingerprint="sha256:coverage-approved",
            approved_by=ACTOR,
            rationale="coverage admission fixture",
        ),
        log_root=Path(ws.log_root),
    )
    set_request_principal(AGENT, AGENT)
    return {}


def _connector(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    M.workspace_workflow("connector_register", {
        "folder_context": ws.folder, "connector_id": "c1",
        "role": "egress", "channel": "email", "actor": ACTOR})
    return {"connector_id": "c1"}


def _run(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    M.workspace_workflow("define", {
        "folder_context": ws.folder, "name": "wf1",
        "steps": [{"skill_id": "x:y", "query": "q"}]})
    r = M.workspace_workflow("enqueue", {
        "folder_context": ws.folder, "name": "wf1", "enqueued_by": ACTOR})
    return {"run_id": r.get("run_id")}


def _approval_wf(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    M.workspace_workflow("approval_request", {
        "folder_context": ws.folder, "request_id": "r1", "now": 1000.0, "actor": ACTOR})
    return {"request_id": "r1"}


def _two_humans(ws: WS) -> dict:
    from rvnd.parties import register_party
    register_party(ws.folder, "operator", "human", log_root=ws.log_root)
    register_party(ws.folder, "approver", "human", log_root=ws.log_root)
    return {}


def _an_event(ws: WS) -> dict:
    with_parties(ws)
    from rvnd.mutation_log import MutationLog
    log = MutationLog(ws.folder, log_root=ws.log_root)
    for e in log.replay():
        if getattr(e, "audit_id", ""):
            return {"event_id": e.audit_id}
    return {"event_id": ""}


def _bundle(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    r = M.workspace_session("build", {
        "workspaces": [{"folder_context": ws.folder, "id": "ws1", "name": "ws1"}],
        "rail": {}, "name": "sess"})
    return {"bundle": r.get("bundle")}


def _work(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    r = M.workspace_grounder("work.register", {"folder_context": ws.folder, "title": "Traced work"})
    return {"work_id": r.get("id")}


def _two_works(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    a = M.workspace_grounder("work.register", {"folder_context": ws.folder, "title": "Citing"})
    b = M.workspace_grounder("work.register", {"folder_context": ws.folder, "title": "Cited"})
    return {"from_work": a.get("id"), "to_work": b.get("id")}


def _claim(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    r = M.workspace_grounder("ground", {
        "folder_context": ws.folder, "claim": "The sky is blue.",
        "works": [{"title": "Colour of the sky"}]})
    return {"claim_id": (r.get("claim") or {}).get("id")}


def _contract(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    cid = "C-COV-1"
    M.workspace_contract("ingest", {
        "folder_context": ws.folder,
        "text": "Party A shall deliver the report by 2026-08-01.",
        "contract_id": cid, "actor": ACTOR})
    return {"contract_id": cid}


def _approval_contract(ws: WS) -> dict:
    import rvnd.mcp_server as M
    ctx = _contract(ws)
    r = M.workspace_contract("request_approval", {
        "folder_context": ws.folder, "contract_id": ctx["contract_id"],
        "signers": ["operator"], "action_summary": "Approve delivery terms",
        "requested_by": "system"})
    return {"contract_id": ctx["contract_id"], "approval_id": r.get("approval_id")}


_DEC_SURFACE = {
    "query": "Erase the record while invoices sit in the retention window?",
    "esc_reason": "erase duty vs keep-ten-years duty",
    "options": [
        {"id": "erase", "label": "Erase now", "conclusion": "erase", "supporting": [], "consequences": []},
        {"id": "split", "label": "Split records", "conclusion": "split", "supporting": [], "consequences": []},
    ],
}


def _open_decision(ws: WS) -> str:
    with_parties(ws)
    from rvnd.mcp_impl import decision_open
    return decision_open(ws.folder, _DEC_SURFACE, "crm-bot",
                         competence="data-protection", auto_notify=False)["decision_id"]


def _open(ws: WS) -> dict:
    return {"decision_id": _open_decision(ws)}


def _open_claimed(ws: WS) -> dict:
    from rvnd.mcp_impl import decision_claim
    did = _open_decision(ws)
    decision_claim(ws.folder, did, "dana")
    return {"decision_id": did}


def _precedent(ws: WS) -> dict:
    with_parties(ws)
    import rvnd.mcp_server as M
    M.workspace_lens("precedent_declare", {"folder_context": ws.folder, "id": "prec-1",
                     "actor": ACTOR, "chosen_option": "opt-a", "rationale": "human origination"})
    return {"id": "prec-1"}


def _mirror_draft(ws: WS) -> dict:
    with_parties(ws)
    lock = Path(ws.folder) / "mirrors" / "lock"
    lock.mkdir(parents=True, exist_ok=True)
    mirror = lock / "doc.cleaned.md"
    mirror.write_text("Hello [REDACTED:email] please confirm.\n", encoding="utf-8")
    (lock / "doc.spans.json").write_text(json.dumps({
        "schema": "workspace.mirror.spans/v1",
        "source_path": str(Path(ws.folder) / "doc.md"),
        "source_hash": "sha256:abc", "mirror_kind": "lock", "created_at": 0,
        "spans": [{"start": 6, "end": 17, "kind": "tier_b.pii_in_argument",
                   "original_hash": "sha256:zzz", "replacement": "[REDACTED:email]",
                   "span_id": "span:e1"}]}), encoding="utf-8")
    from rvnd import mirror_editor
    mirror_editor.open_revision(ws.folder, mirror, actor="system:editor", log_root=Path(ws.log_root))
    return {"mirror_path": str(mirror)}


import time as _time


def _g(ws, c):    # a global-read valid: folder + actor
    return {"folder_context": ws.folder, "actor": ACTOR}


def _gf(ws, c):   # a global-read valid: folder only
    return {"folder_context": ws.folder}


FIXTURES: dict[tuple, Fixture] = {
    # ---- standalone tools -------------------------------------------------
    ("server_info", None): Fixture(
        valid=lambda ws, c: {},
        invalid=lambda ws, c: {"request": {"unexpected": True}},
        check=lambda r: isinstance(r, dict) and not r.get("error")),
    ("cross_workspace_read", None): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "sources": [ws.folder]},
        invalid=lambda ws, c: {"folder_context": ws.folder, "sources": [ws.folder]},
        setup=_p, invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_orchestrate", None): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "query": "status"},
        invalid=lambda ws, c: {}, setup=_p, mutating=True),

    # ---- workspace_folder -------------------------------------------------
    ("workspace_folder", "list"): Fixture(valid=lambda ws, c: {"path": ws.folder},
                                          invalid=lambda ws, c: {}),
    ("workspace_folder", "write_file"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "relative_path": "note.txt", "content": "hi"},
        invalid=lambda ws, c: {}, mutating=True),

    # ---- global reads whose refusal is unauthorized-principal ------------
    ("workspace_workspace", "list"): Fixture(setup=_p, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_conformity", "threat_model"): Fixture(setup=_p, valid=_g, invalid=_g,
        invalid_unmapped=True, invalid_check=empty_result),

    # ---- workspace_capture (records an exchange; not inference) -----------
    ("workspace_capture", "llm"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "model": "test-model",
                             "prompt_context": "q", "response": "a", "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder}, mutating=True),

    # ---- workspace_lock ---------------------------------------------------
    ("workspace_lock", "audit_query"): Fixture(
        valid=lambda ws, c: {"reason_for_query": "monthly compliance review"},
        invalid=lambda ws, c: {"reason_for_query": "   "}),
    ("workspace_lock", "classify"): Fixture(
        valid=lambda ws, c: {"text": "email jane.doe\x40example.com or call 555-123-4567"},
        invalid=lambda ws, c: {}),
    ("workspace_lock", "egress_check"): Fixture(
        valid=lambda ws, c: {"tool": "hr.get_employee",
                             "arguments": {"employee_id": "E1", "ssn": "078-05-1120"},
                             "task_scope": ["employee_id"]},
        invalid=lambda ws, c: {}),
    ("workspace_lock", "ingress_check"): Fixture(
        valid=lambda ws, c: {"payload": {"name": "Jane", "ssn": "078-05-1120"}, "task_scope": ["name"]},
        invalid=lambda ws, c: {}),
    ("workspace_lock", "threshold_set"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "threshold": 0.5},
        invalid=lambda ws, c: {"folder_context": ws.folder},
        check=lambda r: r.get("ok") is True and "previous" in r, mutating=True),
    ("workspace_lock", "threshold_get"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: "threshold" in r),
    ("workspace_lock", "setup_status"): Fixture(
        valid=lambda ws, c: {}, invalid=None,
        check=lambda r: r.get("ok") is True and "configured" in r),

    # ---- workspace_model : only the inference-free read -------------------
    ("workspace_model", "attest_status"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder},
        invalid=lambda ws, c: {"folder_context": ws.folder},
        check=lambda r: r.get("ok") is True and isinstance(r.get("models"), list),
        invalid_unmapped=True, invalid_check=empty_result),

    # ---- workspace_lens ---------------------------------------------------
    ("workspace_lens", "budget_cap_set"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "cap": 5.0}, invalid=lambda ws, c: {}, mutating=True),
    ("workspace_lens", "precedent_declare"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "id": "prec-1", "actor": ACTOR,
                             "chosen_option": "opt-a", "rationale": "human origination"},
        invalid=lambda ws, c: {}, setup=_p, mutating=True),
    ("workspace_lens", "precedent_revoke"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "id": c["id"], "actor": ACTOR, "reason": "superseded"},
        invalid=lambda ws, c: {}, setup=_precedent, mutating=True),
    ("workspace_lens", "precedent_list"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {"folder_context": ws.folder},
        setup=_precedent, invalid_unmapped=True, invalid_check=empty_result),

    # ---- workspace_mirror -------------------------------------------------
    ("workspace_mirror", "un_redact"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "mirror_path": c["mirror_path"],
                             "span_id": "span:e1", "controller_key": "<TEST-OVERRIDE>",
                             "original_text": "bob\x40example.com", "recheck": True},
        invalid=lambda ws, c: {"folder_context": ws.folder, "mirror_path": c["mirror_path"], "span_id": "span:e1"},
        setup=_mirror_draft, mutating=True),

    # ---- workspace_grounder (gateway) ------------------------------------
    ("workspace_grounder", "work.register"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "title": "Registered work"},
        invalid=lambda ws, c: {"folder_context": ws.folder}, setup=_p, mutating=True),
    ("workspace_grounder", "ground"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "claim": "The sky is blue.",
                             "works": [{"title": "Colour of the sky"}]},
        invalid=lambda ws, c: {"folder_context": ws.folder}, setup=_p, mutating=True),
    ("workspace_grounder", "claim.status"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "claim_id": c["claim_id"], "status": "verified"},
        invalid=lambda ws, c: {"folder_context": ws.folder}, setup=_claim, mutating=True),
    ("workspace_grounder", "provenance.add"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "from_work": c["from_work"],
                             "relation": "cites", "to_work": c["to_work"]},
        invalid=lambda ws, c: {"folder_context": ws.folder}, setup=_two_works, mutating=True),
    ("workspace_grounder", "provenance.trace"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "work_id": c["work_id"]},
        invalid=lambda ws, c: {"folder_context": ws.folder}, setup=_work),
    ("workspace_grounder", "bibliography"): Fixture(valid=_gf, invalid=_gf, setup=_p,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_grounder", "coverage"): Fixture(valid=_gf, invalid=_gf, setup=_p,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_grounder", "swarm.frontier"): Fixture(valid=_gf, invalid=_gf, setup=_p,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_grounder", "entities.link"): Fixture(valid=_gf, invalid=_gf, setup=_p,
        invalid_unmapped=True, invalid_check=empty_result, mutating=True),

    # ---- workspace_contract ----------------------------------------------
    ("workspace_contract", "request_approval"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "contract_id": c["contract_id"],
                             "signers": ["operator"], "action_summary": "Approve delivery"},
        invalid=lambda ws, c: {"folder_context": ws.folder}, setup=_contract, mutating=True),
    ("workspace_contract", "record_approval"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "approval_id": c["approval_id"],
                             "signer": "operator", "decision": "approved"},
        invalid=lambda ws, c: {"folder_context": ws.folder},
        check=lambda r: isinstance(r, dict) and not r.get("error"), setup=_approval_contract, mutating=True),
    ("workspace_contract", "review"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "contract_id": c["contract_id"],
                             "decision": "Approve", "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder}, setup=_contract, mutating=True),
    ("workspace_contract", "list_reviews"): Fixture(valid=_gf, invalid=_gf, setup=_p,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_contract", "list_approvals"): Fixture(valid=_gf, invalid=_gf, setup=_p,
        invalid_unmapped=True, invalid_check=empty_result),

    # ---- workspace_dispatch ----------------------------------------------
    ("workspace_dispatch", "decision_claim"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "decision_id": c["decision_id"], "actor": "dana"},
        invalid=lambda ws, c: {}, setup=_open, mutating=True),
    ("workspace_dispatch", "decision_record"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "decision_id": c["decision_id"],
                             "chosen_option_id": "split", "rationale": "honours both duties", "actor": "dana"},
        invalid=lambda ws, c: {}, setup=_open_claimed, mutating=True),
    ("workspace_dispatch", "decision_release"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "decision_id": c["decision_id"], "actor": "dana"},
        invalid=lambda ws, c: {}, setup=_open_claimed, mutating=True),
    ("workspace_dispatch", "pin_many"): Fixture(
        valid=lambda ws, c: {"folder_context": ws.folder, "skill_ids": ["workspace:governance-map"]},
        invalid=lambda ws, c: {}, setup=_p, mutating=True),
    ("workspace_dispatch", "decision_pending"): Fixture(valid=_gf, invalid=_gf, setup=_p,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_dispatch", "list_pinned"): Fixture(valid=_gf, invalid=_gf, setup=_p,
        invalid_unmapped=True, invalid_check=empty_result),

    # ---- workspace_policy -------------------------------------------------
    ("workspace_policy", "delegate_signing"): Fixture(setup=_two_humans,
        valid=lambda ws, c: {"folder_context": ws.folder, "from_party": "operator", "to_party": "approver", "actor": "operator"},
        invalid=lambda ws, c: {}, mutating=True),
    ("workspace_policy", "party_register"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "party_id": "newagent", "kind": "agent", "actor": ACTOR},
        invalid=lambda ws, c: {}, mutating=True),
    ("workspace_policy", "party_route"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "competence": "approval", "actor": ACTOR},
        invalid=lambda ws, c: {}),
    ("workspace_policy", "party_status"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "party_id": "operator", "status": "suspended", "actor": ACTOR},
        invalid=lambda ws, c: {}, mutating=True),
    ("workspace_policy", "set_lock_mode"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "mode": "clean_room_with_algo", "actor": ACTOR},
        invalid=lambda ws, c: {}, mutating=True),
    ("workspace_policy", "set_oversight_level"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "level": "review", "actor": ACTOR},
        invalid=lambda ws, c: {}, mutating=True),
    ("workspace_policy", "party_list"): Fixture(setup=_p, valid=_g,
        invalid=_g, invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_policy", "snapshot"): Fixture(setup=_p, valid=_gf, invalid=lambda ws, c: {}),
    ("workspace_policy", "juris_packs"): Fixture(setup=_p, valid=_g, invalid=_g,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_policy", "tdm_declare"): Fixture(setup=_p, valid=_g, invalid=lambda ws, c: {}, mutating=True),
    ("workspace_policy", "tdm_optout"): Fixture(setup=_p, valid=_g, invalid=lambda ws, c: {}, mutating=True),

    # ---- workspace_matrix -------------------------------------------------
    ("workspace_matrix", "set_all"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "matrix": {"L0": {"autonomous": "go"}}, "actor": ACTOR},
        invalid=lambda ws, c: {}, mutating=True),

    # ---- workspace_session -----------------------------------------------
    ("workspace_session", "draft_save"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "surface": "chat", "payload": {"text": "hi"}},
        invalid=lambda ws, c: {}, mutating=True),
    ("workspace_session", "verify_bytes"): Fixture(setup=_bundle,
        valid=lambda ws, c: {"bundle": c["bundle"]}, invalid=lambda ws, c: {}),
    ("workspace_session", "draft_load"): Fixture(setup=_p, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_session", "draft_discard"): Fixture(setup=_p, valid=_gf, invalid=lambda ws, c: {}, mutating=True),

    # ---- workspace_audit --------------------------------------------------
    ("workspace_audit", "get_event"): Fixture(setup=_an_event,
        valid=lambda ws, c: {"event_id": c["event_id"], "folder_context": ws.folder},
        invalid=lambda ws, c: {"event_id": "00000000-0000-4000-8000-000000000000", "folder_context": ws.folder}),
    ("workspace_audit", "verify_chain"): Fixture(setup=_p, valid=_gf, invalid=lambda ws, c: {}),
    ("workspace_audit", "shadow_scan"): Fixture(setup=_p, valid=_g, invalid=_g,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_audit", "discipline"): Fixture(setup=_p, valid=_gf, invalid=lambda ws, c: {}),
    ("workspace_audit", "override_recurrence"): Fixture(setup=_p, valid=_g, invalid=_g,
        invalid_unmapped=True, invalid_check=empty_result),

    # ---- workspace_workflow ----------------------------------------------
    ("workspace_workflow", "approval_request"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "request_id": "r1", "now": 1000.0, "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder, "now": 1000.0}, mutating=True),
    ("workspace_workflow", "approval_decide"): Fixture(setup=_approval_wf,
        valid=lambda ws, c: {"folder_context": ws.folder, "request_id": "r1", "decision": "approve", "actor": ACTOR, "now": 2000.0},
        invalid=lambda ws, c: {"folder_context": ws.folder, "decision": "approve", "actor": ACTOR, "now": 2000.0}, mutating=True),
    ("workspace_workflow", "approval_list"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "now": 1000.0}, invalid=lambda ws, c: {"folder_context": ws.folder}),
    ("workspace_workflow", "authority_revoke"): Fixture(setup=_use_case,
        valid=lambda ws, c: {"folder_context": ws.folder, "use_case_id": "uc1", "agent_id": AGENT, "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder, "use_case_id": "uc1"}, mutating=True),
    ("workspace_workflow", "cancel"): Fixture(setup=_run,
        valid=lambda ws, c: {"run_id": c["run_id"]}, invalid=lambda ws, c: {}, mutating=True),
    ("workspace_workflow", "connector_register"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "connector_id": "c1", "role": "egress", "channel": "email", "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder, "connector_id": "c1"}, mutating=True),
    ("workspace_workflow", "federated_decision"): Fixture(setup=_use_case,
        valid=lambda ws, c: {"folder_context": ws.folder, "use_case_id": "uc1"},
        invalid=lambda ws, c: {"folder_context": ws.folder}),
    ("workspace_workflow", "governance_query"): Fixture(setup=_use_case,
        valid=lambda ws, c: {"folder_context": ws.folder, "query": "unfired"},
        invalid=lambda ws, c: {"folder_context": ws.folder}),
    ("workspace_workflow", "group_floor"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "group_id": "grp1", "floor": "hold", "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder}, mutating=True),
    ("workspace_workflow", "group_revoke"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "group_id": "grp1", "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder}, mutating=True),
    ("workspace_workflow", "operate"): Fixture(setup=_use_case,
        valid=lambda ws, c: {"folder_context": ws.folder, "use_case_id": "uc1", "agent_id": AGENT,
                             "issues": [{"issue_id": "i1", "issue_type": "liability_cap", "completeness": "high"}],
                             "now_epoch": int(_time.time())},
        invalid=lambda ws, c: {"folder_context": ws.folder, "agent_id": AGENT, "issues": [], "now_epoch": 0}, mutating=True),
    ("workspace_workflow", "policy_ingest"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "policy_text": "Automated decisions must be reviewed by a human."},
        invalid=lambda ws, c: {"folder_context": ws.folder, "path": "../secret.pdf"}, mutating=True),
    ("workspace_workflow", "tool_revoke"): Fixture(setup=_connector,
        valid=lambda ws, c: {"folder_context": ws.folder, "connector_id": "c1", "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder}, mutating=True),
    ("workspace_workflow", "track_strip"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "party_id": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder}),
    ("workspace_workflow", "use_case_get"): Fixture(setup=_use_case,
        valid=lambda ws, c: {"folder_context": ws.folder, "use_case_id": "uc1"},
        invalid=lambda ws, c: {"folder_context": ws.folder}),
    ("workspace_workflow", "use_case_register"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "use_case_id": "uc1", "name": "uc1",
                             "fingerprint": FP, "risk": "low", "allowed_agents": [AGENT], "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder, "use_case_id": "uc1"}, mutating=True),
    ("workspace_workflow", "queue"): Fixture(setup=_run, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "take_next"): Fixture(setup=_run,
        valid=lambda ws, c: {"worker_id": "w1"}, invalid=lambda ws, c: {"worker_id": "w1"},
        invalid_unmapped=True, invalid_check=_sees_nothing),
    ("workspace_workflow", "inspect_stuck"): Fixture(setup=_p, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "console_snapshot"): Fixture(setup=_p, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "governance_chat"): Fixture(setup=_use_case,
        valid=lambda ws, c: {"folder_context": ws.folder, "text": "what is allowed?"},
        invalid=lambda ws, c: {"folder_context": ws.folder, "text": "what is allowed?"},
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "governance_map"): Fixture(setup=_use_case, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "governance_graph"): Fixture(setup=_use_case, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "governance_netlist"): Fixture(setup=_use_case, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "governance_register"): Fixture(setup=_use_case, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "coverage_matrix"): Fixture(setup=_use_case, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "egress_board"): Fixture(setup=_connector, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "connector_list"): Fixture(setup=_connector, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "transport_audit"): Fixture(setup=_p, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_workflow", "patch_validate"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "netlist": ""},
        invalid=lambda ws, c: {"folder_context": ws.folder, "patch": "not-a-dict"}),
    ("workspace_workflow", "patch_apply"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "actor": ACTOR, "netlist": ""},
        invalid=lambda ws, c: {"folder_context": ws.folder, "actor": ACTOR, "patch": "not-a-dict"}, mutating=True),
}


# Operations promoted from deferred to mcp-supported (proven callable via MCP).
try:
    from .coverage_fixtures_extra import EXTRA
except ImportError:  # top-level (subprocess runner)
    from coverage_fixtures_extra import EXTRA
FIXTURES.update(EXTRA)


# Surface-only fixtures prove UI/gateway routes without declaring that the
# trusted in-process MCP facade has a controlled-return refusal contract.
SURFACE_FIXTURES: dict[tuple, Fixture] = {
    ("workspace_workflow", "governance_open"): Fixture(
        setup=_governance_lane,
        valid=lambda ws, c: {
            "folder_context": ws.folder,
            "party": AGENT,
            "policy_fingerprint": "sha256:coverage-approved",
        },
        invalid=lambda ws, c: {
            "folder_context": ws.folder,
            "party": AGENT,
            "policy_fingerprint": "sha256:not-approved",
        },
        check=lambda r: bool(r.get("capability_token")) and bool(r.get("claims")),
    ),
}
