#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the Live Audit Ticker (workspace_audit op=tail, read-only).

Boots serve.py, seeds a folder with several signed events (register + party +
operate), strips the signature from one log line so the unsigned-flag path is
exercised, then runs ticker_render.mjs which toggles the ticker and asserts
the strip streams signed-event chips with discrete verdict lamps, flags the
tampered event as unsigned, is a non-modal live region, is read-only, and
toggles off cleanly.

  python3 app/ticker_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="ticker_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402
import workspaces.mcp_server as S          # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)


def main() -> int:
    S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "u",
                                          "name": "Loan scoring", "fingerprint": {"issue_type": "automated_decision"},
                                          "risk": "high", "allowed_agents": ["a1"], "actor": "alex"})
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "a1",
                                     "kind": "agent", "name": "Drafting bot", "actor": "alex"})
    try:
        S.workspace_workflow("operate", {"folder_context": F, "use_case_id": "u", "agent_id": "a1",
                                    "issues": [{"issue_id": "i1", "issue_type": "automated_decision", "completeness": "high"}],
                                    "now_epoch": 1700000000})
    except Exception:
        pass
    # Strip the signature from the last event so the feed carries one unsigned
    # event: audit_tail reports signed=False for it and the ticker must flag
    # it, not render it like a signed chip.
    import json
    from workspaces.mutation_log import MutationLog
    lf = MutationLog(F, log_root=os.environ["WORKSPACE_L0_LOG_ROOT"]).log_file
    lines = lf.read_text().splitlines()
    tampered = json.loads(lines[-1])
    tampered["signature"] = ""
    lines[-1] = json.dumps(tampered)
    lf.write_text("\n".join(lines) + "\n")
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    # The .mjs carries an 18s watchdog that fails fast naming the step. Retry only
    # on a hang/watchdog timeout — a real assertion failure returns immediately and
    # is not masked.
    try:
        out = ""
        for attempt in range(3):
            try:
                r = subprocess.run(["node", str(HERE / "ticker_render.mjs"), str(PORT), F],
                                   capture_output=True, text=True, timeout=25)
                out = (r.stdout + r.stderr).strip()
                if "PASS" in r.stdout:
                    print(out)
                    return 0
                if "watchdog" not in out:          # a real assertion failure → don't retry
                    print(out)
                    return 1
            except subprocess.TimeoutExpired:
                out = f"node timed out (attempt {attempt + 1}) — jsdom boot stall"
            print(f"[retry] {out}")
        print(out)
        return 1
    finally:
        srv.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
