#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render gate for GUI-7 — jurisdiction packs + delegate signing in Protections.

Boots serve.py, seeds two active human parties, then drives policy_extra_render.mjs
which (in the Protections panel) sets the folder's jurisdiction-pack stack and
delegates signing authority boss→deputy. Wires workspace_policy juris_packs +
delegate_signing — governance ops with no prior UI path.

  python3 app/policy_extra_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="polx_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, rvnd.mcp_server as S  # noqa: E402
F = os.path.join(tmp, "org"); os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})
# two active human signers — delegate_signing refuses unless the delegator is one
S.workspace_policy("party_register", {"folder_context": F, "party_id": "boss", "kind": "human", "role": "controller"})
S.workspace_policy("party_register", {"folder_context": F, "party_id": "deputy", "kind": "human", "role": "deputy"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "policy_extra_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=45)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
