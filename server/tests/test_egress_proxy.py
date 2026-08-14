# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the egress proxy — the enforced gate.

Covers:
- Prompt extraction from Anthropic / OpenAI / legacy request shapes
- Scan finding rollup
- Approval callbacks per oversight level
- Block-on-disallowed-upstream
- Audit log writes
- End-to-end: spin up real local server, fire a request, observe decision

The end-to-end test uses a stub upstream (Python's own http.server) so we can
verify the forwarding path without touching api.anthropic.com.
"""

from __future__ import annotations

import io
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.security  # red-team-relevant: runs in the `-m security` gate


@pytest.fixture(autouse=True)
def _identity_keypair():
    """Constructing an EgressProxy builds a CapabilityVerifier, which needs an
    identity keypair on disk (its trust root). conftest points the key dir at a
    fresh temp HOME per session, so without this the FIRST proxy-constructing
    test in a run fails with 'identity trust root unavailable' and later ones
    only pass because an earlier test happened to mint a key — an isolation
    bug. Mint one up front so every test in this module stands alone."""
    from workspaces import signing
    signing.ensure_keypair()
    yield

from workspaces.lock import Finding, OversightLevel
from workspaces.lock.egress_proxy import (
    ApprovalDecision,
    EgressProxy,
    GateDecision,
    PendingRequest,
    _credential_binding_violation,
    _block_all_callback,
    _sanitise_agent_id,
    _upstream_request_url,
    autonomous_callback,
    block_on_findings_callback,
    extract_prompt_text,
    make_default_callback,
    notify_callback,
    request_agent_identity,
    resolve_agent_identity,
    scan_prompt,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from workspaces import agent_keys as _agent_keys
from workspaces import web_bot_auth as _wba


# ---------------------------------------------------------------------------
# Prompt extraction
# ---------------------------------------------------------------------------


def test_extract_anthropic_messages():
    body = json.dumps({
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "What's Maria Schmidt's salary?"},
        ],
    }).encode()
    text = extract_prompt_text("api.anthropic.com", body)
    assert "Maria Schmidt" in text


def test_extract_anthropic_system_prompt():
    body = json.dumps({
        "system": "You are a privacy-conscious HR assistant.",
        "messages": [{"role": "user", "content": "Look up the candidate."}],
    }).encode()
    text = extract_prompt_text("api.anthropic.com", body)
    assert "privacy-conscious" in text
    assert "candidate" in text


def test_extract_structured_system_prompt():
    body = json.dumps({
        "system": [
            {"type": "text", "text": "Contact alice\x40example.com"},
            {"type": "text", "text": "Keep this private."},
        ],
        "messages": [{"role": "user", "content": "Summarise this."}],
    }).encode()
    text = extract_prompt_text("api.anthropic.com", body)
    assert "alice\x40example.com" in text
    assert "Keep this private" in text


def test_extract_anthropic_content_block_array():
    body = json.dumps({
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Block 1 with email alice\x40example.com"},
                    {"type": "image", "source": "..."},
                    {"type": "text", "text": "Block 2"},
                ],
            },
        ],
    }).encode()
    text = extract_prompt_text("api.anthropic.com", body)
    assert "alice\x40example.com" in text
    assert "Block 1" in text
    assert "Block 2" in text


def test_extract_openai_messages():
    body = json.dumps({
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "What is patient John Doe's diagnosis?"},
        ],
    }).encode()
    text = extract_prompt_text("api.openai.com", body)
    assert "John Doe" in text
    assert "Be helpful" in text


def test_extract_legacy_completions_prompt():
    body = json.dumps({"prompt": "Maria Schmidt called yesterday."}).encode()
    text = extract_prompt_text("api.openai.com", body)
    assert "Maria Schmidt" in text


def test_extract_malformed_body_returns_empty():
    text = extract_prompt_text("api.anthropic.com", b"not-json")
    assert text == ""


def test_extract_non_utf8_body_returns_empty():
    text = extract_prompt_text("api.anthropic.com", b"\xff\xfe\xfd")
    assert text == ""


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def test_scan_clean_prompt_no_findings():
    findings = scan_prompt("aggregate metrics for the team this quarter")
    assert findings == []


def test_scan_prompt_with_email_flags_tier_b():
    findings = scan_prompt("please email alice\x40example.com about it")
    assert any(f.tier == "B" for f in findings)


def test_scan_uses_tier_c_via_mock_backend(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    from workspaces.lock.tier_c import reset_backend_cache
    reset_backend_cache()
    findings = scan_prompt("patient prescribed chemotherapy and recovering well")
    assert any(f.tier == "C" for f in findings)


# ---------------------------------------------------------------------------
# Callback dispatch
# ---------------------------------------------------------------------------


def _pending_with_high_finding() -> PendingRequest:
    return PendingRequest(
        request_id="req-test-1",
        upstream_host="api.anthropic.com",
        method="POST",
        path="/v1/messages",
        body=b"{}",
        extracted_text="some text",
        findings=[Finding(tier="B", type="pii_in_argument", severity="high",
                          field=None, detail="email pattern")],
        oversight=OversightLevel.APPROVE,
    )


def _pending_clean() -> PendingRequest:
    return PendingRequest(
        request_id="req-test-2",
        upstream_host="api.anthropic.com",
        method="POST",
        path="/v1/messages",
        body=b"{}",
        extracted_text="benign text",
        findings=[],
        oversight=OversightLevel.APPROVE,
    )


def test_autonomous_callback_always_allows():
    assert autonomous_callback(_pending_with_high_finding()).action == "allow"


def test_notify_callback_always_allows():
    assert notify_callback(_pending_with_high_finding()).action == "allow"


def test_block_on_findings_allows_clean():
    assert block_on_findings_callback(_pending_clean()).action == "allow"


def test_block_on_findings_blocks_high():
    assert block_on_findings_callback(_pending_with_high_finding()).action == "block"


def test_manual_callback_always_blocks():
    assert _block_all_callback(_pending_clean()).action == "block"
    assert _block_all_callback(_pending_with_high_finding()).action == "block"


def test_make_default_callback_per_level():
    # Quick sanity: each level produces a callable
    for lvl in OversightLevel:
        cb = make_default_callback(lvl)
        assert callable(cb)


# ---------------------------------------------------------------------------
# Proxy lifecycle
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Find a free TCP port for testing."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_proxy_starts_and_stops():
    port = _free_port()
    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
    )
    proxy.start()
    try:
        # Health endpoint
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/__lock_health__", timeout=2)
        data = json.loads(resp.read().decode())
        assert data["status"] == "ok"
        assert data["oversight"] == "autonomous"
    finally:
        proxy.stop()


def test_proxy_blocks_disallowed_upstream():
    port = _free_port()
    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
    )
    proxy.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=b'{}',
            headers={"X-Lock-Upstream": "evil.example.com"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=2)
        assert ei.value.code == 403
    finally:
        proxy.stop()


def test_upstream_url_preserves_origin_form_path_and_query():
    assert (
        _upstream_request_url("https://api.anthropic.com", "/v1/messages?beta=1")
        == "https://api.anthropic.com/v1/messages?beta=1"
    )


@pytest.mark.parametrize("target", [
    "@127.0.0.1:9000/v1/messages",
    "//127.0.0.1:9000/v1/messages",
    "https://127.0.0.1:9000/v1/messages",
    "/v1/messages#fragment",
])
def test_upstream_url_rejects_targets_that_can_select_an_authority(target):
    with pytest.raises(ValueError):
        _upstream_request_url("https://api.anthropic.com", target)


def test_proxy_userinfo_request_target_never_reaches_attacker():
    attacker_port = _free_port()
    proxy_port = _free_port()
    attacker_received = []

    class AttackerHandler(BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            return

        def do_POST(self):
            attacker_received.append(self.path)
            self.send_response(200)
            self.end_headers()

    attacker = ThreadingHTTPServer(("127.0.0.1", attacker_port), AttackerHandler)
    threading.Thread(target=attacker.serve_forever, daemon=True).start()
    proxy = EgressProxy(
        port=proxy_port,
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
        upstream_overrides={"trusted.test": "http://trusted.test"},
    )
    proxy.start()
    try:
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=2) as client:
            target = f"@127.0.0.1:{attacker_port}/v1/messages"
            request = (
                f"POST {target} HTTP/1.1\r\n"
                "Host: trusted.test\r\n"
                "Content-Length: 2\r\n"
                "Connection: close\r\n\r\n{}"
            )
            client.sendall(request.encode("ascii"))
            response = client.recv(4096)
        assert b"403" in response
        assert attacker_received == []
    finally:
        proxy.stop()
        attacker.shutdown()
        attacker.server_close()


def test_proxy_blocks_when_callback_says_block():
    port = _free_port()

    def reject_all(pending):
        return ApprovalDecision(action="block", reason="test reject")

    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.SUPERVISED,
        approval_callback=reject_all,
    )
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": "Maria Schmidt's salary?"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=body,
            headers={
                "X-Lock-Upstream": "api.anthropic.com",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=2)
        assert ei.value.code == 403
        body = json.loads(ei.value.read().decode())
        assert "blocked by agent-tool-lock" in body["error"]
        assert body["reason"] == "test reject"
        assert proxy.stats["blocked"] == 1
        assert proxy.stats["allowed"] == 0
    finally:
        proxy.stop()


def test_proxy_writes_audit_log_on_decision(tmp_path):
    port = _free_port()
    audit = tmp_path / "audit.jsonl"

    def reject_all(pending):
        return ApprovalDecision(action="block", reason="test")

    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.SUPERVISED,
        approval_callback=reject_all,
        audit_log_path=str(audit),
    )
    proxy.start()
    try:
        # Use text that triggers the gate so the callback fires and we exercise
        # the block path. (Plain "hello" passes the gate directly without
        # invoking the callback under the new gate-first flow.)
        body = json.dumps({"messages": [{"role": "user", "content": "contact alice\x40example.com"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=body,
            headers={"X-Lock-Upstream": "api.anthropic.com"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
        except urllib.error.HTTPError:
            pass  # expected
    finally:
        proxy.stop()

    # The audit log may contain both lock_text "kind=text" entries (written
    # by the gate from inside gate_for_cloud) and proxy "kind=proxy_decision"
    # entries (written by the HTTP handler). Filter to the latter.
    lines = audit.read_text().strip().splitlines()
    entries = [json.loads(l) for l in lines]
    decisions = [e for e in entries if e.get("kind") == "proxy_decision"]
    assert len(decisions) >= 1
    entry = decisions[0]
    assert entry["upstream"] == "api.anthropic.com"
    assert entry["action"] == "block"


def test_proxy_audit_log_records_findings_summary(tmp_path, monkeypatch):
    """End-to-end: prompt with email triggers Tier B; audit records the finding."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    from workspaces.lock.tier_c import reset_backend_cache
    reset_backend_cache()

    port = _free_port()
    audit = tmp_path / "audit.jsonl"

    def reject(pending):
        return ApprovalDecision(action="block", reason="test reject")

    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.SUPERVISED,
        approval_callback=reject,
        audit_log_path=str(audit),
    )
    proxy.start()
    try:
        body = json.dumps({
            "messages": [{"role": "user", "content": "Reach Maria Schmidt at maria\x40example.com"}],
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=body,
            headers={"X-Lock-Upstream": "api.anthropic.com"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
        except urllib.error.HTTPError:
            pass
    finally:
        proxy.stop()

    entries = [json.loads(line) for line in audit.read_text().strip().splitlines()]
    decision_entries = [e for e in entries if e.get("kind") == "proxy_decision"]
    assert len(decision_entries) == 1
    entry = decision_entries[0]
    assert entry["findings_count"] >= 1
    severities = {f["severity"] for f in entry["findings_summary"]}
    # Email pattern → HIGH tier B
    assert "high" in severities


def test_proxy_forwards_to_stub_upstream():
    """Verify the proxy actually proxies a successful request to a stub upstream."""
    # Spin up a stub upstream on a port; configure proxy to allow it
    upstream_port = _free_port()
    proxy_port = _free_port()

    upstream_received = []

    class StubHandler(BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            upstream_received.append({
                "path": self.path,
                "body": self.rfile.read(length).decode(),
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true, "from": "stub-upstream"}')

    upstream_server = ThreadingHTTPServer(("127.0.0.1", upstream_port), StubHandler)
    upstream_thread = threading.Thread(target=upstream_server.serve_forever, daemon=True)
    upstream_thread.start()

    proxy = EgressProxy(
        port=proxy_port,
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
        upstream_overrides={"stub.test": f"http://127.0.0.1:{upstream_port}"},
    )
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/messages",
            data=body,
            headers={
                "X-Lock-Upstream": "stub.test",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=2)
        out = json.loads(resp.read().decode())
        assert out == {"ok": True, "from": "stub-upstream"}
        assert proxy.stats["allowed"] == 1
        assert len(upstream_received) == 1
        assert upstream_received[0]["path"] == "/v1/messages"
    finally:
        proxy.stop()
        upstream_server.shutdown()
        upstream_server.server_close()


def test_proxy_modify_path_substitutes_body():
    """If callback returns action='modify' with modified_body, proxy forwards the modified bytes."""
    upstream_port = _free_port()
    proxy_port = _free_port()

    upstream_received = []

    class StubHandler(BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            upstream_received.append(self.rfile.read(length).decode())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

    upstream_server = ThreadingHTTPServer(("127.0.0.1", upstream_port), StubHandler)
    threading.Thread(target=upstream_server.serve_forever, daemon=True).start()

    def redact_callback(pending):
        return ApprovalDecision(
            action="modify",
            modified_body=b'{"messages":[{"role":"user","content":"[REDACTED]"}]}',
            reason="redacted Maria Schmidt",
        )

    proxy = EgressProxy(
        port=proxy_port,
        oversight=OversightLevel.SUPERVISED,
        approval_callback=redact_callback,
        upstream_overrides={"stub.test": f"http://127.0.0.1:{upstream_port}"},
    )
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": "Maria Schmidt's salary?"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/messages",
            data=body,
            headers={"X-Lock-Upstream": "stub.test"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
        assert proxy.stats["modified"] == 1
        assert "[REDACTED]" in upstream_received[0]
        assert "Maria Schmidt" not in upstream_received[0]
    finally:
        proxy.stop()
        upstream_server.shutdown()
        upstream_server.server_close()


def test_proxy_waiver_path_logs_waived_findings(tmp_path):
    """User waiver → forwarded but findings are recorded in audit log."""
    port = _free_port()
    audit = tmp_path / "audit.jsonl"

    def waive_callback(pending):
        return ApprovalDecision(
            action="allow",
            reason="user waiver — business override",
            waived_findings=[f.detail for f in pending.findings],
        )

    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.SUPERVISED,
        approval_callback=waive_callback,
        upstream_overrides={"stub.test": "http://127.0.0.1:1"},  # unreachable
        audit_log_path=str(audit),
    )
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": "alice\x40example.com"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=body,
            headers={"X-Lock-Upstream": "stub.test"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=1)
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass  # upstream unreachable — that's ok, audit log is what matters
    finally:
        proxy.stop()

    entries = [json.loads(l) for l in audit.read_text().strip().splitlines()]
    decision = next(e for e in entries if e.get("kind") == "proxy_decision")
    assert decision["action"] == "allow"
    assert "user waiver" in decision["reason"]
    assert len(decision["waived_findings"]) >= 1


# ===========================================================================
# Gate-integrated flow (vault context + DecisionsStore + new actions)
# ===========================================================================


def test_proxy_audit_records_new_gate_fields(tmp_path, monkeypatch):
    """The audit entry must carry the new gate fields alongside the legacy 'action'."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    from workspaces.lock.tier_c import reset_backend_cache
    reset_backend_cache()

    port = _free_port()
    audit = tmp_path / "audit.jsonl"
    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.NOTIFY,   # below APPROVE → no ask_user
        audit_log_path=str(audit),
        decisions_path=str(tmp_path / "decisions.jsonl"),
    )
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": "contact alice\x40example.com"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=body,
            headers={"X-Lock-Upstream": "api.anthropic.com"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
        except urllib.error.HTTPError:
            pass
    finally:
        proxy.stop()

    entries = [json.loads(l) for l in audit.read_text().strip().splitlines()]
    decisions = [e for e in entries if e.get("kind") == "proxy_decision"]
    assert len(decisions) >= 1
    d = decisions[0]
    assert d["gate_action"] == "refuse"             # lock said no
    assert d["final_action"] == "refuse"
    assert d["action"] == "block"                   # legacy alias
    assert d["recalled_from_decisions"] is False
    assert d["vault_context_loaded"] is False       # no vault path configured
    assert "text_length" in d                       # never the text itself
    # The audit MUST NOT contain the raw text.
    assert "alice\x40example.com" not in audit.read_text()


def test_proxy_vault_context_refuses_confidential_term(tmp_path, monkeypatch):
    """End-to-end: a vault declares 'Workspaceversum' confidential; a prompt mentioning it refuses."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    from workspaces.lock.tier_c import reset_backend_cache
    reset_backend_cache()

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Workspaceversum.md").write_text("---\nconfidential: true\n---\nbody\n")

    port = _free_port()
    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.NOTIFY,    # no ask_user — refuse passes through
        vault_path=str(vault),
        decisions_path=str(tmp_path / "decisions.jsonl"),
    )
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": "ship workspaceversum in june"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=body,
            headers={"X-Lock-Upstream": "api.anthropic.com"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=2)
        assert ei.value.code == 403
        body_resp = json.loads(ei.value.read().decode())
        assert "blocked by agent-tool-lock" in body_resp["error"]
        # Reason should reference the confidential finding.
        assert any(
            f.get("type") in ("pii_in_argument",) or f.get("severity") == "high"
            for f in body_resp["findings"]
        )
    finally:
        proxy.stop()


def test_proxy_decisions_store_short_circuits_refusal(tmp_path, monkeypatch):
    """A prior 'allow always' decision lets a previously-refused prompt through.

    Two-step:
      1. With no remembered decision, prompt with email is refused (oversight=notify).
      2. After decisions.remember(text, allow, scope=always), proxy short-circuits → allow.
    """
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    from workspaces.lock.tier_c import reset_backend_cache
    from workspaces.lock import DecisionsStore
    reset_backend_cache()

    decisions_path = tmp_path / "decisions.jsonl"
    audit = tmp_path / "audit.jsonl"

    # Pre-seed: user previously approved this exact text always.
    pre_store = DecisionsStore(decisions_path)
    candidate = "contact alice\x40example.com"
    pre_store.remember(candidate, "allow", scope="always", actor="tester", reason="approved at setup")

    port = _free_port()
    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.NOTIFY,
        audit_log_path=str(audit),
        decisions_path=str(decisions_path),
        upstream_overrides={"stub.test": "http://127.0.0.1:1"},  # unreachable; we'll catch URLError
    )
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": candidate}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=body,
            headers={"X-Lock-Upstream": "stub.test"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=1)
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass  # we only care about the audit log
    finally:
        proxy.stop()

    entries = [json.loads(l) for l in audit.read_text().strip().splitlines()]
    decisions = [e for e in entries if e.get("kind") == "proxy_decision"]
    assert len(decisions) >= 1
    d = decisions[0]
    assert d["recalled_from_decisions"] is True
    assert d["final_action"] == "allow"
    assert d["action"] == "allow"                   # legacy alias


def test_proxy_minimise_redacts_request_body_before_forward(tmp_path, monkeypatch):
    """When the gate returns 'minimise', the proxy forwards a redacted body."""
    # We exercise the redact_body_in_place helper directly. End-to-end the gate
    # mostly returns refuse on HIGH-severity findings under STANDARD mode, so the
    # minimise path is rarely hit through the proxy in practice — but the helper
    # is invoked when it does fire.
    from workspaces.lock.egress_proxy import redact_body_in_place

    body = json.dumps({"messages": [{"role": "user", "content": "write to alice\x40example.com please"}]}).encode()
    redacted = redact_body_in_place(body, "api.anthropic.com")
    payload = json.loads(redacted)
    new_text = payload["messages"][0]["content"]
    assert "alice\x40example.com" not in new_text
    assert "[REDACTED-EMAIL]" in new_text


def test_minimise_redacts_structured_system_prompt():
    from workspaces.lock.egress_proxy import redact_body_in_place

    body = json.dumps({
        "system": [{"type": "text", "text": "Contact alice\x40example.com"}],
        "messages": [{"role": "user", "content": "Summarise this."}],
    }).encode()
    payload = json.loads(redact_body_in_place(body, "api.anthropic.com"))
    system_text = payload["system"][0]["text"]
    assert "alice\x40example.com" not in system_text
    assert "[REDACTED-EMAIL]" in system_text


def test_proxy_blocks_pii_in_structured_system_even_with_clean_message():
    port = _free_port()
    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
    )
    proxy.start()
    try:
        body = json.dumps({
            "system": [{"type": "text", "text": "SSN 123-45-6789"}],
            "messages": [{"role": "user", "content": "hello"}],
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=body,
            headers={"X-Lock-Upstream": "api.anthropic.com"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=2)
        assert exc.value.code == 403
    finally:
        proxy.stop()


def test_proxy_interactive_callback_persist_marker_remembers_decision(tmp_path, monkeypatch):
    """When the interactive callback returns ['scope:always'], the proxy persists the decision."""
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    from workspaces.lock.tier_c import reset_backend_cache
    from workspaces.lock import DecisionsStore
    reset_backend_cache()

    decisions_path = tmp_path / "decisions.jsonl"

    def persist_callback(pending):
        return ApprovalDecision(
            action="allow",
            reason="user approved at proxy (remember always)",
            waived_findings=["scope:always"],
        )

    port = _free_port()
    proxy = EgressProxy(
        port=port,
        oversight=OversightLevel.APPROVE,   # triggers ask_user → callback
        approval_callback=persist_callback,
        decisions_path=str(decisions_path),
        upstream_overrides={"stub.test": "http://127.0.0.1:1"},
    )
    proxy.start()
    candidate = "contact bob\x40example.com"
    try:
        body = json.dumps({"messages": [{"role": "user", "content": candidate}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            data=body,
            headers={"X-Lock-Upstream": "stub.test"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=1)
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass
    finally:
        proxy.stop()

    # The proxy must have persisted the user's decision.
    after = DecisionsStore(decisions_path)
    assert after.recall(candidate) == "allow"


# ── D3: broadened extraction + fail-closed on unverifiable bodies ───────────
def test_extract_pulls_tool_result_and_tool_use():
    """PII riding in tool_result / tool_use / text-document blocks is now
    extracted (was the bypass that forwarded the body unscanned)."""
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "tool_result", "content": "patient alice\x40example.com"},
        {"type": "tool_result", "content": [{"type": "text", "text": "iban DE89 3704 0044 0532 0130 00"}]},
        {"type": "tool_use", "input": {"q": "ssn 123-45-6789"}},
        {"type": "document", "source": {"media_type": "text/plain", "data": "call +44 7700 900123"}},
    ]}]}).encode()
    text = extract_prompt_text("api.anthropic.com", body)
    assert "alice\x40example.com" in text
    assert "DE89" in text and "123-45-6789" in text and "900123" in text


def test_extract_image_only_yields_no_text():
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgoAAAANS"}}
    ]}]}).encode()
    assert extract_prompt_text("api.anthropic.com", body).strip() == ""


