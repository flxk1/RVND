# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Deployment bind semantics: the bridge leaves loopback only deliberately
and only behind a verified-identity proxy.

Claims under test (written before the logic):
  D1  default (no RVND_BIND): loopback bind; a foreign Host header is
      refused (anti-rebinding unchanged)
  D2  RVND_BIND to a non-loopback address without a declared principal
      header refuses to start, in words
  D3  a non-loopback bind also requires proxy identity proof configuration
  D4  with RVND_BIND + principal header + proof: a request addressed to the
      deployment's own host passes the guard, the principal is enforced,
      and a cross-origin request (Origin != Host) is still refused

Run: python -m pytest server/tests/test_deploy_bind.py -q
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "app"))
import serve  # noqa: E402


# Pin the bridge session token so the in-process caller can present it on
# POST /tool. Guard checks (loopback/Host/Origin) run before the token check,
# so the rebinding/cross-origin cases below still fail at the guard.
_BRIDGE_TOKEN = "deploy-bind-test-token"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("RVND_BRIDGE_TOKEN", _BRIDGE_TOKEN)
    return monkeypatch


def call(port, headers=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/tool",
        data=json.dumps({"tool": "workspace_workspace",
                         "args": {"op": "list"}}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Workspaces-Token": _BRIDGE_TOKEN, **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_default_keeps_rebinding_guard(env):                     # D1
    srv = serve.make_server(port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, out = call(port, {"Host": "attacker.example"})
        assert status == 403 and "rebinding" in out["error"]
        status, out = call(port)
        assert status == 200
    finally:
        srv.shutdown()


def test_nonloopback_without_trust_refuses_start(env):           # D2
    env.setenv("RVND_BIND", "0.0.0.0")
    env.delenv("WORKSPACE_PRINCIPAL_HEADER", raising=False)
    with pytest.raises(SystemExit, match="verified-identity proxy"):
        serve.make_server(port=0)


def test_deployed_guard_same_origin_and_principal(env):          # D3
    env.setenv("RVND_BIND", "0.0.0.0")
    env.setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    with pytest.raises(SystemExit, match="WORKSPACE_PROXY_SHARED_SECRET"):
        serve.make_server(port=0)


def test_deployed_guard_same_origin_and_principal_with_proof(env):  # D4
    env.setenv("RVND_BIND", "0.0.0.0")
    env.setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    env.setenv("WORKSPACE_PROXY_SHARED_SECRET", "deployment-proof")
    srv = serve.make_server(port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        hdr = {"Host": "rvnd.corp.example",
               "X-Auth-Request-Email": "dana\x40corp.example",
               "X-RVND-Proxy-Proof": "deployment-proof"}
        status, out = call(port, hdr)
        assert status == 200 and out.get("ok") is not False
        status, out = call(port, {"Host": "rvnd.corp.example",
                                 "X-Auth-Request-Email": "dana\x40corp.example"})
        assert status == 403 and "proxy identity proof" in out["error"]
        # a request with no principal fails closed (proxy missing)
        status, out = call(port, {"Host": "rvnd.corp.example",
                                 "X-RVND-Proxy-Proof": "deployment-proof"})
        assert out["ok"] is False and "no principal header" in out["error"]
        # cross-origin (Origin names another site) is refused
        status, out = call(port, {**hdr, "Origin": "https://evil.example"})
        assert status == 403 and "cross-origin" in out["error"]
        # same-origin (Origin matches Host) passes
        status, out = call(port, {**hdr, "Origin": "https://rvnd.corp.example"})
        assert status == 200
    finally:
        srv.shutdown()
