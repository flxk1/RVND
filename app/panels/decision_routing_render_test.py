#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for decision routing in the Pending panel.

Seeds three pending decisions — one competence-tagged (holder: app-user), one
raised by app-user itself, one already claimed by another reviewer — boots
serve.py, then runs decision_routing_render.mjs.

  python3 app/decision_routing_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="decroute_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
from workspaces.parties import register_party  # noqa: E402

F = os.path.join(tmp, "fanclub-crm"); os.makedirs(F, exist_ok=True)
LOG = os.environ["WORKSPACE_L0_LOG_ROOT"]
S.workspace_workspace("add", {"folder_context": F})

SURFACE = {
    "query": "Erase K.'s record while invoices sit in the retention window?",
    "esc_reason": "GDPR Art. 17(1) erase vs § 147(3) AO keep-ten-years",
    "options": [
        {"id": "erase", "label": "Erase everything now", "conclusion": "erase",
         "supporting": [], "consequences": ["the accounting records go too"]},
        {"id": "split", "label": "Split the records", "conclusion": "split",
         "supporting": [], "consequences": ["profile gone; invoices frozen"]},
    ],
}


def seed() -> None:
    register_party(F, party_id="app-user", kind="human", name="Operator",
                   competences=["data-protection"], actor="alex", log_root=LOG)
    a = S.workspace_dispatch("decision_open", {"folder_context": F,
        "surface": SURFACE, "raised_by": "crm-bot", "competence": "data-protection"})
    assert a["ok"], a
    own = dict(SURFACE, query="A choice app-user raised itself")
    b = S.workspace_dispatch("decision_open", {"folder_context": F,
        "surface": own, "raised_by": "app-user"})
    assert b["ok"], b
    other = dict(SURFACE, query="A card another reviewer holds")
    c = S.workspace_dispatch("decision_open", {"folder_context": F,
        "surface": other, "raised_by": "crm-bot"})
    assert c["ok"], c
    claimed = S.workspace_dispatch("decision_claim", {"folder_context": F,
        "decision_id": c["decision_id"], "actor": "dana"})
    assert claimed["ok"], claimed


def main() -> int:
    seed()
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "decision_routing_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