def test_proxy_fails_closed_on_unverifiable_body():
    """A non-empty body that yields no scannable text (image-only) must be
    REFUSED, not forwarded unscanned (D3 — was fail-OPEN)."""
    port = _free_port()
    proxy = EgressProxy(port=port, oversight=OversightLevel.AUTONOMOUS,
                        approval_callback=autonomous_callback)
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgoAAAANS"}}
        ]}]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/messages", data=body,
                                     headers={"X-Lock-Upstream": "api.anthropic.com",
                                              "Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=2)
        assert ei.value.code == 403
    finally:
        proxy.stop()


def test_proxy_scans_pii_hidden_in_tool_result():
    """An email inside a tool_result block is now scanned → refused at
    AUTONOMOUS (before D3 it extracted '' → allow → forwarded unscanned)."""
    port = _free_port()
    proxy = EgressProxy(port=port, oversight=OversightLevel.AUTONOMOUS,
                        approval_callback=autonomous_callback)
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": [
            {"type": "tool_result", "content": "contact alice\x40example.com"}
        ]}]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/messages", data=body,
                                     headers={"X-Lock-Upstream": "api.anthropic.com",
                                              "Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=2)
        assert ei.value.code == 403
    finally:
        proxy.stop()


# ── D3 panel fixes: nested tool_use + minimise-path redaction parity ─────────
def test_extract_nested_tool_use_input():
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "tool_use", "input": {"params": {"email": "alice\x40example.com"}, "n": 3}}
    ]}]}).encode()
    assert "alice\x40example.com" in extract_prompt_text("api.anthropic.com", body)


