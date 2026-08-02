#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the Verdict Router node (visualize-only). Adds the node from
the Build menu and asserts it shows the six verdict→handling rows, lights a verdict
present on a cord, leaves absent verdicts dim, carries no port/button (it changes
nothing), and survives a server reload.

  python3 app/verdict_router_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="vrouter_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
F = os.path.join(tmp, "acme")
os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})
# a person to route a sign-off to, and a task the router can act on
S.workspace_policy("party_register", {"folder_context": F, "party_id": "jordan", "kind": "human", "name": "Jordan", "actor": "alex"})
S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "review-task", "name": "Review task",
                                       "fingerprint": {}, "risk": "high", "allowed_agents": [], "actor": "alex"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "verdict_router_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=45)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
