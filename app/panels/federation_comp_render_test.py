#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the federated-verdict composition inspector.

Seeds one use case with a denying tool, a floor-only channel and a muted
channel that had said deny, boots serve.py, then runs
federation_comp_render.mjs against it. No new server op — the panel renders
what federated_decision already returns.

  python3 app/federation_comp_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="fedcomp_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, rvnd.mcp_server as S  # noqa: E402

F = os.path.join(tmp, "acme"); os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})
# a tool that said deny (the dominator), on the client bus "n8n"
S.workspace_workflow("connector_register", {"folder_context": F, "connector_id": "deny-bot",
    "role": "oversight", "channel": "jira", "use_cases": ["score"], "group": "n8n"})
S.workspace_workflow("tool_verdict", {"folder_context": F, "connector_id": "deny-bot", "raw_tier": "fail"})
# a channel with no verdict, contributing only through its hold floor
S.workspace_workflow("connector_register", {"folder_context": F, "connector_id": "floor-bot",
    "role": "ingress", "channel": "api", "use_cases": ["score"], "group": "n8n", "floor": "hold"})
# a muted channel that had said deny — must stay visible, struck, last state shown
S.workspace_workflow("connector_register", {"folder_context": F, "connector_id": "muted-bot",
    "role": "oversight", "channel": "slack", "use_cases": ["score"]})
S.workspace_workflow("tool_verdict", {"folder_context": F, "connector_id": "muted-bot", "raw_tier": "fail"})
S.workspace_workflow("tool_revoke", {"folder_context": F, "connector_id": "muted-bot", "actor": "alex"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "federation_comp_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
