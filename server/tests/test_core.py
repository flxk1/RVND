# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Unit tests for agent-tool-lock core middleware."""

from __future__ import annotations

import json
import time
from pathlib import Path


from rvnd.lock import (
    AuditLog,
    CapabilityToken,
    Mode,
    ToolCall,
    ToolResponse,
    egress,
    ingress,
)
from rvnd.lock.core import (
    _flatten_argument_fields,
    tier_a_check_arguments,
    tier_a_check_response,
    tier_b_scan_dict,
    tier_b_scan_text,
    validate_token,
)


# ---------------------------------------------------------------------------
# Tier A
# ---------------------------------------------------------------------------


def test_tier_a_argument_fields_flattening_simple():
    args = {"employee_id": "E-1", "include_salary": True}
    flat = _flatten_argument_fields(args)
    assert flat == {"employee_id", "include_salary"}


def test_tier_a_argument_fields_flattening_nested():
    args = {"employee_id": "E-1", "options": {"include_history": True, "include_voice": False}}
    flat = _flatten_argument_fields(args)
    assert "options" in flat
    assert "options.include_history" in flat
    assert "options.include_voice" in flat


def test_tier_a_check_arguments_no_over_collection():
    findings = tier_a_check_arguments(
        {"employee_id": "E-1"},
        task_scope={"employee_id", "name", "role"},
    )
    assert findings == []


def test_tier_a_check_arguments_flags_over_collection():
    findings = tier_a_check_arguments(
        {"employee_id": "E-1", "include_salary_band": True, "include_voiceprint": True},
        task_scope={"employee_id"},
    )
    flagged = {f.field for f in findings}
    assert "include_salary_band" in flagged
    assert "include_voiceprint" in flagged
    assert "employee_id" not in flagged


def test_tier_a_check_response_flags_over_returns():
    findings = tier_a_check_response(
        {"name": "Maria", "role": "Engineer", "salary_band": "L4", "voiceprint_id": "vp-9"},
        task_scope={"name", "role"},
    )
    flagged = {f.field for f in findings}
    assert flagged == {"salary_band", "voiceprint_id"}


# ---------------------------------------------------------------------------
# Tier B
# ---------------------------------------------------------------------------


def test_tier_b_regex_email():
    findings = tier_b_scan_text("contact alice\x40example.com for details")
    types = {f.detail for f in findings}
    assert any("email" in d for d in types)


def test_tier_b_regex_iban():
    findings = tier_b_scan_text("DE89370400440532013000 is the account")
    types = {f.detail for f in findings}
    assert any("iban" in d for d in types)


def test_tier_b_regex_national_id_us_ssn_pattern():
    findings = tier_b_scan_text("SSN 123-45-6789")
    types = {f.detail for f in findings}
    # Accept both the legacy "national_id" label and the new "us_ssn" label
    # so the test stays green across the Tier B regex coverage upgrade.
    assert any(("national_id" in d) or ("us_ssn" in d) for d in types), \
        f"expected SSN finding, got: {types}"


def test_tier_b_scan_dict_finds_pii_in_nested():
    findings = tier_b_scan_dict({
        "note": "ping alice\x40example.com",
        "child": {"description": "DE89370400440532013000 account"},
    })
    fields = {f.field for f in findings}
    assert "note" in fields
    assert "description" in fields


def test_tier_b_misses_unstructured_pii_as_documented():
    # Smoke test: Tier B does NOT detect free-text names. That's Tier C's job.
    # If a future change makes Tier B catch this, the test should be updated AND
    # Tier C semantics rethought.
    findings = tier_b_scan_text("Maria Schmidt approved the request")
    assert all("email" in f.detail or "phone" in f.detail or "iban" in f.detail or "national_id" in f.detail
               or False for f in findings)
    # We accept this as a false-negative-by-design; Tier C is the catch.


# ---------------------------------------------------------------------------
# Capability tokens
# ---------------------------------------------------------------------------


def _make_token(*, exp_offset: int = 60, aud: str = "hr.get_employee") -> CapabilityToken:
    now = int(time.time())
    return CapabilityToken(
        iss="identity.example",
        sub="agent:test",
        aud=aud,
        iat=now,
        exp=now + exp_offset,
        scope={
            "regions": ["EU-WEST"],
            "identifier_classes": ["employee_id"],
            "retention_class": "task-bound",
            "fields": ["name", "role"],
            "purpose": "scheduling",
        },
        controller="deployer.example",
        task_id="task-test-001",
    )


def test_capability_token_valid():
    token = _make_token()
    call = ToolCall(tool="hr.get_employee", arguments={"employee_id": "E-1"}, capability_token=token)
    result = validate_token(token, call)
    assert result.valid is True


def test_capability_token_expired():
    token = _make_token(exp_offset=-10)
    call = ToolCall(tool="hr.get_employee", arguments={"employee_id": "E-1"}, capability_token=token)
    result = validate_token(token, call)
    assert result.valid is False
    assert any("expired" in f.detail for f in result.findings)


