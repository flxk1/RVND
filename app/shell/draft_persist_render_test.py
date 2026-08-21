#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for draft persistence on the authoring panels.

Seeds one registered workspace whose draft store holds a map draft saved
BEFORE the last applied change (so its chip must read "older than the patch")
and policy/cards/chat drafts saved after it, boots serve.py, and runs
draft_persist_render.mjs: boot-tail rehydrate, silent prefill on open,
debounced persist-on-edit with close + pagehide flush, the per-panel chip,
the chat restore divider, and the discard controls.

  python3 app/draft_persist_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = os.path.realpath(tempfile.mkdtemp(prefix="draftpersist_"))
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                                # noqa: E402
import rvnd.mcp_server as S           # noqa: E402
from rvnd import draft_store as D     # noqa: E402

LR = os.environ["WORKSPACE_L0_LOG_ROOT"]
F = os.path.join(tmp, "org")
F2 = os.path.join(tmp, "org2")   # the switch target — must never receive F's drafts
os.makedirs(F, exist_ok=True)
os.makedirs(F2, exist_ok=True)


def setup():
    S.workspace_workspace("add", {"folder_context": F})
    S.workspace_workspace("add", {"folder_context": F2})
    # the map draft predates the last applied change -> amber chip
    D.save(F, "map", {"text": "Article 9 risk management", "group_by": "role"},
           log_root=LR)
    time.sleep(1.1)   # the audit tail's timestamps are second-granular
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "bot7",
                                          "kind": "agent", "actor": "alex"})
    time.sleep(1.1)
    # these drafts postdate the chain tip -> plain "saved Ns ago" chips
    D.save(F, "policy_paste", {"text": "Automated decisions must be reviewed."},
           log_root=LR)
    D.save(F, "cards", {"kind": "use_case", "name": "Loan scoring",
                        "risk": "high"}, log_root=LR)
    D.save(F, "chat", {"entries": [
        {"who": "you", "text": "hello governance"},
        {"who": "rvnd", "text": "routed: ask"}],
        "policy": "no pii egress"}, log_root=LR)


def main() -> int:
    setup()
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "draft_persist_render.mjs"),
                            str(port), F, F2],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
