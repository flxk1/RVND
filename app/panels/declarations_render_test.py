#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for #40 — authoring declarations on a gate from the Inspector.

Boots serve.py with one registered use case, then runs declarations_render.mjs
which loads index.html in jsdom, selects the gate, and authors all four
declaration types through the add-form: a reservation (reaches the chain), a
second reservation (accumulates — sticky), an obligation (persists as a duty),
a redress route (persists as a remedy), and a prohibit (severs the gate).

  python3 app/declarations_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="decl_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402
import rvnd.mcp_server as S          # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)


def main() -> int:
    S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "uc-draft",
                                          "name": "uc-draft", "fingerprint": {"issue_type": "liability_cap"},
                                          "risk": "low", "allowed_agents": [], "actor": "alex"})
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "declarations_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=50)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
