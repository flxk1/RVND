# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-09: real-transport timeout + half-response behaviour at the outbound
network boundaries.

The existing local-model robustness suite injects failures by MOCKING the
call boundary — a "timeout" there is an instant ``{ok: False}`` return, so it
proves the *pipeline logic* tolerates a timeout-shaped result but never that
the *transport actually sets a timeout*. These tests stand up REAL
``http.server`` upstreams that hang or truncate, point the real
``urllib``-based clients at them, and assert the boundary is bounded and
fails closed within the deadline — not that it blocks a worker forever.

Boundaries covered:
  * egress proxy forward (``egress_proxy.py`` — the fail-closed cloud path);
  * local-model OpenAI-compatible route (``local_llm.complete_via``).
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import urllib.error
import urllib.request

pytestmark = pytest.mark.security  # fail-closed boundary behaviour


@pytest.fixture(autouse=True)
def _identity_keypair():
    """EgressProxy construction needs an identity trust root on disk."""
    from rvnd import signing
    signing.ensure_keypair()
    yield


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# A slow/degenerate upstream, configurable per test.
# ---------------------------------------------------------------------------


def _make_upstream(mode: str, *, hang_s: float = 30.0):
    """Return (server, port). ``mode`` selects the pathology:

      * ``hang_before_headers`` — accept, then sleep past any client timeout
        before sending a response line at all.
      * ``truncated_body`` — send 200 with a Content-Length larger than the
        bytes actually written, then close: the client's read raises
        http.client.IncompleteRead.
    """
    port = _free_port()

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            return

        def _drain(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)

        def do_POST(self):
            self._drain()
            if mode == "hang_before_headers":
                time.sleep(hang_s)
                return
            if mode == "truncated_body":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "1000")  # promise 1000…
                self.end_headers()
                self.wfile.write(b'{"partial":')            # …deliver ~11
                # returning closes the connection mid-body

        do_GET = do_POST

    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


# ---------------------------------------------------------------------------
# Egress proxy
# ---------------------------------------------------------------------------


def _start_proxy(upstream_port: int):
    from rvnd.lock import OversightLevel
    from rvnd.lock.egress_proxy import EgressProxy, autonomous_callback

    proxy_port = _free_port()
    proxy = EgressProxy(
        port=proxy_port,
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
        upstream_overrides={"stub.test": f"http://127.0.0.1:{upstream_port}"},
    )
    proxy.start()
    return proxy, proxy_port


