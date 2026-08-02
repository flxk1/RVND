#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the Privacy-lock backend setup CTA.

Boots serve.py with XDG_CONFIG_HOME pointed at a fresh temp dir so no lock
config exists, then runs lock_setup_render.mjs: the drawer shows the
not-set-up card, the Set up flow runs the onboarding wizard headlessly and
renders its outcome, and the drawer flips to the configured card. The CLI
wizard path is untouched by this surface (covered by its own op tests).

  python3 app/lock_setup_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="locksetup_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "xdg")   # no lock config yet
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "lock_setup_render.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    ok = r.returncode == 0 and "PASS" in r.stdout
    cfg = Path(os.environ["XDG_CONFIG_HOME"]) / "agent-tool-lock" / "config.json"
    if ok and not cfg.exists():
        print("FAIL: setup reported success but wrote no config file"); return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
