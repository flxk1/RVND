#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-05: failure-injection render gate for the Protections drawer.

A fail-closed server behind a fail-open display is fail-open to the operator.
This gate boots the real serve.py and, per mode, breaks the workspace_policy
seam the way the world actually breaks it — the call dies, the call never
returns, the session token stops being accepted, or the backend dies after a
healthy first load — then runs protections_failure_render.mjs to assert the
drawer's visible state is an explicit degradation, never a calm or fabricated
one. The injection wraps serve._facade_call for ONE tool only, so the page
boots normally and the failure is scoped to the panel under test — the same
seam ui_walk_reconcile_test.py traces, driven to the opposite end.

Deliberately NOT named *_render_test.py: the UI walk globs that pattern and
runs matches inside its 8-way-parallel, 90s-per-gate budget. Failure
injection contributes no op coverage there and four sequential server boots
under that contention failed on the 2-core CI runner with the mode
unidentifiable from the truncated tail. This runs as its own explicit lane
(make app-tests / the render-board CI step) with full output.

  python3 app/tests/protections_failure_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="psfail_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                                # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)

REAL = serve._facade_call
HANG_S = 4.0   # outlives the scenario's 2s observation window, not the runner


def _inject(mode: str):
    """Scope the failure to workspace_policy; every other tool stays live."""
    stale_failed = False

    def wrapped(tool: str, args: dict):
        nonlocal stale_failed
        if tool != "workspace_policy":
            return REAL(tool, args)
        if mode == "error":
            raise RuntimeError("injected: policy backend unavailable")
        if mode == "hang":
            time.sleep(HANG_S)
            raise RuntimeError("injected: gave up after hanging")
        if mode == "stale":
            op = (args or {}).get("op", "?")
            if not stale_failed and op in {"snapshot", "juris_packs", "party_list"}:
                return REAL(tool, args)
            stale_failed = True
            raise RuntimeError("injected: backend died after first load")
        return REAL(tool, args)          # revoked: the 403 happens before dispatch

    serve._facade_call = wrapped


def _run_mode(mode: str) -> bool:
    _inject(mode)
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()
    srv = serve.make_server(port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(
            ["node", str(HERE.parent / "panels" / "protections_failure_render.mjs"), str(port), F, mode],
            capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
        serve._facade_call = REAL
    print((r.stdout + r.stderr).strip())
    return r.returncode == 0 and f"PASS[{mode}]" in r.stdout


def main() -> int:
    ok = True
    for mode in ("error", "hang", "revoked", "stale"):
        ok = _run_mode(mode) and ok
    if not ok:
        print("FAIL: at least one failure mode leaves the operator with a calm or fabricated display")
        return 1
    print("PASS: protections drawer degrades explicitly under error, hang, revoked token, and post-load backend death")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
