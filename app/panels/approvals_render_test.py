#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the Approvals inbox (workspace_contract).

Boots serve.py, seeds one 2-signer pending approval request, then runs
approvals_render.mjs which opens the drawer and exercises the write round-trip:
approving one signer leaves the request pending; approving every signer clears
it from the pending inbox.

  python3 app/approvals_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="appr_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402
import workspaces.mcp_server as S          # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)


def main() -> int:
    S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "u",
                                          "name": "u", "fingerprint": {"issue_type": "automated_decision"},
                                          "risk": "high", "allowed_agents": [], "actor": "alex"})
    S.workspace_contract("request_approval", {"folder_context": F, "contract_id": "c-1",
                                         "signers": ["dpo", "ciso"], "requested_by": "agent-1",
                                         "reason": "deploy model X to prod", "action_summary": "deploy model X"})
    # a signer whose name contains a single quote — exercises onclick value escaping
    S.workspace_contract("request_approval", {"folder_context": F, "contract_id": "c-2",
                                         "signers": ["o'brien"], "requested_by": "agent-2",
                                         "reason": "tricky name", "action_summary": "tricky"})
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "approvals_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=45)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
