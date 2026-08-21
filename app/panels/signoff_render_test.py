#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the sign-off widget page (served at /sign). Seeds two
pending decisions, mints a link for party "dana" on one of them, boots
serve.py, asserts the route serves the wired page, then runs
signoff_render.mjs: a valid token renders exactly the bound decision (never
the other), approve records and the page states the outcome, an invalid token
renders a refusal and lists nothing.

  python3 app/signoff_render_test.py
"""
from __future__ import annotations
import json, os, sys, time, tempfile, threading, subprocess, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="signoff_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, rvnd.mcp_server as S  # noqa: E402

F = os.path.join(tmp, "fanclub-crm"); os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})

BOUND_QUERY = "Erase K.'s record while invoices sit in the retention window?"
OTHER_QUERY = "Renew the mailing-list processor contract for another year?"
SURFACE = {
    "query": BOUND_QUERY,
    "esc_reason": "GDPR Art. 17(1) erase vs § 147(3) AO keep-ten-years",
    "options": [
        {"id": "erase", "label": "Erase everything now", "conclusion": "erase",
         "supporting": [], "consequences": ["the accounting records go too"]},
        {"id": "keep", "label": "Refuse the erasure for now", "conclusion": "keep",
         "supporting": [], "consequences": ["profile stays until the window closes"]},
    ],
}


def seed() -> str:
    did = S.workspace_dispatch("decision_open", {
        "folder_context": F, "surface": SURFACE, "raised_by": "crm-bot",
        "decide_by": "2030-01-01T00:00:00", "escalate_to": "legal",
        "auto_notify": False})["decision_id"]
    # a second open decision the token is not bound to — must never render
    S.workspace_dispatch("decision_open", {
        "folder_context": F, "raised_by": "crm-bot", "auto_notify": False,
        "surface": {"query": OTHER_QUERY, "options": [
            {"id": "renew", "label": "Renew", "conclusion": "renew",
             "supporting": [], "consequences": []}]}})
    out = S.workspace_dispatch("decision_link_mint", {
        "folder_context": F, "decision_id": did, "party_id": "dana"})
    assert out["ok"], out
    return out["token"]


def check_route(port: int) -> None:
    """The /sign route serves the widget page with the bridge wiring injected
    and never echoes any token from the request path."""
    with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/sign?folder=x&token=not-echoed") as r:
        page = r.read().decode()
        assert r.status == 200, r.status
    assert "Rvnd — Sign-off" in page, "widget page not served at /sign"
    assert "window.__WORKSPACES_HTTP__='/tool'" in page, "bridge wiring missing"
    assert "window.__WORKSPACES_TOKEN__" in page, "session token wiring missing"
    assert "not-echoed" not in page, "the served page must not echo URL tokens"


def main() -> int:
    token = seed()
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        check_route(PORT)
        r = subprocess.run(["node", str(HERE / "signoff_render.mjs"),
                            str(PORT), F, token,
                            json.dumps({"bound": BOUND_QUERY, "other": OTHER_QUERY})],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