def test_capability_token_audience_mismatch():
    token = _make_token(aud="some.other.tool")
    call = ToolCall(tool="hr.get_employee", arguments={"employee_id": "E-1"}, capability_token=token)
    result = validate_token(token, call)
    assert result.valid is False
    assert any("audience" in f.detail for f in result.findings)


def test_capability_token_missing():
    call = ToolCall(tool="hr.get_employee", arguments={"employee_id": "E-1"}, capability_token=None)
    result = validate_token(None, call)
    assert result.valid is False
    assert any("no capability token" in f.detail for f in result.findings)


# ---------------------------------------------------------------------------
# Egress middleware
# ---------------------------------------------------------------------------


def test_egress_standard_strips_over_collection():
    call = ToolCall(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1", "include_salary_band": True},
        capability_token=_make_token(),
    )
    decision = egress(call, task_scope={"employee_id"}, mode=Mode.STANDARD)
    assert decision.action == "strip"
    assert "include_salary_band" in decision.stripped_fields
    assert decision.modified_call is not None
    assert "include_salary_band" not in decision.modified_call.arguments


def test_egress_strict_refuses_over_collection():
    call = ToolCall(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1", "include_salary_band": True},
        capability_token=_make_token(),
    )
    decision = egress(call, task_scope={"employee_id"}, mode=Mode.STRICT)
    assert decision.action == "refuse"


def test_egress_permissive_allows_with_warnings():
    call = ToolCall(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1", "include_salary_band": True},
        capability_token=_make_token(),
    )
    decision = egress(call, task_scope={"employee_id"}, mode=Mode.PERMISSIVE)
    assert decision.action == "allow"
    assert any(f.type == "over_collection" for f in decision.findings)


def test_egress_audit_only_never_blocks():
    call = ToolCall(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1", "include_salary_band": True},
        capability_token=_make_token(),
    )
    decision = egress(call, task_scope={"employee_id"}, mode=Mode.AUDIT_ONLY)
    assert decision.action == "allow"


def test_egress_allows_clean_call():
    call = ToolCall(
        tool="hr.get_employee",
        arguments={"employee_id": "E-1"},
        capability_token=_make_token(),
    )
    decision = egress(call, task_scope={"employee_id"}, mode=Mode.STANDARD)
    assert decision.action == "allow"


def test_egress_flags_pii_in_argument_via_tier_b():
    call = ToolCall(
        tool="some.tool",
        arguments={"query": "find records for alice\x40example.com please"},
        capability_token=_make_token(aud="some.tool"),
    )
    decision = egress(call, task_scope={"query"}, mode=Mode.PERMISSIVE)
    assert any(f.tier == "B" for f in decision.findings)


# ---------------------------------------------------------------------------
# Ingress middleware
# ---------------------------------------------------------------------------


def test_ingress_redacts_unrequested_fields_in_standard_mode():
    response = ToolResponse(payload={"name": "Maria", "role": "Engineer", "salary_band": "L4"})
    decision = ingress(response, task_scope={"name", "role"}, mode=Mode.STANDARD)
    assert decision.action == "redact"
    assert decision.redacted_payload["salary_band"] == "[REDACTED]"
    assert decision.redacted_payload["name"] == "Maria"


def test_ingress_allows_clean_response():
    response = ToolResponse(payload={"name": "Maria", "role": "Engineer"})
    decision = ingress(response, task_scope={"name", "role"}, mode=Mode.STANDARD)
    assert decision.action == "allow"


def test_ingress_redacts_pii_via_tier_b():
    response = ToolResponse(payload={"name": "Maria", "note": "email: alice\x40example.com"})
    decision = ingress(response, task_scope={"name", "note"}, mode=Mode.STANDARD)
    # Tier B flags 'note' high-severity → redacted
    assert decision.action == "redact"
    assert decision.redacted_payload["note"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_audit_log_records_schema_not_values(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLog(audit_path)
    call = ToolCall(
        tool="hr.get_employee",
        arguments={"employee_id": "E-VERY-SENSITIVE-VALUE", "include_salary_band": True},
        capability_token=_make_token(),
    )
    decision = egress(call, task_scope={"employee_id"}, mode=Mode.STANDARD, audit=audit)
    assert decision.action == "strip"

    log_lines = audit_path.read_text().strip().splitlines()
    assert len(log_lines) == 1
    entry = json.loads(log_lines[0])
    # Schema present
    assert "employee_id" in entry["argument_schema"]
    # Raw value MUST NOT appear
    raw = audit_path.read_text()
    assert "E-VERY-SENSITIVE-VALUE" not in raw


def test_audit_log_correlates_task_id(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLog(audit_path)
    token = _make_token()
    call = ToolCall(tool="hr.get_employee", arguments={"employee_id": "E-1"}, capability_token=token)
    egress(call, task_scope={"employee_id"}, mode=Mode.STANDARD, audit=audit)

    entry = json.loads(audit_path.read_text().strip())
    assert entry["task_id"] == "task-test-001"
