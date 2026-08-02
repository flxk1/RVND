#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the first-run onboarding wizard.

Boots serve.py against an empty workspace registry, then runs
wizard_render.mjs: the wizard appears only on true first-run, walks
create-workspace -> agent+task -> autonomy posture -> four-section tour,
persists the seen flag, and never reappears once a workspace exists.

  python3 app/wizard_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="wizard_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402

NEW = os.path.join(tmp, "first-workspace")


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "wizard_render.mjs"), str(PORT), NEW],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    ok = r.returncode == 0 and "PASS" in r.stdout
    if ok and not os.path.isdir(NEW):
        print("FAIL: the wizard's workspace directory was not created on disk"); return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
