#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render gate for GUI-3 — the federated verdict in the Check panel.

Boots serve.py, seeds a tool channel that records a deny (local stays permit), then
drives federated_verdict_render.mjs which asserts the Check panel shows the joined
strictest-wins verdict (deny) with a ⚠ disagreement badge.

  python3 app/federated_verdict_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="fedv_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
F = os.path.join(tmp, "acme"); os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})
# a tool channel linked to use case 'score'; it records a failing tier → deny, while
# the local verdict is permit → the join is deny and the sources disagree.
S.workspace_workflow("connector_register", {"folder_context": F, "connector_id": "n8n-jira",
    "role": "oversight", "channel": "jira", "use_cases": ["score"], "group": "n8n"})
S.workspace_workflow("tool_verdict", {"folder_context": F, "connector_id": "n8n-jira", "raw_tier": "fail"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "federated_verdict_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