def test_minimise_redacts_new_block_shapes():
    """The minimise path must redact the same shapes extraction scans — else PII
    the scan saw is forwarded unredacted (the D3-panel blocker)."""
    from workspaces.lock.egress_proxy import redact_body_in_place
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "tool_result", "content": "patient alice\x40example.com"},
        {"type": "tool_use", "input": {"deep": {"mail": "bob\x40example.com"}}},
        {"type": "document", "source": {"media_type": "text/plain", "data": "carol\x40example.com"}},
    ]}]}).encode()
    out = redact_body_in_place(body, "api.anthropic.com").decode()
    assert "alice\x40example.com" not in out
    assert "bob\x40example.com" not in out
    assert "carol\x40example.com" not in out
    assert "REDACTED" in out
    assert json.loads(out)  # still valid JSON, structure preserved


def test_numeric_tool_args_are_not_scannable_text():
    """Numbers are data, not NL text — they must not be extracted (else they'd
    leak unredacted on minimise AND defeat fail-closed). String ids still scan."""
    body = json.dumps({"messages": [{"role": "user", "content": [
        {"type": "tool_use", "input": {"id": 123456789, "ratio": 3.14}}
    ]}]}).encode()
    assert extract_prompt_text("api.anthropic.com", body).strip() == ""


