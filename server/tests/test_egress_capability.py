# SPDX-License-Identifier: AGPL-3.0-only
"""Real signed session admission occurs before any egress proxy routing."""
import json
import socket
import urllib.error
import urllib.request

import pytest

from workspaces import signing
from workspaces.lock.egress_proxy import EgressProxy, autonomous_callback
from workspaces.lock.oversight import OversightLevel
from workspaces.session_capability import CapabilityVerifier, mint
from workspaces.session_capability import CapabilityError
from workspaces.mutation_log import MutationLog

pytestmark = pytest.mark.live_egress_capability


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _post(port: int, token: str = ""):
    headers = {"X-Lock-Upstream": "not-allowed.invalid"}
    if token:
        headers["X-Rvnd-Capability"] = token
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/messages",
        data=json.dumps({"messages": [{"content": "clean"}]}).encode(),
        headers=headers,
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=2)
    return error.value


def test_proxy_refuses_missing_invalid_and_revoked_before_routing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    signing.ensure_keypair()
    verifier = CapabilityVerifier.from_key_dir()
    token, claims = mint(
        party="bot",
        lane_id="lane",
        folder=str(tmp_path / "workspace"),
        grade="L2",
        policy_fingerprint="sha256:policy",
        spec_fingerprint="sha256:spec",
        uid=123,
    )
    port = _port()
    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
        capability_verifier=verifier,
    )
    proxy.start()
    try:
        missing = _post(port)
        invalid = _post(port, "not-a-capability")
        valid = _post(port, token)
        verifier.revoke(claims.nonce)
        revoked = _post(port, token)
        assert b"session capability refused" in missing.read()
        assert b"session capability refused" in invalid.read()
        # A genuine live token reaches routing, which then rejects the deliberately
        # disallowed upstream. This proves admission runs first.
        assert b"not allowed by lock" in valid.read()
        assert b"session capability refused" in revoked.read()
        assert proxy.stats["blocked"] == 4
        with pytest.raises(CapabilityError, match="revoked"):
            CapabilityVerifier.from_key_dir().verify(token)
    finally:
        proxy.stop()


def test_workspace_bound_proxy_signs_capability_refusal(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    folder = str(tmp_path / "workspace")
    log_root = str(tmp_path / "logs")
    signing.ensure_keypair()
    port = _port()
    proxy = EgressProxy(
        port=port,
        track_folder=folder,
        track_log_root=log_root,
        capability_verifier=CapabilityVerifier.from_key_dir(),
    )
    proxy.start()
    try:
        _post(port)
    finally:
        proxy.stop()
    incidents = [
        event for event in MutationLog(folder, log_root=log_root).replay()
        if (event.extra or {}).get("incident_type") == "oversight-bypassed"
    ]
    assert len(incidents) == 1
    assert incidents[0].actor == "egress-proxy"
    assert incidents[0].signature
