#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for S5 slice 1 — "Save env" (environment save via workspace_session).

Seeds two registered workspaces (each with a chain), boots serve.py, and checks
that the header "Save env" action captures the whole rail as one signed .rvnd
that verifies through the live MCP.

  python3 app/session_env_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
HERE = Path(__file__).parent
tmp = os.path.realpath(tempfile.mkdtemp(prefix="session_env_"))  # resolve /var->/private/var so paths match the registry
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
A = os.path.join(tmp, "alpha")
B = os.path.join(tmp, "beta")
for f, bot in ((A, "bot-a"), (B, "bot-b")):
    os.makedirs(f, exist_ok=True)
    S.workspace_workspace("add", {"folder_context": f})
    S.workspace_policy("party_register", {"folder_context": f, "party_id": bot,
                                          "kind": "agent", "actor": "alex"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "session_env_render.mjs"), str(PORT), A, B],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
