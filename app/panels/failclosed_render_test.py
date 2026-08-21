#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-05: fail-closed failure-injection gate for the governance console.

The 68 render gates prove the console renders the RIGHT verdict from a HEALTHY
bridge — but "server decides, client renders" is only safe if a fail-closed
server is not fronted by a fail-open display. This gate drives the real console
against a bridge forced into each failure mode and asserts the connection
surface reads DEGRADED — never a calm "live" or an empty-looking "no workspace"
that a down or refusing server is indistinguishable from:

  * healthy  — control: a real bridge must NOT read degraded (the fault state is
               earned, not always-on);
  * error    — the bridge answers every call with a 500-shaped error response;
  * hang     — the bridge never answers; the client's own deadline must abort
               and surface the fault, not spin forever;
  * revoked  — a live session whose token is rejected mid-session must flip to
               degraded on the next read, leaving no stale "live" on screen.

The server-side faults are installed on serve._facade_call — the same seam
ui_walk_reconcile wraps to trace ops; the revoked mode drives a bad token from
the page. Each mode runs against its own fresh ephemeral server.

  python3 app/panels/failclosed_render_test.py        # exit 0 = PASS
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="failclosed_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve  # noqa: E402
import rvnd.mcp_server as MS  # noqa: E402

F = os.path.join(tmp, "fanclub-crm"); os.makedirs(F, exist_ok=True)
MS.workspace_workspace("add", {"folder_context": F})   # a real workspace the console should read

_HEALTHY = serve._facade_call   # the genuine bridge dispatch


def _install_fault(mode: str) -> None:
    if mode == "error":
        # A 500-shaped refusal on every call: the bridge answers, but with a fault.
        serve._facade_call = lambda tool, args: {"ok": False, "error": "injected server fault"}
    elif mode == "hang":
        def _hang(tool, args):
            time.sleep(3.0)                 # never returns before the page's 600ms deadline
            return {"ok": True}
        serve._facade_call = _hang
    else:
        serve._facade_call = _HEALTHY       # healthy + revoked use the genuine bridge


def _run_mode(mode: str) -> bool:
    _install_fault(mode)
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()   # server + page share this session token
    srv = serve.make_server(port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(
            ["node", str(HERE / "failclosed_render.mjs"), str(port), F, mode],
            capture_output=True, text=True, timeout=120)
    finally:
        srv.shutdown()
        serve._facade_call = _HEALTHY
    print((r.stdout + r.stderr).strip())
    return r.returncode == 0 and "PASS" in r.stdout


def main() -> int:
    ok = True
    for mode in ("healthy", "error", "hang", "revoked"):
        if not _run_mode(mode):
            ok = False
    print("PASS: fail-closed display — healthy reads live, every bridge fault "
          "(error/hang/revoked) reads DEGRADED, no silent green"
          if ok else "FAIL: a failure mode did not surface as a degraded state")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
