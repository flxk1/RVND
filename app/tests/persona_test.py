#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Persona click-through test for the Rvnd app.

Five company roles with distinct responsibilities (functional, IT/security,
operational, compliance/risk, data/DPO) each drive the tabs they'd use, through
the same HTTP transport the app uses (serve.py). Exit 0 = every click reached a
working backend. "ok*" = worked but needs config (a model / a sealed store) —
counted as pass, since it's an honest precondition, not a failure.

  python app/persona_test.py
"""
from __future__ import annotations
import sys, json, threading, time, tempfile, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import serve  # noqa: E402
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server" / "src"))
import workspaces.mcp_server as S  # noqa: E402
from workspaces import use_case as UC  # noqa: E402

EXPECTED = ("no model tier configured", "no local-llm", "not sealed", "no local-llm endpoint")


def main() -> int:
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    F = tempfile.mkdtemp()

    def http(tool, args):
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/tool",
            data=json.dumps({"tool": tool, "args": args}).encode(),
            headers={"Content-Type": "application/json",
                     "X-Workspaces-Token": srv.session_token})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=6).read())
        except Exception as e:  # noqa: BLE001
            return {"__exc": str(e)}

    # setup: a workspace, an agent, a human approver, a real run
    S.workspace_workspace("add", {"folder_context": F})
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "svc-bot", "kind": "agent", "owner": "alice", "grade": "L2"})
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "alice", "kind": "human", "competences": ["data-protection", "review"]})
    UC.register_use_case(F, use_case_id="cr", name="CR", fingerprint={"issue_type": "x"}, risk="medium", allowed_agents=["svc-bot"], actor="alice")
    S.workspace_workflow("operate", {"folder_context": F, "use_case_id": "cr", "agent_id": "svc-bot",
                                "issues": [{"issue_id": "i1", "risk": "low"}, {"issue_id": "i2", "issue_type": "data_transfer", "risk": "high"}],
                                "now_epoch": int(time.time())})

    def fp(op, **p):
        return {"op": op, "params": {"folder_context": F, **p}}

    personas = {
        "Functional owner": [
            ("workspace_workflow", fp("runs")),
            ("workspace_workflow", fp("approval_request", request_id="r-fn", now=1000, form="single_approver", competence="review", requester="svc-bot")),
            ("workspace_workflow", fp("approval_decide", request_id="r-fn", decision="approve", actor="alice", now=1001)),
            ("workspace_workflow", fp("approval_resolve", request_id="r-fn", now=1002)),
            ("workspace_orchestrate", {"folder_context": F, "query": "find workspace"}),
            ("workspace_mirror", fp("list")),
        ],
        "IT / Security": [
            ("workspace_audit", fp("verify_chain")),
            ("workspace_policy", fp("snapshot")),
            ("workspace_lock", fp("seal")),
            ("workspace_lock", fp("unseal", passphrase="x")),
            ("workspace_lock", {"op": "classify", "params": {"text": "a\x40b.com IBAN DE89370400440532013000"}}),
            ("workspace_policy", fp("actor_stamps")),
        ],
        "Operational manager": [
            ("workspace_model", fp("cascade", prompt="ping")),
            ("workspace_matrix", fp("show")),
            ("workspace_matrix", fp("set", grade="L3", oversight="notify", light="ask")),
            ("workspace_policy", fp("set_oversight_level", level="approve")),
            ("workspace_lens", fp("budget_cap_get")),
            ("workspace_workflow", fp("runs")),
        ],
        "Compliance / Risk officer": [
            ("workspace_conformity", fp("evidence_pack")),
            ("workspace_conformity", fp("oversight_attestation")),
            ("workspace_conformity", fp("risk_register")),
            ("workspace_audit", {"op": "reserved_acts", "params": {"issue_types": ["data_transfer", "ai_high_risk"]}}),
            ("workspace_audit", fp("calibration")),
            ("workspace_grounder", fp("oversight.feed")),
        ],
        "DPO / Data privacy": [
            ("workspace_erase", fp("request", subject="alice", requester_ref="DSAR-1", reason="erasure")),
            ("workspace_lock", {"op": "classify", "params": {"text": "Bob bob\x40x.com"}}),
            ("workspace_audit", {"op": "reserved_acts", "params": {"issue_types": ["data_transfer"]}}),
            ("workspace_policy", fp("set_oversight_level", level="manual")),
        ],
    }

    def ok(r):
        if isinstance(r, dict):
            if "__exc" in r:
                return False, "exc"
            e = str(r.get("error", "")).lower()
            if "unknown tool" in e:
                return False, "unknown"
            if e and any(x in e for x in EXPECTED):
                return True, "needs-config"
            if e:
                return False, e[:40]
        return True, ""

    total = passed = 0
    bad = []
    for persona, steps in personas.items():
        for tool, args in steps:
            good, note = ok(http(tool, args))
            total += 1
            passed += good
            if not good:
                bad.append(f"{persona}:{tool}/{args.get('op','')} ({note})")
    print(f"persona clicks: {passed}/{total}")
    if bad:
        print("FAILED:", bad)
        print("FAIL")
        return 1
    print("PASS — all persona clicks reached a working backend")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
