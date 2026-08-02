#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the Bring-in drawer (Set up > Bring-in, workspace_ingest).

Boots serve.py with a file to ingest and a loopback page to fetch, then runs
bringin_render.mjs: opens the drawer from the Set up entry, asserts the
acts-on-the-record badge and the boundary copy, runs the ingest-file
round-trip, drives the URL fetch through its confirm gate (declined fetches
nothing; accepted lands a fetched ledger row), and runs the skill ingest
round-trip.

  python3 app/bringin_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess, socket
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="bringin_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                          # noqa: E402
import workspaces.mcp_server as S          # noqa: E402,F401

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)
INGEST = os.path.join(F, "ingest_me.txt")
Path(INGEST).write_text("A note about the GDPR matter for the ingest round-trip.\n")


def _start_loopback():
    """A loopback page for the URL-ingest drive (robots allows everything)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            body = (b"User-agent: *\nDisallow:\n" if self.path == "/robots.txt"
                    else b"<html><head><title>Notice</title></head>"
                         b"<body><p>Processing notice for the register.</p></body></html>")
            self.send_response(200)
            self.send_header("Content-Type",
                             "text/plain" if self.path == "/robots.txt" else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://public.test:{httpd.server_address[1]}/page.html"


def main() -> int:
    # Admit only the test hostname and keep the production API free of a
    # private-network bypass.
    from workspaces import url_ingest
    httpd, url = _start_loopback()
    production_resolver = url_ingest._resolve_public
    test_port = httpd.server_address[1]

    def resolve_test_host(host, port):
        if host == "public.test" and port == test_port:
            return [(socket.AF_INET, ("127.0.0.1", test_port))]
        return production_resolver(host, port)

    url_ingest._resolve_public = resolve_test_host
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "bringin_render.mjs"), str(PORT), F, INGEST, url],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
        httpd.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
