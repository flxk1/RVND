#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Sync gate for the five-widget front door's shared store.

Seeds two workspaces with distinct agent counts (two agents, one agent), boots
serve.py, and drives the real console.html in jsdom through the shared store
(units/state.mjs). Asserts the loop is synchronous: one mutation reflects in
every surface in the same tick with the version bumped once, and a workspace
switch re-projects the outside before any paint with no prior-workspace leak
(the distinct agent counts make a leaked outside visible on the Read screen).

  python3 app/console_sync_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="console_sync_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402

# Workspace A: two agents. Workspace B: one agent. The counts are the leak
# detector — B's outside must read "1 of 1", never A's "2 of 2".
F_A = os.path.join(tmp, "alpha")
F_B = os.path.join(tmp, "beta")
os.makedirs(F_A, exist_ok=True)
os.makedirs(F_B, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F_A})
S.workspace_workspace("add", {"folder_context": F_B})
for _pid in ("a1", "a2"):
    S.workspace_policy("party_register", {"folder_context": F_A, "party_id": _pid,
                                          "kind": "agent", "grade": "L1", "actor": "alex"})
S.workspace_policy("party_register", {"folder_context": F_B, "party_id": "b1",
                                      "kind": "agent", "grade": "L1", "actor": "alex"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "console_sync.mjs"), str(PORT), F_A, F_B],
                           capture_output=True, text=True, timeout=45)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
