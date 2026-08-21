#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the Contracts drawer (workspace_contract), both modes: seed a review
+ a contract, then read mode renders them with zero act controls and act mode renders
the decision queue whose ingest round-trip grows the stack.

  python3 app/contract_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="ct_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, rvnd.mcp_server as S  # noqa: E402
F = os.path.join(tmp, "org"); os.makedirs(F, exist_ok=True)


def main() -> int:
    assert S.workspace_contract("review", {"folder_context": F, "contract_id": "ENT-NDA-001",
        "decision": "Approve with Conditions", "contract_type": "nda"}).get("ok")
    assert S.workspace_contract("ingest", {"folder_context": F, "contract_id": "ent-rec-002",
        "text": "The Label shall pay the Artist within 30 days of receipt."}).get("ok")
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "contract_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=45)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
