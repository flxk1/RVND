#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render gate for GUI-2 — data-lineage tags in the Inspector.

Boots serve.py, seeds a use_case authored with tag 'pii' + a connector stamping
tag 'eu-region' linked to it + a tags-guarded reservation (reserve ... when tags
contains pii), then drives tags_render.mjs which selects the use_case node and
asserts the Inspector's Data-tags field shows authored ∪ connector tags
(attributed) and the tags-guarded reservation reads as 'only when tagged'.

  python3 app/tags_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="tags_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402
import rvnd.mcp_server as S          # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)


def setup():
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "bot7", "kind": "agent", "grade": "L2"})
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "dpo", "kind": "human", "role": "data-protection"})
    # authored tag 'pii' on the act
    S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "uc-score", "name": "Loan score",
                                          "fingerprint": {"issue_type": "automated_decision"}, "risk": "medium",
                                          "allowed_agents": ["bot7"], "actor": "alex", "tags": ["pii"]})
    # a channel stamping tag 'eu-region', linked to the use case
    S.workspace_workflow("connector_register", {"folder_context": F, "connector_id": "c-eu", "role": "ingress",
                                           "channel": "intake", "name": "EU intake", "use_cases": ["uc-score"],
                                           "tags": ["eu-region"], "group": "acme"})
    # a tags-guarded reservation: only reserves when the act is tagged pii
    S.workspace_workflow("patch_apply", {"folder_context": F, "actor": "alex", "netlist":
        "actor bot7\ngate uc-score risk medium grant bot7\ncord bot7 -> uc-score\n"
        "cord uc-score -> master\nreserve uc-score by dpo when tags contains pii\n"})


def main() -> int:
    setup()
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "tags_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=40)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
