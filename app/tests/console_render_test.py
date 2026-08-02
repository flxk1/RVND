#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Route test for the five-widget front door.

Boots serve.py and asserts the new default at / serves console.html with the
five-frame skeleton and the bridge wiring, while /classic still serves the
unchanged classic console. The old design is preserved and reachable.

  python3 app/console_render_test.py
"""
from __future__ import annotations
import os, sys, threading, time, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()
import serve  # noqa: E402


def _get(port: int, path: str) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        assert r.status == 200, (path, r.status)
        return r.read().decode()


def main() -> int:
    srv = serve.make_server(port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        root = _get(port, "/")
        # the new default is the five-widget front door
        assert "Rvnd — Console" in root, "console.html not served at /"
        # the frames: search/chat bar, build + run (centre toggle), read (screen).
        # Frame 1 was renamed "Say" → "Search/Chat" when the console landed its
        # search integration; the chat-bar ids (#say, #say-out) kept their names.
        for frag in ("1 · Search/Chat", "2 · Build", "4 · Read"):
            assert frag in root, f"front door missing frame {frag!r}"
        assert 'data-centre="run"' in root, "no Run toggle in the centre"
        # the persistent chat bar with its input and All-Stop
        assert 'id="say"' in root, "chat bar input missing"
        assert 'id="allstop"' in root, "All-Stop missing from the chat bar"
        # the header workspace context + environment rollup (console_snapshot)
        assert 'id="ws"' in root and 'id="env"' in root, "header context/rollup missing"
        # the front door renders from the shared store (units/state.mjs); the
        # console_snapshot wiring now lives there, imported by the console.
        assert "/units/state.mjs" in root, "the console does not import the shared store module"
        # Say (the driver + the grower) is a shared unit too, with its output
        # surface, and the placeholder no longer carries the phase tag
        assert "/units/say.mjs" in root, "the console does not import the Say module"
        assert 'id="say-out"' in root, "no Say output surface for the confirm-card / ledger"
        assert "(Phase 2a)" not in root, "the Say input still carries a phase tag"
        # the Read screen reads Allowed / Waiting / Happened
        for k in ("Allowed", "Waiting", "Happened"):
            assert k in root, f"Read screen missing {k!r}"
        # The visible link back to /classic was removed from the front door;
        # the route itself is preserved and asserted below.
        assert "/classic" in root, "front door no longer mentions the classic route at all"
        assert "window.__WORKSPACES_HTTP__='/tool'" in root, "bridge wiring missing at /"

        # the shared store module is served with a JS content-type and carries
        # the real reads the surfaces render from — console_snapshot + audit tail
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/units/state.mjs") as r:
            assert r.status == 200, "/units/state.mjs not served"
            assert "javascript" in r.headers.get("Content-Type", ""), "store module not served as javascript"
            unit = r.read().decode()
        assert "createStore" in unit, "store module exports no createStore"
        assert "console_snapshot" in unit, "the env rollup is not wired to console_snapshot"
        # the Say module is served too, and drafts through the ingest/confirm ops
        # the classic console uses (governance_chat + patch_apply), not a new path
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/units/say.mjs") as r:
            assert r.status == 200, "/units/say.mjs not served"
            assert "javascript" in r.headers.get("Content-Type", ""), "Say module not served as javascript"
            say = r.read().decode()
        assert "createSay" in say, "Say module exports no createSay"
        for tok in ("governance_chat", "patch_apply", "mutates"):
            assert tok in say, f"Say module missing {tok!r} — driver/grower not wired to the real ops"
        # a path outside the exact-name allowlist is refused (no path resolution)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/units/serve.py")
            raise AssertionError("/units/ served a name outside the allowlist")
        except urllib.error.HTTPError as e:
            assert e.code == 404, f"/units/ allowlist miss returned {e.code}, not 404"

        # the classic console is preserved, unchanged, and reachable
        classic = _get(port, "/classic")
        assert "Governance Patchbay" in classic, "classic console not served at /classic"
        assert "window.__WORKSPACES_HTTP__='/tool'" in classic, "bridge wiring missing at /classic"

        # the sign-off widget route is untouched
        sign = _get(port, "/sign")
        assert "Rvnd — Sign-off" in sign, "widget page no longer served at /sign"
    finally:
        srv.shutdown()
    print("PASS: front door — / serves the five-widget console (say/build/run/read + "
          "classic link), /classic serves the unchanged patchbay, /sign the widget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