def test_fail_closed_not_defeated_by_numeric_tool_arg():
    """Image + numeric-only tool_use → still refused (number isn't scannable)."""
    port = _free_port()
    proxy = EgressProxy(port=port, oversight=OversightLevel.AUTONOMOUS,
                        approval_callback=autonomous_callback)
    proxy.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR"}},
            {"type": "tool_use", "input": {"id": 12345}},
        ]}]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/messages", data=body,
                                     headers={"X-Lock-Upstream": "api.anthropic.com",
                                              "Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=2)
        assert ei.value.code == 403
    finally:
        proxy.stop()


# ── D4: credential is bound to its upstream (no cross-provider key leak) ──────

def test_credential_binding_matched_is_ok():
    # the provider's own credential header for its host → no violation
    assert _credential_binding_violation({"x-api-key": "sk-ant"}, "api.anthropic.com") is None
    assert _credential_binding_violation({"Authorization": "Bearer sk"}, "api.openai.com") is None
    assert _credential_binding_violation({"x-goog-api-key": "k"},
                                         "generativelanguage.googleapis.com") is None


def test_credential_binding_crossprovider_is_violation():
    # an Anthropic key (x-api-key) aimed at OpenAI → blocked
    assert _credential_binding_violation({"x-api-key": "sk-ant"}, "api.openai.com") == "x-api-key"
    # an OpenAI-style bearer aimed at Anthropic → blocked
    assert _credential_binding_violation({"Authorization": "Bearer sk"},
                                         "api.anthropic.com") == "authorization"


def test_credential_binding_no_credential_is_ok():
    assert _credential_binding_violation({"Content-Type": "application/json"},
                                         "api.anthropic.com") is None


def test_credential_binding_unknown_upstream_fails_closed():
    # an upstream with no declared credential set accepts no credential header
    assert _credential_binding_violation({"x-api-key": "k"}, "evil.example.com") == "x-api-key"


# ---------------------------------------------------------------------------
# Per-request agent identity — a shared proxy attributes each request to the
# agent that made it (Web Bot Auth Signature-Agent / the X-Lock-Agent alias),
# replacing one process-wide RVND_AGENT. Safe by construction: the actor flows
# only into escalate-only policy composition (can only tighten) and attribution,
# never into a durable clearance (those carry the operator's identity).
# ---------------------------------------------------------------------------


def _hdrs(**pairs) -> Message:
    """A case-insensitive header object like BaseHTTPRequestHandler.headers.
    Underscores map to hyphens (Signature_Agent -> 'Signature-Agent')."""
    m = Message()
    for k, v in pairs.items():
        m[k.replace("_", "-")] = v
    return m


def test_agent_identity_prefers_signature_agent_header(monkeypatch):
    monkeypatch.setenv("RVND_AGENT", "env-agent")
    # Web Bot Auth Signature-Agent wins over the alias and the env default.
    assert request_agent_identity(
        _hdrs(Signature_Agent="agent-alpha", X_Lock_Agent="beta")) == "agent-alpha"


def test_agent_identity_alias_then_env_then_default(monkeypatch):
    monkeypatch.delenv("RVND_AGENT", raising=False)
    assert request_agent_identity(_hdrs(X_Lock_Agent="beta")) == "beta"
    monkeypatch.setenv("RVND_AGENT", "env-agent")
    assert request_agent_identity(_hdrs()) == "env-agent"      # process default
    monkeypatch.delenv("RVND_AGENT", raising=False)
    assert request_agent_identity(_hdrs()) == "agent"          # generic fallback


def test_agent_identity_header_beats_env_is_per_request(monkeypatch):
    """The core of the gap: two requests in ONE process (one RVND_AGENT) are
    attributed to DIFFERENT agents by their per-request header."""
    monkeypatch.setenv("RVND_AGENT", "one-and-only")
    assert request_agent_identity(_hdrs(Signature_Agent="alpha")) == "alpha"
    assert request_agent_identity(_hdrs(Signature_Agent="beta")) == "beta"


def test_agent_identity_header_lookup_is_case_insensitive(monkeypatch):
    monkeypatch.delenv("RVND_AGENT", raising=False)
    assert request_agent_identity(_hdrs(**{"signature-agent": "lower"})) == "lower"


def test_agent_identity_sanitises_quotes_control_chars_and_length():
    assert _sanitise_agent_id('"https://bot.example"') == "https://bot.example"
    assert _sanitise_agent_id("a\x00b\x07") == "ab"        # control chars stripped
    assert _sanitise_agent_id("a   b") == "a b"            # whitespace collapsed
    assert len(_sanitise_agent_id("x" * 400)) == 128       # length capped


def test_agent_identity_never_raises(monkeypatch):
    monkeypatch.setenv("RVND_AGENT", "fallback")

    class Boom:
        def get(self, _name):
            raise RuntimeError("header store broke")

    assert request_agent_identity(Boom()) == "fallback"
    assert request_agent_identity(None) == "fallback"


def test_proxy_attributes_agent_identity_per_request(monkeypatch):
    """End-to-end: a shared proxy attributes each request to its declared agent,
    and the RVND-internal identity header never leaks to the upstream."""
    seen_actors: list = []

    def spy_gate(text, **kw):
        seen_actors.append(kw.get("actor"))
        return GateDecision(action="allow", reason="test", source="cloud_llm_request")

    monkeypatch.setattr("workspaces.lock.egress_proxy.gate_prompt", spy_gate)

    upstream_port = _free_port()
    proxy_port = _free_port()
    upstream_header_sets: list = []

    class StubHandler(BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            upstream_header_sets.append({k.lower() for k in self.headers.keys()})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

    upstream = ThreadingHTTPServer(("127.0.0.1", upstream_port), StubHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    proxy = EgressProxy(
        port=proxy_port,
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
        upstream_overrides={"stub.test": f"http://127.0.0.1:{upstream_port}"},
    )
    proxy.start()
    try:
        for who, header in (("agent-alpha", "Signature-Agent"),
                            ("agent-beta", "X-Lock-Agent")):
            body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{proxy_port}/v1/messages",
                data=body,
                headers={"X-Lock-Upstream": "stub.test",
                         "Content-Type": "application/json",
                         header: who},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3).read()
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()

    # per-request attribution — one process, two agents, distinguished by header
    assert seen_actors == ["agent-alpha", "agent-beta"], seen_actors
    # the RVND-internal identity headers are stripped before the upstream forward
    for received in upstream_header_sets:
        assert "signature-agent" not in received
        assert "x-lock-agent" not in received


# ---------------------------------------------------------------------------
# P3 — verified agent identity wired into the proxy. A signed request upgrades
# the DECLARED identity to VERIFIED (recorded in the audit); an unsigned or
# bad-signature request falls back to declared and is NOT rejected here (the
# escalate-only gate is unchanged). Exercises the real host_deps-injected
# verifier (agent_keys registry + web_bot_auth).
# ---------------------------------------------------------------------------


def _register_and_sign(monkeypatch, tmp_path, *, agent="crawler-x",
                       authority="localhost:8443", created=None):
    """Register an agent key (in a temp registry) and return an email headers
    object carrying Host + a valid Web Bot Auth signature over @authority."""
    monkeypatch.setenv("WORKSPACE_AGENTS_DIR", str(tmp_path))
    priv = Ed25519PrivateKey.generate()
    pem = priv.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    keyid = _agent_keys.register_agent_key(agent, pem)["keyid"]
    now = int(time.time()) if created is None else created
    ctx = _wba.RequestContext(authority=authority,
                              headers={"signature-agent": f'"{agent}"'})
    hdrs = _wba.sign(priv, agent=agent, keyid=keyid,
                     covered=["@authority", "signature-agent"], ctx=ctx, created=now)
    msg = Message()
    msg["Host"] = authority
    for k, v in hdrs.items():
        msg[k] = v
    return msg, keyid, now


def test_resolve_identity_verified_for_signed_request(monkeypatch, tmp_path):
    msg, keyid, now = _register_and_sign(monkeypatch, tmp_path)
    ident = resolve_agent_identity(msg, authority=msg.get("Host", ""),
                                   method="POST", path="/v1/messages", now=now)
    assert ident.verified is True
    assert ident.actor == "crawler-x" and ident.keyid == keyid


def test_resolve_identity_declared_when_unsigned(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_AGENTS_DIR", str(tmp_path))
    msg = Message()
    msg["Signature-Agent"] = '"solo-agent"'
    ident = resolve_agent_identity(msg)
    assert ident.verified is False and ident.actor == "solo-agent"
    assert "no signature" in ident.reason


def test_resolve_identity_bad_signature_falls_back_to_declared(monkeypatch, tmp_path):
    # signed over authority "localhost:8443" but verified against a different one
    msg, _, now = _register_and_sign(monkeypatch, tmp_path, authority="localhost:8443")
    ident = resolve_agent_identity(msg, authority="evil.example",
                                   method="POST", path="/v1/messages", now=now)
    assert ident.verified is False          # signature does not verify
    assert ident.actor == "crawler-x"       # but the claim is still attributed
    assert "declared" in ident.reason       # and the request is NOT rejected here


def test_proxy_audits_verified_identity_end_to_end(monkeypatch, tmp_path):
    """A signed request through the live proxy is recorded verified in the audit
    and still forwarded — P3 changes attribution, not the verdict."""
    upstream_port = _free_port()
    proxy_port = _free_port()
    authority = f"127.0.0.1:{proxy_port}"           # what the agent signs @authority over
    msg, keyid, _ = _register_and_sign(monkeypatch, tmp_path, authority=authority)

    upstream_hits = []

    class StubHandler(BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            upstream_hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

    upstream = ThreadingHTTPServer(("127.0.0.1", upstream_port), StubHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    proxy = EgressProxy(
        port=proxy_port,
        oversight=OversightLevel.AUTONOMOUS,
        approval_callback=autonomous_callback,
        upstream_overrides={"stub.test": f"http://127.0.0.1:{upstream_port}"},
    )
    events = []
    monkeypatch.setattr(proxy, "audit_log", lambda ev: events.append(ev))
    proxy.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/messages",
            data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode(),
            headers={"X-Lock-Upstream": "stub.test",
                     "Content-Type": "application/json",
                     "Signature-Agent": msg["Signature-Agent"],
                     "Signature-Input": msg["Signature-Input"],
                     "Signature": msg["Signature"]},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3).read()
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()

    identity_events = [e for e in events if e.get("kind") == "agent_identity"]
    assert identity_events, "a signed request must emit an agent_identity audit event"
    ev = identity_events[0]
    assert ev["verified"] is True and ev["keyid"] == keyid and ev["agent"] == "crawler-x"
    assert upstream_hits == ["/v1/messages"], "verdict unchanged — still forwarded"
