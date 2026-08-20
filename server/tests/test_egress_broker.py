# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Request→track binding — the egress proxy as a per-track credential broker
through the live track broker.

Two layers under test:
  * track_broker.bind_track — resolve a declared track against the folder's
    chain, fail-closed on every missing or barred rung (no declaration, unknown
    track, non-egress role, deny floor, no cable, unplugged reference);
  * the proxy in broker mode, end-to-end against a stub upstream — the header
    convention (X-Lock-Track), credential stripping + injection (the agent
    never holds the key), the hold-floor person gate, and a secret-free audit
    trail and refusal surface.
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from workspaces import connectors
from workspaces.lock import OversightLevel
from workspaces.lock.egress_proxy import ApprovalDecision, EgressProxy, autonomous_callback
from workspaces.lock.track_broker import TRACK_HEADER, bind_track

# Real ThreadingHTTPServer instances (proxy + stub) started and torn down per
# test — ~25s for this file alone versus low single digits for the rest of
# the suite (measured), because it pays real socket/thread startup and
# shutdown cost rather than mocking the transport. Excluded from the fast
# subset; still runs in the full suite.
pytestmark = pytest.mark.slow


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def folder(tmp_path):
    """A workspace folder with one armed egress track and the refusal variants."""
    f = str(tmp_path / "ws")
    lr = str(tmp_path / "log")
    reg = lambda **kw: connectors.register_connector(f, log_root=lr, channel="api", **kw)
    reg(connector_id="out-llm", role="egress", credential_ref="env:BROKER_TOK")
    reg(connector_id="out-bare", role="egress")                              # no cable
    reg(connector_id="out-dead", role="egress", credential_ref="env:BROKER_TOK",
        floor="deny")
    reg(connector_id="out-hold", role="egress", credential_ref="env:BROKER_TOK",
        floor="hold")
    reg(connector_id="out-cold", role="egress", credential_ref="env:NOT_SET_ANYWHERE")
    reg(connector_id="feed", role="ingress")
    return f, lr


# ---- bind_track: the fail-closed ladder ---------------------------------------

def test_bind_track_armed(folder, monkeypatch):
    f, lr = folder
    monkeypatch.setenv("BROKER_TOK", "s3cr3t-tok")
    b = bind_track(f, "out-llm", log_root=lr)
    assert b.ok and b.secret == "s3cr3t-tok"
    assert b.credential_ref == "env:BROKER_TOK" and not b.hold
    # the secret never rides in the repr (tracebacks/logs must stay clean)
    assert "s3cr3t-tok" not in repr(b)


@pytest.mark.parametrize("cid,fragment", [
    (None, "no track declared"),
    ("", "no track declared"),
    ("ghost", "unknown track"),
    ("feed", "not an egress track"),
    ("out-dead", "floor is deny"),
    ("out-bare", "no cable"),
    ("out-cold", "unplugged"),
])
def test_bind_track_refuses_each_rung(folder, monkeypatch, cid, fragment):
    f, lr = folder
    monkeypatch.setenv("BROKER_TOK", "s3cr3t-tok")
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    b = bind_track(f, cid, log_root=lr)
    assert not b.ok and b.secret is None
    assert fragment in b.reason
    assert "s3cr3t-tok" not in b.reason


def test_bind_track_sanitizes_echoed_id(folder):
    """A header-supplied id is echoed printable-ASCII-only and bounded — the
    refusal reason rides in an HTTP status line (latin-1, one line)."""
    f, lr = folder
    b = bind_track(f, "gh—ost\r\n" + "x" * 100, log_root=lr)
    assert not b.ok
    assert "\r" not in b.reason and "\n" not in b.reason
    assert b.reason.isascii()
    assert len(b.reason) < 120


def test_bind_track_hold_floor_binds_with_flag(folder, monkeypatch):
    f, lr = folder
    monkeypatch.setenv("BROKER_TOK", "s3cr3t-tok")
    b = bind_track(f, "out-hold", log_root=lr)
    assert b.ok and b.hold and b.floor == "hold"


# ---- proxy in broker mode: end-to-end against a stub upstream ------------------

class _Stub:
    """A stub upstream capturing the headers/body each forwarded request carried."""

    def __init__(self):
        self.received: list[dict] = []
        port = _free_port()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                stub.received.append({
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": self.rfile.read(length).decode(),
                })
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

        self.url = f"http://127.0.0.1:{port}"
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def broker(folder, tmp_path, monkeypatch):
    """A broker-bound proxy in front of a stub standing in for api.anthropic.com."""
    monkeypatch.setenv("BROKER_TOK", "s3cr3t-tok")
    f, lr = folder
    stub = _Stub()
    audit = tmp_path / "audit.jsonl"
    proxy = EgressProxy(
        port=_free_port(),
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
        upstream_overrides={"api.anthropic.com": stub.url},
        audit_log_path=str(audit),
        track_folder=f,
        track_log_root=lr,
    )
    proxy.start()
    yield proxy, stub, audit
    proxy.stop()
    stub.stop()


