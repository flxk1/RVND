#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for co-decision panel state. Seeds one pending panel
decision (3 seats, m_concordant m=2) with one seat recorded, boots serve.py,
runs decision_panel_render.mjs.

  python3 app/decision_panel_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="decpanel_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, rvnd.mcp_server as S  # noqa: E402

F = os.path.join(tmp, "fanclub-crm"); os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})
did = S.workspace_dispatch("decision_open", {"folder_context": F, "surface": {
    "query": "Erase K.'s record while invoices sit in the retention window?",
    "options": [
        {"id": "erase", "label": "Erase everything now", "conclusion": "erase",
         "supporting": [], "consequences": []},
        {"id": "split", "label": "Split the records", "conclusion": "split",
         "supporting": [], "consequences": []}]},
    "raised_by": "crm-bot", "auto_notify": False,
    "panel": {"seats": 3, "rule": "m_concordant", "m": 2}})["decision_id"]
S.workspace_dispatch("decision_record", {
    "folder_context": F, "decision_id": did, "actor": "dana",
    "chosen_option_id": "erase", "rationale": "my private seat grounds"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "decision_panel_render.mjs"),
                            str(PORT), F],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
