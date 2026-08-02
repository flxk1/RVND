#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the Egress board ("which tracks can act outside?").

Seeds ONE registered workspace with three egress tracks in three cable states —
armed (env ref that resolves), no-cable (no ref), unplugged (ref to a missing
env var) — plus one ingress connector that must not appear, then boots serve.py
and drives the board through JSDOM.

  python3 app/egress_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
PORT = 8883
HERE = Path(__file__).parent
tmp = os.path.realpath(tempfile.mkdtemp(prefix="egress_"))  # resolve /var->/private/var so paths match the registry
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
os.environ["EG_TOK"] = "EG-SECRET-VALUE"          # resolves -> armed; must never reach the DOM
os.environ.pop("EG_MISSING", None)                 # dangling -> unplugged
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
A = os.path.join(tmp, "alpha")
os.makedirs(A, exist_ok=True)
S.workspace_workspace("add", {"folder_context": A})
for cid, role, ch, extra in (
        ("mail-in", "ingress", "email", {}),
        ("jira-out", "egress", "ticket", {"floor": "hold", "credential_ref": "env:EG_TOK",
                                          "destination_class": "tool_api"}),
        ("mail-out", "egress", "email", {}),
        ("dead-out", "egress", "api", {"credential_ref": "env:EG_MISSING"})):
    S.workspace_workflow("connector_register", dict(
        {"folder_context": A, "connector_id": cid, "role": role,
         "channel": ch, "actor": "alex"}, **extra))


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "egress_render.mjs"), str(PORT), A],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
