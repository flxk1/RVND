#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Coverage gate for the Rvnd app's introspection layer.

Asserts the app can reach EVERY tool + op the server declares — so "the app is
the control surface for all functions" is a checked property, not a claim.
Drives the same HTTP transport the app uses (serve.py). Exit 0 = all reachable.

  python app/coverage_gate.py
"""
from __future__ import annotations
import sys, json, threading, time, tempfile, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import serve  # noqa: E402

PORT = 0  # ephemeral — set in main() from the bound socket; no cross-test collisions
F = tempfile.mkdtemp()
TOKEN = ""  # set in main() from the server's per-session token


def _call(tool: str, args: dict):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/tool",
        data=json.dumps({"tool": tool, "args": args}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Workspaces-Token": TOKEN})
    try:
        # Reachability budget, not a latency SLO: the slowest legitimate op
        # (workspace_contract/demo builds the 5-template corpus, ~12s) must
        # fit, or a healthy op reads as unreachable.
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:  # noqa: BLE001
        return {"__exc": str(e)}


def _reachable(res) -> bool:
    # Reachable = the op dispatched and returned a result. A list (or any
    # non-dict) is a valid result. Unreachable only if the transport raised
    # or the server reports the tool itself is unknown.
    if isinstance(res, dict):
        if "__exc" in res:
            return False
        return "unknown tool" not in str(res.get("error", "")).lower()
    return True


def main() -> int:
    global TOKEN, PORT
    srv = serve.make_server(port=0)
    PORT = srv.server_address[1]
    TOKEN = srv.session_token
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    info = _call("server_info", {})
    tools = info.get("tools", [])
    standalone = {"server_info": {}, "workspace_ask": {"folder_context": F, "query": "x"},
                  "workspace_orchestrate": {"folder_context": F, "query": "x"},
                  "cross_workspace_read": {"folder_context": F, "sources": []}}
    bad, total, ok = [], 0, 0
    for t in tools:
        if t in standalone:
            total += 1
            if _reachable(_call(t, standalone[t])):
                ok += 1
            else:
                bad.append(t)
            continue
        h = _call(t, {"op": "help"})
        ops = h.get("ops") or []
        if not ops:
            bad.append(f"{t}:no-help"); continue
        for o in ops:
            name = o["op"] if isinstance(o, dict) else o
            total += 1
            if _reachable(_call(t, {"op": name, "params": {"folder_context": F}})):
                ok += 1
            else:
                bad.append(f"{t}/{name}")

    print(f"tools={len(tools)} ops+standalone={total} reachable={ok}")
    if bad:
        print("UNREACHABLE:", bad)
        print("FAIL"); return 1
    print(f"PASS — all {total} reachable; app covers {len(tools)}/{len(tools)} declared tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
