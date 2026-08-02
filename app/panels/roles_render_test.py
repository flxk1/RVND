#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the Roles & competence drawer (Set up).

Seeds one registered workspace with a competent human (dana, DPO,
data-protection, one channel) and a bare agent (bot-a), boots serve.py, and
checks the panel: grouping, competence chips, role, worded status, the
no-competence note, and the governed register write from the form.

  python3 app/roles_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
PORT = 8889
HERE = Path(__file__).parent
tmp = os.path.realpath(tempfile.mkdtemp(prefix="roles_"))
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
from workspaces.parties import register_party  # noqa: E402
LR = os.environ["WORKSPACE_L0_LOG_ROOT"]
A = os.path.join(tmp, "alpha")
os.makedirs(A, exist_ok=True)
S.workspace_workspace("add", {"folder_context": A})
register_party(A, "dana", "human", name="dana", role="DPO",
               competences=["data-protection"],
               channels=["email:dana\x40corp.example"], actor="alex", log_root=LR)
register_party(A, "bot-a", "agent", name="bot-a", actor="alex", log_root=LR)


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "roles_render.mjs"), str(PORT), A],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
