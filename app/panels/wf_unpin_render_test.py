#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render gate for GUI-6 — workflow delete + skill unpin (two alignment gaps).

Boots serve.py, seeds a workflow definition and a pinned skill, then drives
wf_unpin_render.mjs which deletes the workflow from the board and unpins the
skill, asserting each item disappears. Wires workspace_workflow 'delete' and
workspace_dispatch 'unpin' — lists that previously had no remove action.

  python3 app/wf_unpin_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="wfunpin_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
F = os.path.join(tmp, "org"); os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})
S.workspace_workflow("define", {"folder_context": F, "name": "nightly",
    "steps": [{"skill_id": "workspace:noop", "query": "x", "on_failure": "stop"}],
    "description": "a nightly cleanup"})
S.workspace_dispatch("pin", {"folder_context": F, "skill_id": "demo:skill", "pinned_by": "alex", "note": "demo"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "wf_unpin_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=45)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
