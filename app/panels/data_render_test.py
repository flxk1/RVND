#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the Local data drawer (workspace_memory / workspace_mirror).
This is a READ+WRITE drawer (so it carries NO read-only badge — see slice_e).
Asserts Memory + Mirror render, the erase and bring-in sections are re-homed away
(Rules > Erasure, Set up > Bring-in), no percentage/dial, the memory remember
and mirror generate + approve round-trips, and confirm-gating on un_redact.

  python3 app/data_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="data_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402
import workspaces.mcp_server as S          # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)
MIRROR_SRC = os.path.join(F, "mirror_me.txt")
Path(MIRROR_SRC).write_text("Contact the data subject at victim\x40example.com about the case.\n")


def main() -> int:
    S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "u",
                                          "name": "u", "fingerprint": {"issue_type": "automated_decision"},
                                          "risk": "high", "allowed_agents": [], "actor": "alex"})
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "data_render.mjs"), str(PORT), F, MIRROR_SRC],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
