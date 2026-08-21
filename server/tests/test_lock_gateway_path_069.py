# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Facade-path regression tests for workspace_lock egress/ingress (panel G0, 2026-06-04).

The bug under test: ``rvnd.mcp_server.lock_egress_check`` re-assembled the
``rvnd.lock.egress()`` call locally and drifted from the real signature —
it passed ``capability_token`` as a kwarg to ``egress()`` (which takes it on
the ``ToolCall``), so EVERY call through the workspaces facade raised TypeError.
The stdio surface most exercised in tests was ``rvnd.lock.mcp_server``
directly, so the drift was invisible until the op was called through
``workspace_lock`` — the exact path the gateway will use.

Fix: delegation to the reference implementation. These tests pin the
contract through the facade, including JSON-serialisability (the old
ingress copy leaked dataclass findings via ``__dict__``).
"""

from __future__ import annotations

import json


from rvnd.mcp_server import seal_binding, lock_egress_check


PII_TEXT = "Contact Maria Schneider, maria.schneider\x40example.de, +49 170 1234567."


def _assert_json_safe(result: dict) -> None:
    json.dumps(result)  # raises TypeError if any dataclass/object leaked


# ---------------------------------------------------------------------------
# egress_check through the facade (the call that crashed)
# ---------------------------------------------------------------------------


def test_facade_egress_check_does_not_crash_and_returns_contract():
    result = seal_binding("egress_check", {
        "tool": "slack.post",
        "arguments": {"channel": "#legal", "text": PII_TEXT},
        "task_scope": ["channel", "text"],
    })
    assert "error" not in result, result
    assert result["action"] in ("allow", "strip", "refuse")
    assert isinstance(result["findings"], list)
    _assert_json_safe(result)


def test_facade_egress_check_accepts_capability_token():
    """The exact kwarg that crashed: capability_token through the facade."""
    result = seal_binding("egress_check", {
        "tool": "slack.post",
        "arguments": {"channel": "#legal", "text": "hello"},
        "task_scope": ["channel", "text"],
        "capability_token": {
            "task_id": "t-1",
            "allowed_tools": ["slack.post"],
            "allowed_fields": ["channel", "text"],
        },
    })
    assert "error" not in result, result
    assert result["action"] in ("allow", "strip", "refuse")
    _assert_json_safe(result)


def test_facade_egress_check_malformed_token_degrades_not_crashes():
    result = seal_binding("egress_check", {
        "tool": "slack.post",
        "arguments": {"channel": "#legal", "text": "hello"},
        "task_scope": ["channel", "text"],
        "capability_token": {"not": "a token"},
    })
    assert "error" not in result, result
    assert result["action"] in ("allow", "strip", "refuse")


def test_facade_egress_check_detects_pii_in_arguments():
    result = seal_binding("egress_check", {
        "tool": "slack.post",
        "arguments": {"channel": "#legal", "text": PII_TEXT},
        "task_scope": ["channel", "text"],
    })
    types = {f["type"] for f in result["findings"]}
    assert "pii_in_argument" in types


def test_facade_egress_check_strips_over_collection():
    result = seal_binding("egress_check", {
        "tool": "hr.notify",
        "arguments": {"name": "M. Schneider", "salary": "45k", "shoe_size": "39"},
        "task_scope": ["name"],
    })
    assert result["action"] in ("strip", "refuse")
    if result["action"] == "strip":
        assert "modified_call" in result
        _assert_json_safe(result)


def test_direct_wrapper_matches_reference_implementation():
    """Delegation invariant: workspaces wrapper == workspaces.lock reference, same inputs."""
    from rvnd.lock.mcp_server import egress_check as reference
    kwargs = dict(
        tool="slack.post",
        arguments={"channel": "#legal", "text": PII_TEXT},
        task_scope=["channel", "text"],
    )
    ours = lock_egress_check(**kwargs)
    theirs = reference(**kwargs)
    assert ours["action"] == theirs["action"]
    assert [f["type"] for f in ours["findings"]] == [f["type"] for f in theirs["findings"]]


# ---------------------------------------------------------------------------
# ingress_check through the facade (JSON-safety regression)
# ---------------------------------------------------------------------------


def test_facade_ingress_check_contract_and_json_safe():
    result = seal_binding("ingress_check", {
        "payload": {"summary": PII_TEXT, "ticket": "ABC-1"},
        "task_scope": ["summary", "ticket"],
    })
    assert "error" not in result, result
    assert result["action"] in ("allow", "redact")
    assert isinstance(result["findings"], list)
    _assert_json_safe(result)


def test_facade_ingress_check_accepts_task_id():
    result = seal_binding("ingress_check", {
        "payload": {"summary": "clean text"},
        "task_scope": ["summary"],
        "task_id": "t-42",
    })
    assert "error" not in result, result
    _assert_json_safe(result)


# ---------------------------------------------------------------------------
# Facade hygiene: unknown op and missing params stay graceful
# ---------------------------------------------------------------------------


def test_facade_unknown_op_returns_error_dict():
    result = seal_binding("egress_chekc")
    assert "error" in result


def test_facade_missing_param_returns_error_dict():
    result = seal_binding("egress_check", {"tool": "slack.post"})
    assert "error" in result


def test_ingress_check_accepts_string_payload_g4():
    """G4: workflow engines hand the gate plain text (webhook/ticket body).
    A bare string must classify, not crash on payload.keys()."""
    from rvnd import gateway as gw
    out = gw.workspace_lock("ingress_check", {
        "task_scope": ["ticket-triage"],
        "payload": "mail maria.schneider\x40example.de +49 170 1234567 now"})
    assert "error" not in out, out
    assert out["action"] in ("allow", "redact")
    assert isinstance(out.get("findings"), list)
    # PII in a string payload must be detected, not silently allowed
    assert out["findings"], "expected PII findings on email+phone payload"
