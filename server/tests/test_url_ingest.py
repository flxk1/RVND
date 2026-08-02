# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the URL ingestion lane (``workspaces.url_ingest``).

The local server is admitted only by replacing the internal resolver in tests;
the production API has no private-network bypass.
"""

from __future__ import annotations

import socket
import inspect
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytestmark = pytest.mark.security  # red-team-relevant: runs in the `-m security` gate

import workspaces.url_ingest as url_ingest
from workspaces.memory import WorkspaceMemory
from workspaces.url_ingest import ingest_url, read_ledger


# ---------------------------------------------------------------------------
# Loopback server
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    # Class attribute set per-server; "allow all" by default.
    robots_body = "User-agent: *\nDisallow:\n"
    last_host = None

    def log_message(self, *args):  # silence test output
        return

    def _send(self, status, body: bytes, ctype: str, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.__class__.last_host = self.headers.get("Host")
        if self.path == "/robots.txt":
            self._send(200, self.robots_body.encode(), "text/plain")
        elif self.path.startswith("/page"):
            html = (b"<html><head><title>Digital Laws</title></head>"
                    b"<body><h1>EU AI Act</h1><p>Article 1 scope.</p>"
                    b"<script>var x=1;</script></body></html>")
            self._send(200, html, "text/html; charset=utf-8")
        elif self.path.startswith("/reserved"):
            html = b"<html><body><p>reserved content</p></body></html>"
            self._send(200, html, "text/html",
                       extra_headers={"tdm-reservation": "1"})
        elif self.path == "/redirect-page":
            self._send(302, b"", "text/plain",
                       extra_headers={"Location": "/page.html"})
        elif self.path == "/redirect-private":
            self._send(302, b"", "text/plain",
                       extra_headers={"Location": "http://127.0.0.1/private"})
        elif self.path == "/redirect-metadata":
            self._send(
                302, b"", "text/plain",
                extra_headers={
                    "Location": "http://169.254.169.254/latest/meta-data/",
                })
        elif self.path == "/redirect-file":
            self._send(302, b"", "text/plain",
                       extra_headers={"Location": "file:///etc/passwd"})
        else:
            self._send(404, b"not found", "text/plain")


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    # Reset to default allow-all for each test that uses the fixture.
    _Handler.robots_body = "User-agent: *\nDisallow:\n"
    _Handler.last_host = None
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    host, port = httpd.server_address
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        t.join(timeout=2)


@pytest.fixture
def admitted_server(monkeypatch, server):
    parts = url_ingest.urlsplit(server)
    destination = (
        socket.AF_INET,
        (parts.hostname, parts.port),
    )
    production_resolver = url_ingest._resolve_public

    def resolve(host, port):
        if host == "public.test" and port == parts.port:
            return [destination]
        return production_resolver(host, port)

    monkeypatch.setattr(url_ingest, "_resolve_public", resolve)
    return f"http://public.test:{parts.port}"


def _mk_folder(tmp_path):
    folder = tmp_path / "wks"
    folder.mkdir()
    log_root = tmp_path / "log"
    return folder, log_root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_allowed_url_is_fetched_and_ingested(tmp_path, admitted_server):
    folder, log_root = _mk_folder(tmp_path)
    row = ingest_url(str(folder), f"{admitted_server}/page.html",
                     log_root=str(log_root))

    assert row["state"] == "fetched", row
    assert row["robots_allowed"] is True
    assert row["pair_ids"], "expected at least one ingested pair"
    assert row["content_hash"].startswith("sha256:")

    # Saved file exists under sources/<host>/ with provenance front-matter.
    saved = row["saved_path"]
    assert saved and "/sources/" in saved
    text = open(saved, encoding="utf-8").read()
    assert "source_url:" in text
    assert "lawful_access: user_selected" in text
    assert "Digital Laws" in text          # title captured
    assert "var x=1" not in text           # <script> stripped

    # Pair actually landed in the workspace's memory.
    pairs = WorkspaceMemory(folder, log_root=log_root, actor="t").all_pairs()
    assert len(pairs) >= 1

    # Ledger has exactly one row for the URL.
    led = read_ledger(str(folder))
    assert len(led) == 1
    assert led[0]["url"] == f"{admitted_server}/page.html"


def test_robots_blocked_is_saved_not_fetched(tmp_path, admitted_server):
    folder, log_root = _mk_folder(tmp_path)
    _Handler.robots_body = "User-agent: *\nDisallow: /\n"

    row = ingest_url(str(folder), f"{admitted_server}/page.html",
                     log_root=str(log_root))

    assert row["state"] == "robots_blocked", row
    assert row["robots_allowed"] is False
    assert row["pair_ids"] == []
    assert row["saved_path"] is None

    # URL is still saved (honest row), but nothing was ingested.
    led = read_ledger(str(folder))
    assert len(led) == 1 and led[0]["state"] == "robots_blocked"
    assert WorkspaceMemory(folder, log_root=log_root, actor="t").all_pairs() == []


def test_robots_override_fetches(tmp_path, admitted_server):
    folder, log_root = _mk_folder(tmp_path)
    _Handler.robots_body = "User-agent: *\nDisallow: /\n"

    row = ingest_url(str(folder), f"{admitted_server}/page.html",
                     log_root=str(log_root),
                     allow_robots_override=True)

    assert row["state"] == "fetched", row
    assert row["robots_allowed"] is False    # recorded honestly even on override
    assert row["pair_ids"]


def test_reingest_same_url_is_unchanged(tmp_path, admitted_server):
    folder, log_root = _mk_folder(tmp_path)
    url = f"{admitted_server}/page.html"

    first = ingest_url(str(folder), url, log_root=str(log_root))
    assert first["state"] == "fetched"

    second = ingest_url(str(folder), url, log_root=str(log_root))
    assert second["state"] == "unchanged", second
    assert second["pair_ids"] == first["pair_ids"]


def test_tdm_reservation_recorded_then_blockable(tmp_path, admitted_server):
    folder, log_root = _mk_folder(tmp_path)
    url = f"{admitted_server}/reserved.html"

    # Default: record-only — fetch proceeds, reservation noted.
    rec = ingest_url(str(folder), url, log_root=str(log_root))
    assert rec["state"] == "fetched", rec
    assert "tdm-reservation=1" in rec["tdm_reservation"]

    # Opt-in blocking: refuse ingest on a reserved source.
    folder2, log_root2 = tmp_path / "wks2", tmp_path / "log2"
    folder2.mkdir()
    blocked = ingest_url(str(folder2), url, log_root=str(log_root2),
                         block_on_tdm_reservation=True)
    assert blocked["state"] == "tdm_reserved", blocked
    assert blocked["pair_ids"] == []


def test_non_public_host_refused_by_default(tmp_path, server):
    folder, log_root = _mk_folder(tmp_path)
    # Without the test switch, the loopback host must be refused (SSRF guard).
    row = ingest_url(str(folder), f"{server}/page.html",
                     log_root=str(log_root))
    assert row["state"] == "fetch_error"
    assert "public address" in row["error"]


def test_legitimate_relative_redirect_is_fetched(tmp_path, admitted_server):
    folder, log_root = _mk_folder(tmp_path)
    row = ingest_url(
        str(folder), f"{admitted_server}/redirect-page",
        log_root=str(log_root))

    assert row["state"] == "fetched", row
    assert row["http_status"] == 200
    assert _Handler.last_host == url_ingest.urlsplit(admitted_server).netloc


@pytest.mark.parametrize(
    ("path", "error_fragment"),
    [
        ("/redirect-private", "public addresses"),
        ("/redirect-metadata", "public addresses"),
        ("/redirect-file", "unsupported scheme"),
    ],
)
def test_redirect_hop_must_remain_safe(
        tmp_path, admitted_server, path, error_fragment):
    folder, log_root = _mk_folder(tmp_path)
    row = ingest_url(
        str(folder), f"{admitted_server}{path}",
        log_root=str(log_root))

    assert row["state"] == "fetch_error"
    assert error_fragment in row["error"]


def test_dns_answer_is_bound_to_connection_destination(monkeypatch):
    resolutions = 0
    public_destination = ("93.184.216.34", 80)

    def rebinding_getaddrinfo(host, port, **kwargs):
        nonlocal resolutions
        resolutions += 1
        address = public_destination if resolutions == 1 else ("127.0.0.1", port)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address),
        ]

    connected_destinations = []

    class Response:
        status = 200

        @staticmethod
        def getheader(name):
            return None

    class Connection:
        def __init__(self, host, port, destination, timeout):
            connected_destinations.append(destination)

        def request(self, method, target, headers):
            return None

        def getresponse(self):
            return Response()

        def close(self):
            return None

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
    monkeypatch.setattr(url_ingest, "_PinnedHTTPConnection", Connection)

    conn, _, _ = url_ingest._open_url("http://example.test/page", {}, 1)
    conn.close()

    assert resolutions == 1
    assert connected_destinations == [
        (socket.AF_INET, public_destination),
    ]


def test_https_pin_preserves_tls_hostname(monkeypatch):
    destination = (socket.AF_INET, ("93.184.216.34", 443))
    calls = {}

    class RawSocket:
        def settimeout(self, timeout):
            calls["timeout"] = timeout

        def connect(self, sockaddr):
            calls["destination"] = sockaddr

        def close(self):
            calls["closed"] = True

    class Context:
        def wrap_socket(self, raw_socket, server_hostname):
            calls["server_hostname"] = server_hostname
            return "wrapped-socket"

    monkeypatch.setattr(socket, "socket", lambda *args: RawSocket())
    monkeypatch.setattr(url_ingest.ssl, "create_default_context", Context)

    conn = url_ingest._PinnedHTTPSConnection(
        "example.test", 443, destination, 2)
    conn.connect()

    assert calls["destination"] == destination[1]
    assert calls["server_hostname"] == "example.test"
    assert conn.sock == "wrapped-socket"


def test_private_host_bypass_is_not_part_of_ingest_api():
    assert "allow_private_hosts" not in inspect.signature(ingest_url).parameters
