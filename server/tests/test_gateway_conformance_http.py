# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Gateway conformance over REAL streamable HTTP (committed G4).

Spawns the actual gateway app (auth middleware + FastMCP streamable-http)
on a uvicorn server in-process, then runs `workspaces.gateway_verify` against it
exactly the way a host's MCP client would — network and all. This is the
repo-resident version of the G4 smoke test: every claim a host guide makes
about the gateway side is asserted here, not documented.

Marked `conformance`: slower than unit tests (real sockets), still <10s.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from workspaces import gateway_verify
from workspaces.gateway import build_app

HOST_LABEL = "conformance"
TOKEN = "tok-conformance-e2e"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_gateway(tmp_path_factory):
    """Real gateway on a real port; yields (url, workspace_folder)."""
    import uvicorn

    # Token table via env, exactly like production startup.
    import os
    old = os.environ.get("WORKSPACES_GATEWAY_TOKENS")
    os.environ["WORKSPACES_GATEWAY_TOKENS"] = f"{HOST_LABEL}:{TOKEN}"
    port = _free_port()
    config = uvicorn.Config(build_app(), host="127.0.0.1", port=port,
                            log_level="error", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("gateway did not start within 15s")
        time.sleep(0.05)
    folder = tmp_path_factory.mktemp("gw-conformance-flow")
    yield f"http://127.0.0.1:{port}/mcp", str(folder)
    server.should_exit = True
    thread.join(timeout=10)
    if old is None:
        os.environ.pop("WORKSPACES_GATEWAY_TOKENS", None)
    else:
        os.environ["WORKSPACES_GATEWAY_TOKENS"] = old


def test_full_conformance_over_http(live_gateway):
    """The whole verifier must pass against a live server: auth (401/401/200),
    7-tool profile, trifecta, blocked ops, filtered help, string-payload
    ingress with PII findings, egress over-collection, idempotent
    request_approval under retry, action_summary on the poll, signoff state
    flip, receipts with host attribution, chain verify."""
    url, folder = live_gateway
    report = asyncio.run(gateway_verify.run_checks(
        url, TOKEN, label=HOST_LABEL, folder=folder))
    failures = [f"{c['check']}: {c['detail']}" for c in report.failed]
    assert not failures, "conformance failures:\n  " + "\n  ".join(failures)
    # the verifier must actually have run the full suite, not short-circuited
    names = {c["check"] for c in report.checks}
    assert {"auth.reject_missing_token", "profile.exactly_expected",
            "oversight.retry_is_idempotent",
            "receipts.host_label_attribution",
            "audit.chain_verifies"} <= names


def test_verifier_exit_codes(live_gateway, capsys):
    """CLI contract: exit 0 on conformance, 2 when unreachable."""
    url, folder = live_gateway
    rc = gateway_verify.main(["--url", url, "--token", TOKEN,
                              "--label", HOST_LABEL, "--folder", folder,
                              "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and '"passed": true' in out
    rc_unreachable = gateway_verify.main(
        ["--url", "http://127.0.0.1:1/mcp", "--token", "x"])
    assert rc_unreachable == 2


def test_verifier_fails_loud_on_wrong_token(live_gateway):
    """With a bad token the verifier reports auth.accept_valid_token FAIL
    (and stops) rather than crashing — hosts will paste wrong tokens."""
    url, folder = live_gateway
    report = asyncio.run(gateway_verify.run_checks(
        url, "totally-wrong", label=HOST_LABEL, folder=folder))
    by_name = {c["check"]: c["ok"] for c in report.checks}
    assert by_name.get("auth.accept_valid_token") is False
