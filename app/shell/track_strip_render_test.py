#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the track channel strip (per-track step 2 — the inspector's
per-track selection detail).

Seeds ONE registered workspace with an agent lane (grade L2, competence legal,
soft-bound to its channel) and an armed egress connector linked to a governed
use case, boots serve.py, and drives both strips through JSDOM node selection.

  python3 app/track_strip_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
PORT = 8885
HERE = Path(__file__).parent
tmp = os.path.realpath(tempfile.mkdtemp(prefix="track_strip_"))  # resolve /var->/private/var so paths match the registry
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
os.environ["STRIP_TOK"] = "STRIP-SECRET-VALUE"     # resolves -> armed; must never reach the DOM
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
A = os.path.join(tmp, "alpha")
os.makedirs(A, exist_ok=True)
S.workspace_workspace("add", {"folder_context": A})
S.workspace_policy("party_register", {
    "folder_context": A, "party_id": "scout", "kind": "agent", "grade": "L2",
    "competences": ["legal"], "channels": ["out-llm"], "actor": "alex"})
S.workspace_workflow("connector_register", {
    "folder_context": A, "connector_id": "out-llm", "role": "egress",
    "channel": "api", "floor": "hold", "credential_ref": "env:STRIP_TOK",
    "use_cases": ["uc-1"], "actor": "alex"})
S.workspace_workflow("use_case_register", {
    "folder_context": A, "use_case_id": "uc-1", "name": "Triage",
    "fingerprint": {"issue_type": "triage"}, "risk": "medium",
    "allowed_agents": ["scout"], "actor": "alex"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "track_strip_render.mjs"), str(PORT), A],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
