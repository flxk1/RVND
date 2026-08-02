# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace Gateway tests — profile surface, op filtering, auth, receipts.

Pins exactly 7 tools, the trifecta invariant (no private-data readers),
op-level allowlists (every profile entry explicit, every op present
upstream), per-host tokens with refuse-to-start-without-token, and an audit
receipt in every enveloped response.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from workspaces import gateway as gw
from workspaces.gateway import (
    ALLOWED_OPS,
    FORBIDDEN_TOOLS,
    BearerAuthMiddleware,
    build_app,
    check_auth,
    parse_tokens,
)


PII_TEXT = "Contact Maria Schneider, maria.schneider\x40example.de, +49 170 1234567."


def _profile_tools() -> set[str]:
    tools = asyncio.run(gw.gateway.list_tools())
    return {t.name for t in tools}


# ---------------------------------------------------------------------------
# Profile surface
# ---------------------------------------------------------------------------


def test_profile_is_exactly_seven_tools():
    # reason was removed from the egress surface (N1): it reads raw private pairs
    # and mutates the signed log. Down from 8 to 7.
    assert _profile_tools() == {
        "workspace_lock", "workspace_contract", "workspace_grounder", "workspace_audit",
        "workspace_model", "workspace_policy", "server_info",
    }


def test_trifecta_invariant_no_forbidden_tools():
    assert _profile_tools().isdisjoint(FORBIDDEN_TOOLS)


def test_reason_is_forbidden_and_off_the_profile():
    # Positive trifecta guard: reason must be in FORBIDDEN_TOOLS *and* absent
    # from the profile. Because it is now in FORBIDDEN, the disjointness test
    # above would FAIL loudly if reason were ever re-added to the gateway — the
    # gap the audit flagged (in-profile-but-not-forbidden, so disjointness
    # couldn't catch it) is closed.
    assert "reason" in FORBIDDEN_TOOLS
    assert "reason" not in _profile_tools()
    # the gateway module no longer registers a reason tool at all
    assert not hasattr(gw, "reason")


def test_server_info_profile_excludes_reason():
    prof = set(gw.server_info()["profile"])
    assert "reason" not in prof
    assert prof.isdisjoint(FORBIDDEN_TOOLS)


def test_no_raw_pair_content_reachable_over_gateway(tmp_path, monkeypatch):
    # The trifecta, positively: ingest a pair carrying a unique secret, then
    # confirm NO gateway-reachable op echoes that raw string. reason (the one
    # tool that surfaced raw pairs) is gone; the rest are taxonomy/verdict only.
    import json as _json
    from workspaces import mcp_server
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logroot"))  # hermetic
    folder = tmp_path / "vault"
    folder.mkdir()
    secret = "ZZTOPSECRET-maria.schneider\x40example.de"

    # Store a pair carrying the secret via the LOCAL facade (correct contract:
    # subject/predicate/object — not problem/solution).
    remembered = mcp_server.workspace_memory("remember", {
        "folder_context": str(folder),
        "subject": f"data subject {secret}",
        "predicate": "has_email",
        "object": secret,
    })
    assert "error" not in remembered, remembered

    # NEGATIVE CONTROL: the secret IS retrievable over the LOCAL raw path. Without
    # this the whole test could pass vacuously (secret never stored) — this pins
    # that it is genuinely in a position to leak.
    local = mcp_server.workspace_memory("recent", {"folder_context": str(folder), "limit": 10})
    assert secret in _json.dumps(local), "secret not stored — test would be vacuous"

    # Exercise EVERY gateway facade with representative folder-scoped read ops.
    # None may echo the raw pair content.
    probes = [
        (gw.workspace_policy,   ["snapshot"]),
        (gw.workspace_audit,    ["verify_chain", "discipline", "get_event"]),
        (gw.workspace_lock,     ["audit_query", "threshold_get"]),
        (gw.workspace_contract, ["list_approvals", "list_reviews"]),
        (gw.workspace_grounder, ["bibliography", "coverage", "ground"]),
        (gw.workspace_model,    ["list"]),
    ]
    leaks = []
    for fn, ops in probes:
        for op in ops:
            out = fn(op, {"folder_context": str(folder)})
            if secret in _json.dumps(out, default=str):
                leaks.append((fn.__name__, op))
    assert leaks == [], f"raw pair content leaked over gateway: {leaks}"


def test_server_info_carries_version_claim_and_profile():
    info = gw.server_info()
    assert info["server_name"] == "workspace-gateway"
    assert info["gateway_version"] == gw.GATEWAY_VERSION
    assert "detective" in info["claim"]
    assert info["upstream"]["server_name"] == "workspaces"
    json.dumps(info)


# ---------------------------------------------------------------------------
# Op-level allowlists
# ---------------------------------------------------------------------------


def test_state_changing_lock_ops_blocked():
    for op in ("seal", "unseal", "threshold_set", "reclassify"):
        out = gw.workspace_lock(op, {"folder_context": "/tmp/x"})
        assert "error" in out and "not available over the gateway" in out["error"]
        assert op not in out["valid_ops"]


def test_policy_writes_blocked_snapshot_allowed():
    blocked = gw.workspace_policy("disable", {"folder_context": "/tmp/x", "dial": "lock"})
    assert "error" in blocked and blocked["valid_ops"] == ["snapshot"]


def test_help_is_filtered_to_allowed_ops():
    out = gw.workspace_lock("help")
    ops = {o["op"] for o in out["ops"]}
    assert ops == ALLOWED_OPS["workspace_lock"]


