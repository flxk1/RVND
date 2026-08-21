#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test: the Policy map panel maps PASTED policy text over the LIVE governance_map op,
renders grouped rules, and the ask box narrows via a question — the paste→map→ask a user
actually performs, through the real wired panel (not a synthetic payload; that is the
panel-pin test's job). Also proves the universal chat routes a pasted policy over
governance_chat. Closes the "UI unverified for a user" gap from the commit review.

  python3 app/governance_map_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="gmapview_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, rvnd.mcp_server as S  # noqa: E402
F = os.path.join(tmp, "gmap-ws")
os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})   # a real (empty) workspace to focus


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "governance_map_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