def _post(proxy, *, track=None, extra_headers=None,
          content="hello") -> tuple[int, bytes]:
    body = json.dumps({"messages": [{"role": "user", "content": content}]}).encode()
    headers = {"X-Lock-Upstream": "api.anthropic.com", "Content-Type": "application/json"}
    if track is not None:
        headers[TRACK_HEADER] = track
    headers.update(extra_headers or {})
    req = urllib.request.Request(f"http://127.0.0.1:{proxy.port}/v1/messages",
                                 data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_broker_injects_track_credential_and_strips_client_key(broker):
    proxy, stub, _ = broker
    status, _ = _post(proxy, track="out-llm",
                      extra_headers={"x-api-key": "agent-held-dummy"})
    assert status == 200
    fwd = stub.received[0]["headers"]
    # the track's credential, not the agent's; the declaration does not leak upstream
    assert fwd["x-api-key"] == "s3cr3t-tok"
    assert "x-lock-track" not in fwd
    assert "agent-held-dummy" not in json.dumps(stub.received)


@pytest.mark.parametrize("track,fragment", [
    (None, "no track declared"),
    ("ghost", "unknown track"),
    ("feed", "not an egress track"),
    ("out-dead", "floor is deny"),
    ("out-bare", "no cable"),
    ("out-cold", "unplugged"),
])
def test_broker_refuses_before_forwarding(broker, track, fragment):
    proxy, stub, _ = broker
    status, body = _post(proxy, track=track)
    assert status == 403
    assert fragment in body.decode()
    assert stub.received == []
    assert proxy.stats["blocked"] == 1


def test_broker_refuses_uninjectable_upstream(folder, monkeypatch):
    monkeypatch.setenv("BROKER_TOK", "s3cr3t-tok")
    f, lr = folder
    proxy = EgressProxy(port=_free_port(), oversight=OversightLevel.AUTONOMOUS,
                        approval_callback=autonomous_callback,
                        upstream_overrides={"stub.test": "http://127.0.0.1:9"},
                        track_folder=f, track_log_root=lr)
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{proxy.port}/v1/messages", data=body,
            headers={"X-Lock-Upstream": "stub.test", TRACK_HEADER: "out-llm"},
            method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 403
        assert "no credential injection binding" in e.value.read().decode()
    finally:
        proxy.stop()


def test_hold_floor_consults_a_person_on_clean_content(folder, tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_TOK", "s3cr3t-tok")
    f, lr = folder
    stub = _Stub()
    asked: list = []

    def person(pending):
        asked.append(pending)
        return ApprovalDecision(action="allow", reason="operator approved the hold")

    proxy = EgressProxy(port=_free_port(), oversight=OversightLevel.AUTONOMOUS,
                        approval_callback=person,
                        upstream_overrides={"api.anthropic.com": stub.url},
                        track_folder=f, track_log_root=lr)
    proxy.start()
    try:
        status, _ = _post(proxy, track="out-hold")
        assert status == 200
        # clean content would auto-forward on a permit track; the hold floor asked
        assert len(asked) == 1
        assert proxy.stats["user_approved"] == 1

        asked.clear()
        status, _ = _post(proxy, track="out-llm")
        assert status == 200 and asked == []
    finally:
        proxy.stop()
        stub.stop()


def test_hold_floor_block_refuses(folder, monkeypatch):
    monkeypatch.setenv("BROKER_TOK", "s3cr3t-tok")
    f, lr = folder
    stub = _Stub()
    proxy = EgressProxy(
        port=_free_port(), oversight=OversightLevel.AUTONOMOUS,
        approval_callback=lambda p: ApprovalDecision(action="block", reason="operator said no"),
        upstream_overrides={"api.anthropic.com": stub.url},
        track_folder=f, track_log_root=lr)
    proxy.start()
    try:
        status, body = _post(proxy, track="out-hold")
        assert status == 403
        assert "operator said no" in body.decode()
        assert stub.received == []
    finally:
        proxy.stop()
        stub.stop()


def test_broker_audit_carries_track_never_secret(broker):
    proxy, _, audit = broker
    _post(proxy, track="out-llm")
    _post(proxy, track="out-bare")
    raw = audit.read_text()
    entries = [json.loads(l) for l in raw.strip().splitlines()]
    decision = next(e for e in entries if e.get("kind") == "proxy_decision")
    assert decision["track"] == "out-llm"
    assert decision["credential_ref"] == "env:BROKER_TOK"
    assert decision["mode"] == "brokered"
    block = next(e for e in entries if e.get("kind") == "proxy_block")
    assert block["track"] == "out-bare"
    assert "s3cr3t-tok" not in raw


def test_health_reports_broker_bound(broker):
    proxy, _, _ = broker
    with urllib.request.urlopen(
            f"http://127.0.0.1:{proxy.port}/__lock_health__", timeout=5) as resp:
        health = json.loads(resp.read())
    assert health["broker_bound"] is True


def test_unbound_proxy_ignores_track_header(tmp_path):
    """Without a bound folder the proxy keeps its legacy per-request behaviour."""
    stub = _Stub()
    proxy = EgressProxy(port=_free_port(), oversight=OversightLevel.AUTONOMOUS,
                        approval_callback=autonomous_callback,
                        upstream_overrides={"api.anthropic.com": stub.url})
    proxy.start()
    try:
        status, _ = _post(proxy, track="ghost",
                          extra_headers={"x-api-key": "client-key"})
        assert status == 200
        # legacy mode forwards the client's own credential untouched
        assert stub.received[0]["headers"]["x-api-key"] == "client-key"
    finally:
        proxy.stop()
        stub.stop()
