#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the Coverage lens (another read-only view of governance_graph).

Seeds ONE registered workspace with two agents and two use cases whose authority
differs — uc-x is allowed only to bot-a, uc-y to both — boots serve.py, and
checks the header "Coverage" lens reads that authority correctly: a filled
"may run" cell where authority exists, the gap ("no authority") where it doesn't.

  python3 app/coverage_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
PORT = 8881
HERE = Path(__file__).parent
tmp = os.path.realpath(tempfile.mkdtemp(prefix="coverage_"))  # resolve /var->/private/var so paths match the registry
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
A = os.path.join(tmp, "alpha")
os.makedirs(A, exist_ok=True)
S.workspace_workspace("add", {"folder_context": A})
for bot in ("bot-a", "bot-b"):
    S.workspace_policy("party_register", {"folder_context": A, "party_id": bot,
                                          "kind": "agent", "actor": "alex"})
# uc-x: only bot-a may run it (bot-b is the coverage gap). uc-y: both may.
S.workspace_workflow("use_case_register", {
    "folder_context": A, "use_case_id": "uc-x", "name": "Task X",
    "fingerprint": {"issue_type": "x"}, "risk": "high",
    "allowed_agents": ["bot-a"], "actor": "alex"})
S.workspace_workflow("use_case_register", {
    "folder_context": A, "use_case_id": "uc-y", "name": "Task Y",
    "fingerprint": {"issue_type": "y"}, "risk": "low",
    "allowed_agents": ["bot-a", "bot-b"], "actor": "alex"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "coverage_render.mjs"), str(PORT), A],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
