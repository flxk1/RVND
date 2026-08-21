#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for unit chrome gating in the console.

Boots serve.py with a declared principal header and an approver-role party,
then runs unit_gating_render.mjs twice against the same server: once with the
identity header (the approver's chrome collapses to none of the Set up/Rules/
Pending/Record menus or view buttons — the widget is its unit), once with the
header undeclared (local single-operator mode: everything renders). Gating is
presentation over the server's /whoami answer; the enforcement (read scoping,
write gates) is covered by server/tests/test_proxy_identity.py.

  python3 app/unit_gating_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="unitgate_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ["WORKSPACE_PRINCIPAL_HEADER"] = "X-Auth-Request-Email"
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, rvnd.mcp_server as S  # noqa: E402
from rvnd.parties import register_party  # noqa: E402

WHO = "vera\x40corp.example"
F = os.path.join(tmp, "fanclub-crm"); os.makedirs(F, exist_ok=True)
LOG = os.environ["WORKSPACE_L0_LOG_ROOT"]
S.workspace_workspace("add", {"folder_context": F})
register_party(F, party_id=WHO, kind="human", name="Vera", role="approver",
               competences=["data-protection"], actor="alex", log_root=LOG)


def _run(port: int, mode: str) -> bool:
    cmd = ["node", str(HERE / "unit_gating_render.mjs"), str(port), F, mode]
    if mode == "principal":
        cmd.append(WHO)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    print((r.stdout + r.stderr).strip())
    return r.returncode == 0 and "PASS" in r.stdout


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        ok = _run(port, "principal")
        # the bridge reads its principal-header declaration per request, so
        # undeclaring it here flips the same server into local mode
        del os.environ["WORKSPACE_PRINCIPAL_HEADER"]
        ok = _run(port, "local") and ok
    finally:
        srv.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
