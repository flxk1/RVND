#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the Privacy Lock drawer (workspace_lock, read + write).

Boots serve.py with one registered use case plus stored pairs, and a second
folder sealed at rest (known passphrase). lock_render.mjs opens the drawer,
asserts the read cards render with no percentage/dial, drives the floor
(raise=direct, lower=confirm+reason), reclassify (confirm-gated, counts the
stored pairs), and on the sealed folder: unseal with a wrong passphrase fails
closed, the real passphrase unseals, and seal drops the cached session key.

  python3 app/lock_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="lock_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402
import rvnd.mcp_server as S          # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)
F2 = os.path.join(tmp, "vault")        # sealed at rest; the gate unseals it
os.makedirs(F2, exist_ok=True)
PASSPHRASE = "gate-passphrase"


def _seed_pairs(folder: str, n: int) -> None:
    from rvnd.memory import WorkspaceMemory
    mem = WorkspaceMemory(folder, log_root=os.environ["WORKSPACE_L0_LOG_ROOT"], actor="alex")
    for i in range(n):
        mem.remember({
            "id": f"sha256:x{i}",
            "problem": {"id": f"p{i}", "scope": "s", "type": "rule", "summary": f"note {i}"},
            "solution": {"id": f"sha256:x{i}", "problem_id": f"p{i}", "body": "plain body",
                         "authority_tier": 1, "confidence": 1.0, "body_format": "prose"},
        })


def main() -> int:
    S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "u",
                                          "name": "u", "fingerprint": {"issue_type": "automated_decision"},
                                          "risk": "high", "allowed_agents": [], "actor": "alex"})
    _seed_pairs(F, 3)                      # reclassify sweeps these
    _seed_pairs(F2, 2)
    from rvnd import seal
    seal.seal_folder(F2, passphrase=PASSPHRASE,
                     log_root=os.environ["WORKSPACE_L0_LOG_ROOT"])
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "lock_render.mjs"), str(PORT), F, F2, PASSPHRASE],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
