#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the Kind x risk lens (the coverage panel's server-backed preset).

Seeds ONE registered workspace with one agent and two use cases of different kind
and risk — a high-risk "billing" and a low-risk "outreach" — boots serve.py, and
checks the Coverage panel's second preset reads the coverage_matrix projection:
the kinds render as rows, the risk bands as columns, a populated cell names its
band and use-case count, and an empty band reads "none".

  python3 app/coverage_matrix_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
PORT = 8885
HERE = Path(__file__).parent
tmp = os.path.realpath(tempfile.mkdtemp(prefix="covmatrix_"))
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, rvnd.mcp_server as S  # noqa: E402
from rvnd.parties import register_party  # noqa: E402
from rvnd.use_case import register_use_case  # noqa: E402
LR = os.environ["WORKSPACE_L0_LOG_ROOT"]
A = os.path.join(tmp, "alpha")
os.makedirs(A, exist_ok=True)
S.workspace_workspace("add", {"folder_context": A})
register_party(A, "bot-a", "agent", name="bot-a", actor="alex", log_root=LR)
# a DPO holds data-protection; nobody holds finance (the task_role gap)
register_party(A, "dpo", "human", name="dpo",
               competences=["data-protection"], actor="alex", log_root=LR)
# uc-bill reserves to data-protection (covered); uc-out reserves to finance (gap).
# Both carry a kind (issue_type) + risk so the kind x risk preset also reads them.
register_use_case(A, use_case_id="uc-bill", name="uc-bill",
                  fingerprint={"issue_type": "billing"}, risk="high",
                  allowed_agents=["bot-a"], actor="alex",
                  policy_reservations={"uc-bill": {
                      "reserved_to": "data-protection", "act_type": "review",
                      "source": "policy"}}, log_root=LR)
register_use_case(A, use_case_id="uc-out", name="uc-out",
                  fingerprint={"issue_type": "outreach"}, risk="low",
                  allowed_agents=["bot-a"], actor="alex",
                  policy_reservations={"uc-out": {
                      "reserved_to": "finance", "act_type": "approve",
                      "source": "policy"}}, log_root=LR)


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "coverage_matrix_render.mjs"), str(PORT), A],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
