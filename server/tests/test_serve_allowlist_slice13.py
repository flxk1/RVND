# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""app/serve.py local bridge hardening.

N5: the bridge did getattr(mcp_server, tool) for ANY name, so any same-machine
    process (or a malicious web page via DNS rebinding) could invoke ANY
    module-level callable — write surfaces, private readers, internal helpers.
Handlers must surface a clean error across the bridge, never a 500 crash.

These tests assert the allowlist is curated + accurate and that the loopback /
Host / Origin guards refuse cross-origin and rebinding requests."""
from __future__ import annotations

import http.client
import importlib.util
import json
import threading
from pathlib import Path

import pytest

_SERVE_PATH = Path(__file__).resolve().parents[2] / "app" / "serve.py"


def _load_serve():
    spec = importlib.util.spec_from_file_location("rvnd_serve_under_test", _SERVE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


serve = _load_serve()


# ── allowlist is curated, accurate, and excludes dangerous bare callables ─────

def test_every_allowed_tool_is_a_real_callable():
    from workspaces import mcp_server
    for tool in serve.ALLOWED_TOOLS:
        assert callable(getattr(mcp_server, tool, None)), f"{tool} missing/!callable"


def test_dangerous_bare_callables_are_not_allowlisted():
    # private readers + raw write surfaces must only be reachable via a facade.
    for tool in ("write_file_to_folder", "by_id", "search", "route_to_workspace",
                 "recent_dispatches", "os", "sys", "__import__", "_facade_call"):
        assert tool not in serve.ALLOWED_TOOLS


def test_facade_call_refuses_unlisted_tool_without_invoking():
    res = serve._facade_call("write_file_to_folder",
                             {"folder_context": "/tmp", "filename": "x",
                              "content_b64": "eA=="})
    assert "error" in res
    assert "not callable over the local app bridge" in res["error"]


def test_facade_call_allows_a_listed_tool():
    res = serve._facade_call("server_info", {})
    assert isinstance(res, dict)
    assert "error" not in res


def test_allowlist_exactly_equals_declared_tools():
    # The strongest sync guarantee: the bridge's surface IS the registered MCP
    # surface — no folded tool leaks in, no declared tool is unreachable.
    from workspaces import mcp_server
    assert serve.ALLOWED_TOOLS == frozenset(mcp_server._DECLARED_TOOLS)


@pytest.mark.parametrize("folded", ["reason", "workspace_cascade", "workspace_shadow_scan"])
def test_folded_tools_are_not_directly_callable(folded):
    # These were folded into facade ops (workspace_memory(reason) / workspace_model(cascade)
    # / workspace_audit(shadow_scan)); invoking them directly would bypass the facade.
    assert folded not in serve.ALLOWED_TOOLS
    res = serve._facade_call(folded, {"folder_context": "/tmp"})
    assert "not callable over the local app bridge" in res["error"]


def test_cross_workspace_read_is_reachable():
    # A real declared standalone tool must NOT be silently unreachable.
    assert "cross_workspace_read" in serve.ALLOWED_TOOLS


def test_facade_op_dispatched_via_op_parameter():
    # An op-based facade is invoked as fn(op, params) — verified by it running
    # rather than erroring on a signature mismatch.
    res = serve._facade_call("workspace_audit", {"op": "discipline", "params": {}})
    assert isinstance(res, dict)


def test_allowed_tool_bad_args_returns_clean_error_not_crash():
    # An allowed tool called with bad kwargs yields a clean error dict, never
    # an uncaught exception across the bridge.
    res = serve._facade_call("server_info", {"bogus_kwarg": "x"})
    assert "error" in res
    assert "TypeError" in res["error"]


# ── Host / Origin / loopback predicates ──────────────────────────────────────

@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1:8799", True), ("localhost:8799", True), ("[::1]:8799", True),
    ("127.0.0.1", True), ("evil.com", False), ("evil.com:8799", False), ("", False),
])
def test_host_is_local(host, ok):
    assert serve._host_is_local(host) is ok


@pytest.mark.parametrize("origin,ok", [
    (None, True), ("", True), ("http://127.0.0.1:8799", True),
    ("http://localhost:8799", True), ("http://evil.com", False),
    ("https://evil.com:8799", False),
])
def test_origin_is_local(origin, ok):
    assert serve._origin_is_local(origin) is ok


def test_is_loopback_ip():
    assert serve._is_loopback_ip("127.0.0.1")
    assert serve._is_loopback_ip("::1")
    assert not serve._is_loopback_ip("10.0.0.5")
    assert not serve._is_loopback_ip("192.168.1.4")


# ── end-to-end through the real handler ──────────────────────────────────────

# Pin the session token so the in-process caller presents it on POST /tool.
_BRIDGE_TOKEN = "serve-allowlist-test-token"


@pytest.fixture
def server_port(monkeypatch):
    monkeypatch.setenv("RVND_BRIDGE_TOKEN", _BRIDGE_TOKEN)
    srv = serve.make_server(port=0)               # ephemeral loopback port
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield port
    finally:
        srv.shutdown()


# _TOKEN_UNSET distinguishes "send the pinned token" (default) from "send no
# token header at all" — the latter is the fail-closed missing-token case.
_TOKEN_UNSET = object()


def _post(port, body, headers=None, token=_TOKEN_UNSET):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = {"Content-Type": "application/json"}
    tok = _BRIDGE_TOKEN if token is _TOKEN_UNSET else token
    if tok is not None:
        hdrs["X-Workspaces-Token"] = tok
    if headers:
        hdrs.update(headers)
    conn.request("POST", "/tool", json.dumps(body), hdrs)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, json.loads(data or b"{}")


def test_post_rejects_nonlocal_host(server_port):
    status, body = _post(server_port, {"tool": "server_info", "args": {}},
                         {"Host": "evil.com"})
    assert status == 403
    assert "rebinding" in body["error"] or "Host" in body["error"]


def test_post_rejects_cross_origin(server_port):
    status, body = _post(server_port, {"tool": "server_info", "args": {}},
                         {"Origin": "http://evil.com"})
    assert status == 403
    assert "cross-origin" in body["error"]


def test_post_allows_local_listed_tool(server_port):
    # http.client sets Host: 127.0.0.1:<port>, no Origin → permitted.
    status, body = _post(server_port, {"tool": "server_info", "args": {}})
    assert status == 200
    assert "error" not in body


def test_post_refuses_unlisted_tool_with_clean_error(server_port):
    status, body = _post(server_port,
                         {"tool": "write_file_to_folder", "args": {}})
    assert status == 200                          # clean error envelope, not 500
    assert "error" in body
    assert "not callable over the local app bridge" in body["error"]


def test_post_allowed_tool_bad_args_is_200_clean_error(server_port):
    # N4 end-to-end: a real tool with bad args → 200 + error, never a 500 crash.
    status, body = _post(server_port,
                         {"tool": "server_info", "args": {"bogus_kwarg": "x"}})
    assert status == 200
    assert "error" in body


# ── per-session token gate on /tool (fail-closed) ────────────────────────────

def test_post_without_token_is_refused(server_port):
    # A same-machine caller with no token is refused before any dispatch: the
    # loopback boundary alone no longer admits the full read+write surface.
    status, body = _post(server_port, {"tool": "server_info", "args": {}},
                         token=None)
    assert status == 403
    assert "session token" in body["error"]


def test_post_with_wrong_token_is_refused(server_port):
    status, body = _post(server_port, {"tool": "server_info", "args": {}},
                         token="not-the-session-token")
    assert status == 403
    assert "session token" in body["error"]


def test_post_with_correct_token_dispatches(server_port):
    status, body = _post(server_port, {"tool": "server_info", "args": {}},
                         token=_BRIDGE_TOKEN)
    assert status == 200
    assert "error" not in body


def test_make_server_exposes_session_token(monkeypatch):
    # The token is readable off the returned server so a harness can present it.
    monkeypatch.setenv("RVND_BRIDGE_TOKEN", _BRIDGE_TOKEN)
    srv = serve.make_server(port=0)
    try:
        assert srv.session_token == _BRIDGE_TOKEN
    finally:
        srv.server_close()


def test_make_server_generates_token_when_unset(monkeypatch):
    # Without RVND_BRIDGE_TOKEN the server mints its own per-session token.
    monkeypatch.delenv("RVND_BRIDGE_TOKEN", raising=False)
    srv = serve.make_server(port=0)
    try:
        assert srv.session_token and len(srv.session_token) >= 32
    finally:
        srv.server_close()