def test_grounder_erasure_and_ingest_stay_local():
    for op in ("subject.forget", "source.ingest", "claim.check",
               "creators.classify", "oversight.feed"):
        out = gw.workspace_grounder(op, {"folder_context": "/tmp/x"})
        assert "error" in out and "not available over the gateway" in out["error"]


def test_profile_ops_are_explicit_and_exist_upstream():
    from workspaces import mcp_server
    for tool, allowed in ALLOWED_OPS.items():
        assert allowed, f"{tool}: the profile must name its ops explicitly"
        catalogue = {o.get("op") for o in getattr(mcp_server, tool)("help")["ops"]}
        missing = set(allowed) - catalogue
        assert not missing, (f"{tool}: profile ops missing upstream"
                             f" (renamed or removed?): {sorted(missing)}")


def test_allowed_lock_op_delegates_and_works():
    out = gw.workspace_lock("classify", {"text": PII_TEXT})
    assert out["ok"] is True
    assert out["findings_count"] >= 2
    json.dumps(out)


def test_egress_check_works_through_gateway():
    out = gw.workspace_lock("egress_check", {
        "tool": "slack.post",
        "arguments": {"channel": "#legal", "text": PII_TEXT},
        "task_scope": ["channel", "text"],
    })
    assert out["action"] in ("allow", "strip", "refuse")
    json.dumps(out)


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


def test_envelope_attaches_gateway_meta_without_folder():
    out = gw.workspace_lock("classify", {"text": "clean text"})
    meta = out["gateway_meta"]
    assert meta["gateway_version"] == gw.GATEWAY_VERSION
    assert meta["host_label"] == "local"
    assert meta["audit"] == "skipped"


def test_receipt_recorded_with_folder_context(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKSPACE_FOLDER_CONTEXT", raising=False)
    folder = tmp_path / "flow"
    folder.mkdir()
    out = gw.workspace_contract("request_approval", {
        "folder_context": str(folder),
        "contract_id": "n8n-test-flow",
        "signers": ["compliance-lead"],
        "requested_by": "gateway:test",
    })
    assert out["ok"] is True
    meta = out["gateway_meta"]
    assert meta["audit"] == "recorded"
    assert meta["audit_event_id"]
    # the receipt's event is in the same chain verify_chain validates
    chain = gw.workspace_audit("verify_chain", {"folder_context": str(folder)})
    assert chain["ok"] is True
    assert chain["broken_links"] == []


def test_oversight_roundtrip_through_gateway(tmp_path, monkeypatch):
    # Hermetic log root. Without this, approvals land in ~/.workspace/log keyed
    # by the folder's PATH STRING — and pytest recycles its numbered
    # basetemp dirs (keeps 3), so the 4th suite run on the same machine
    # resurrects a ghost approval from a prior run and counts 2
    # (observed on the Mac, 2026-06-12; first runs passed).
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logroot"))
    folder = tmp_path / "flow2"
    folder.mkdir()
    req = gw.workspace_contract("request_approval", {
        "folder_context": str(folder), "contract_id": "c-1",
        "signers": ["lead"],
    })
    rec = gw.workspace_contract("record_approval", {
        "folder_context": str(folder), "approval_id": req["approval_id"],
        "signer": "lead", "decision": "approved",
    })
    assert rec["overall_state"] == "approved"
    lst = gw.workspace_contract("list_approvals", {"folder_context": str(folder)})
    assert lst["count"] == 1


# ---------------------------------------------------------------------------
# Auth: tokens, header check, middleware, refuse-to-start
# ---------------------------------------------------------------------------


def test_parse_tokens_per_host_and_single():
    table = parse_tokens({
        "WORKSPACES_GATEWAY_TOKENS": "n8n:secret-a, langdock:secret-b",
        "WORKSPACES_GATEWAY_TOKEN": "secret-c",
    })
    assert table == {"secret-a": "n8n", "secret-b": "langdock",
                     "secret-c": "default"}


def test_parse_tokens_ignores_malformed_entries():
    table = parse_tokens({"WORKSPACES_GATEWAY_TOKENS": "nocolon,:empty,ok:tok"})
    assert table == {"tok": "ok"}


def test_check_auth():
    table = {"tok-1": "n8n"}
    assert check_auth("Bearer tok-1", table) == "n8n"
    assert check_auth("Bearer wrong", table) is None
    assert check_auth("Basic tok-1", table) is None
    assert check_auth(None, table) is None


def test_build_app_refuses_without_token(monkeypatch):
    monkeypatch.delenv("WORKSPACES_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("WORKSPACES_GATEWAY_TOKENS", raising=False)
    with pytest.raises(RuntimeError, match="refusing to start"):
        build_app()


def test_middleware_rejects_and_admits():
    seen: dict[str, str] = {}

    async def inner(scope, receive, send):
        seen["label"] = gw._CURRENT_LABEL.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = BearerAuthMiddleware(inner, {"tok-1": "n8n"})

    async def run(headers):
        sent = []

        async def send(msg):
            sent.append(msg)

        await mw({"type": "http", "headers": headers}, None, send)
        return sent

    rejected = asyncio.run(run([]))
    assert rejected[0]["status"] == 401

    admitted = asyncio.run(run([(b"authorization", b"Bearer tok-1")]))
    assert admitted[0]["status"] == 200
    assert seen["label"] == "n8n"
    # label is request-scoped: back to default outside the request
    assert gw._CURRENT_LABEL.get() == "local"