def _post_through_proxy(proxy_port: int, *, client_timeout: float):
    req = urllib.request.Request(
        f"http://127.0.0.1:{proxy_port}/v1/messages",
        data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode(),
        headers={"X-Lock-Upstream": "stub.test", "Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=client_timeout)


def test_egress_hung_upstream_is_bounded_and_fails_closed(monkeypatch):
    """A genuinely hung upstream (accepts, never responds) must surface as a
    502 within the egress deadline — proving the transport timeout bounds a
    real hang, not just a mocked error-return. Without a transport timeout
    the worker thread would block until the client gave up."""
    monkeypatch.setenv("WORKSPACE_EGRESS_TIMEOUT_SECS", "2")
    upstream, up_port = _make_upstream("hang_before_headers", hang_s=30.0)
    proxy, proxy_port = _start_proxy(up_port)
    try:
        started = time.time()
        with pytest.raises(urllib.error.HTTPError) as ei:
            # Client patience well above the 2s egress deadline: the proxy,
            # not the client, must be the one to give up.
            _post_through_proxy(proxy_port, client_timeout=20)
        elapsed = time.time() - started
        assert ei.value.code == 502, f"expected fail-closed 502, got {ei.value.code}"
        assert elapsed < 10, (
            f"proxy took {elapsed:.1f}s to bound a hung upstream — the 2s "
            "egress timeout did not fire (transport has no read deadline?)")
        assert proxy.stats["errors"] >= 1
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()


def test_egress_truncated_body_is_caught_not_uncaught(monkeypatch):
    """Half-response regression: an upstream that promises a Content-Length
    and then closes mid-body makes urllib's read raise
    http.client.IncompleteRead — which subclasses HTTPException, not OSError
    or URLError. Before the fix it escaped the proxy's except tuple entirely:
    the forward already flushed 200 + headers, so the read fault could not be
    turned into a 502, but the uncaught exception meant the error went
    UNCOUNTED and the handler tore down without recording it.

    The status line is already on the wire by the time the body stalls, so a
    clean 502 is impossible here (that path is covered by the hung-before-
    headers test). The achievable, and load-bearing, guarantee is that the
    fault is CAUGHT: the proxy counts it as an upstream error and the server
    keeps serving. `proxy.stats["errors"]` incrementing is the exact signal —
    pre-fix, IncompleteRead escaped and never reached the `errors += 1`."""
    monkeypatch.setenv("WORKSPACE_EGRESS_TIMEOUT_SECS", "5")
    upstream, up_port = _make_upstream("truncated_body")
    proxy, proxy_port = _start_proxy(up_port)
    try:
        # The client may get a short 200 body or an incomplete read; either
        # way it must not hang. What we assert is server-side.
        try:
            resp = _post_through_proxy(proxy_port, client_timeout=10)
            resp.read()
        except (urllib.error.URLError, http.client.IncompleteRead,
                ConnectionError):
            pass  # client-side truncation is fine; we assert on the proxy

        assert proxy.stats["errors"] >= 1, (
            "IncompleteRead from a truncated upstream body was not counted as "
            "an upstream error — it escaped the fail-closed handler uncaught")

        # The proxy thread must have survived: the health endpoint answers.
        health = urllib.request.urlopen(
            f"http://127.0.0.1:{proxy_port}/__lock_health__", timeout=3)
        assert json.loads(health.read())["status"] == "ok", (
            "proxy did not survive a truncated-upstream forward")
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()


def test_egress_concurrency_is_bounded_sheds_load(monkeypatch):
    """RV-09 self-DoS bound: ThreadingHTTPServer spawns a worker thread per
    connection, and each forward against a hung upstream blocks that thread for
    the full egress deadline. Without a cap, a flood during an upstream outage
    is an unbounded-thread self-DoS. With the cap, in-flight forwards are bounded
    and excess requests are SHED with a 503 (fail-closed, a clear operator
    signal) rather than silently queued or each given a fresh blocking thread.

    We saturate the cap with hung-upstream forwards held open in background
    threads, then fire MORE requests than the cap and assert they come back 503
    immediately — proving load is shed, not absorbed by unbounded threads."""
    cap = 3
    extra = 5
    # Egress deadline long enough that the saturating forwards stay hung and hold
    # the semaphore through the whole assertion, but short enough that teardown
    # doesn't wait a full minute for the background threads to unwind.
    monkeypatch.setenv("WORKSPACE_EGRESS_TIMEOUT_SECS", "8")
    monkeypatch.setenv("WORKSPACE_EGRESS_MAX_CONCURRENCY", str(cap))

    upstream, up_port = _make_upstream("hang_before_headers", hang_s=30.0)
    proxy, proxy_port = _start_proxy(up_port)
    assert proxy.max_concurrency == cap, "cap override did not take effect"

    saturators: list[threading.Thread] = []

    def _hold_open():
        # Blocks on the hung upstream, holding one concurrency slot, until the
        # egress deadline turns it into a 502. Swallow whatever it becomes.
        try:
            _post_through_proxy(proxy_port, client_timeout=25).read()
        except Exception:
            pass

    try:
        for _ in range(cap):
            t = threading.Thread(target=_hold_open, daemon=True)
            t.start()
            saturators.append(t)

        # Wait until all `cap` saturating forwards have entered the handler and
        # taken their slot (received++ happens right after the semaphore acquire).
        deadline = time.time() + 5
        while proxy.stats["received"] < cap and time.time() < deadline:
            time.sleep(0.02)
        assert proxy.stats["received"] >= cap, (
            "saturating forwards never reached the handler")

        # Now the semaphore is fully held. Every additional request must be shed
        # with a 503 essentially immediately — NOT block on a new thread.
        for i in range(extra):
            started = time.time()
            with pytest.raises(urllib.error.HTTPError) as ei:
                _post_through_proxy(proxy_port, client_timeout=10).read()
            elapsed = time.time() - started
            assert ei.value.code == 503, (
                f"over-cap request {i} got {ei.value.code}, expected 503 load-shed")
            assert elapsed < 3, (
                f"over-cap request {i} took {elapsed:.1f}s — it was NOT shed but "
                "given a blocking thread (unbounded-thread vector still open)")

        assert proxy.stats["shed"] >= extra, (
            f"expected ≥{extra} shed requests, got {proxy.stats['shed']}")
        # The saturating forwards are still counted as received, not shed.
        assert proxy.stats["received"] == cap, (
            "an over-cap request slipped past the semaphore into the forward path")

        # The proxy stayed alive and observable while shedding.
        health = urllib.request.urlopen(
            f"http://127.0.0.1:{proxy_port}/__lock_health__", timeout=3)
        payload = json.loads(health.read())
        assert payload["status"] == "ok"
        assert payload["max_concurrency"] == cap
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()
        for t in saturators:
            t.join(timeout=10)


def test_egress_max_concurrency_is_configurable():
    """The concurrency cap honours WORKSPACE_EGRESS_MAX_CONCURRENCY, defaults to
    64, and ignores a non-positive / unparseable override (fail-safe default)."""
    import os
    from rvnd.lock.egress_proxy import _egress_max_concurrency

    saved = os.environ.get("WORKSPACE_EGRESS_MAX_CONCURRENCY")
    try:
        os.environ.pop("WORKSPACE_EGRESS_MAX_CONCURRENCY", None)
        assert _egress_max_concurrency() == 64
        os.environ["WORKSPACE_EGRESS_MAX_CONCURRENCY"] = "8"
        assert _egress_max_concurrency() == 8
        os.environ["WORKSPACE_EGRESS_MAX_CONCURRENCY"] = "0"
        assert _egress_max_concurrency() == 64, "non-positive must fall back"
        os.environ["WORKSPACE_EGRESS_MAX_CONCURRENCY"] = "1.5"
        assert _egress_max_concurrency() == 64, "unparseable must fall back"
    finally:
        if saved is None:
            os.environ.pop("WORKSPACE_EGRESS_MAX_CONCURRENCY", None)
        else:
            os.environ["WORKSPACE_EGRESS_MAX_CONCURRENCY"] = saved


def test_egress_timeout_is_configurable():
    """The forward deadline honours WORKSPACE_EGRESS_TIMEOUT_SECS, defaulting
    to 60s, and ignores a non-positive / unparseable override."""
    import os
    from rvnd.lock.egress_proxy import _egress_timeout_secs

    saved = os.environ.get("WORKSPACE_EGRESS_TIMEOUT_SECS")
    try:
        os.environ.pop("WORKSPACE_EGRESS_TIMEOUT_SECS", None)
        assert _egress_timeout_secs() == 60.0
        os.environ["WORKSPACE_EGRESS_TIMEOUT_SECS"] = "3.5"
        assert _egress_timeout_secs() == 3.5
        os.environ["WORKSPACE_EGRESS_TIMEOUT_SECS"] = "0"
        assert _egress_timeout_secs() == 60.0, "non-positive must fall back"
        os.environ["WORKSPACE_EGRESS_TIMEOUT_SECS"] = "not-a-number"
        assert _egress_timeout_secs() == 60.0, "unparseable must fall back"
    finally:
        if saved is None:
            os.environ.pop("WORKSPACE_EGRESS_TIMEOUT_SECS", None)
        else:
            os.environ["WORKSPACE_EGRESS_TIMEOUT_SECS"] = saved


# ---------------------------------------------------------------------------
# Local-model route (local_llm.complete_via)
# ---------------------------------------------------------------------------


def test_local_llm_real_hang_returns_typed_error_within_timeout():
    """complete_via against a genuinely hung endpoint must return a typed
    {ok: False} within the timeout, never block indefinitely. The mocked
    robustness suite proves the pipeline tolerates a timeout-shaped return;
    this proves the transport itself is deadline-bounded."""
    from rvnd.local_llm import complete_via

    upstream, up_port = _make_upstream("hang_before_headers", hang_s=30.0)
    try:
        started = time.time()
        result = complete_via(
            f"http://127.0.0.1:{up_port}",
            "mock-model",
            "classify this",
            timeout=2.0,
        )
        elapsed = time.time() - started
        assert elapsed < 10, (
            f"complete_via blocked {elapsed:.1f}s against a hung endpoint — "
            "the 2s timeout did not bound the socket read")
        assert result.get("ok") is False, (
            f"a hung endpoint must yield ok=False, got {result}")
        assert "error" in result
    finally:
        upstream.shutdown()
        upstream.server_close()
