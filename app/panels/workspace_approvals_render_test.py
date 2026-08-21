#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the §1.5 reservation-approval card in the Sign-offs inbox.

Boots serve.py, seeds one §1.5 role-quorum approval (the shape the reservation bridge
produces — 2 of {legal, finance, risk}) AND one named-signer contract review, then runs
workspace_approvals_render.mjs to assert both render together: the quorum meter + role set
for the reservation approval, beside the contract review. The two engines stay separate.

  python3 app/workspace_approvals_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="wsappr_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402
import rvnd.mcp_server as S     # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)


def main() -> int:
    now = time.time()
    # a §1.5 role-quorum approval (what request_from_reservation produces for
    # `reserve … by 2 of {legal, finance, risk}`): role-based, no identities.
    S.workspace_workflow("approval_request", {
        "folder_context": F, "request_id": "rq-quorum", "form": "four_eyes",
        "quorum": 2, "competences": ["legal", "finance", "risk"],
        "requester": "agent-1", "now": now})
    # a named-signer contract review, to prove the two engines coexist in one inbox.
    S.workspace_contract("request_approval", {
        "folder_context": F, "contract_id": "c-1", "signers": ["dpo", "ciso"],
        "requested_by": "agent-2", "reason": "deploy", "action_summary": "deploy model X"})
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    # Watchdog-retry (ticker-gate discipline): this is the fleet's heaviest
    # gate and the composed page now carries always-on chrome (the governance
    # strip) that polls while gates run, so a slow CI box can stall the jsdom
    # boot past one budget. Retry ONLY on a stall/timeout — a real assertion
    # failure returns immediately, unmasked.
    try:
        out = ""
        for attempt in range(3):
            try:
                r = subprocess.run(["node", str(HERE / "workspace_approvals_render.mjs"), str(PORT), F],
                                   capture_output=True, text=True, timeout=45)
                out = (r.stdout + r.stderr).strip()
                if "PASS" in r.stdout:
                    print(out)
                    return 0
                if "watchdog" not in out:          # real assertion failure → don't retry
                    print(out)
                    return 1
            except subprocess.TimeoutExpired:
                out = f"node timed out (attempt {attempt + 1}) — jsdom boot stall"
            print(f"[retry] {out}")
        print(out)
        return 1
    finally:
        srv.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
