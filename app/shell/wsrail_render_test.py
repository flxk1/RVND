#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the Workspaces rail (a DAW channel per workspace). Seeds a
parent workspace, a registered child under it (hierarchy), and an independent
workspace — each with an agent + oversight. The rail then renders a strip per
workspace with discrete L0–L4 autonomy LEDs, marks the current one, shows the
child's group-bus tag and the parent's send→, exposes a master All-Stop, and
focuses a workspace when its name button is clicked.

  python3 app/wsrail_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="wsrail_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
PARENT = os.path.join(tmp, "acme-prod")          # parent
CHILD = os.path.join(PARENT, "eu-team")          # registered child of acme-prod
INDEP = os.path.join(tmp, "research")            # independent
for f in (PARENT, CHILD, INDEP):
    os.makedirs(f, exist_ok=True)
    S.workspace_workspace("add", {"folder_context": f})
    S.workspace_policy("party_register", {"folder_context": f, "party_id": "bot", "kind": "agent", "actor": "alex"})
    S.workspace_policy("set_oversight_level", {"folder_context": f, "level": "review", "actor": "alex"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "wsrail_render.mjs"), str(PORT), PARENT, CHILD, INDEP],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
