#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Say gate for the five-widget front door — the driver + the grower.

Seeds one workspace with one agent, boots serve.py, and drives the real
console.html in jsdom through Say (units/say.mjs) over the shared store. Asserts:

  (a) the grower renders the requisite-variety ledger — express (absorbed) AND
      residual (handed back) both visible on the Say output surface;
  (b) the drafted control patch validates through loomground_lang (patch_validate
      returns ok over the drafted netlist);
  (c) nothing is applied without a human confirm — applied stays false and no
      chain write lands until the confirm control is pressed;
  (d) the driver's confirm gate: a mutating command raises a confirm-card and
      fires nothing, while a read runs and renders with no execute control.

  python3 app/console_ingest_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="console_ingest_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, rvnd.mcp_server as S  # noqa: E402

F = os.path.join(tmp, "alpha")
os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})
S.workspace_policy("party_register", {"folder_context": F, "party_id": "a1",
                                      "kind": "agent", "grade": "L1", "actor": "alex"})


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "console_ingest.mjs"), str(PORT), F],
                           capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
