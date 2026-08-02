#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the Knob bindings drawer (MIDI-learn + All-Stop). Seeds 2 active agents,
baseline oversight and a 0.5 grounding floor, then the .mjs stubs Web MIDI and
learns CCs both ways: tighten is instant; ov_loosen and floor_down are
confirm-gated (declined = no server change, accepted = exactly one discrete
step, lamp shows granted). Reserved controls carry no dial, and All-Stop
suspends (never kills) every active party.

  python3 app/controller_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="controller_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
F = os.path.join(tmp, "org"); os.makedirs(F, exist_ok=True)


def main() -> int:
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "bot7", "kind": "agent", "actor": "alex"})
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "bot8", "kind": "agent", "actor": "alex"})
    S.workspace_policy("set_oversight_level", {"folder_context": F, "level": "approve", "actor": "alex"})
    S.workspace_lock("threshold_set", {"folder_context": F, "threshold": 0.5})
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "controller_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=45)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
