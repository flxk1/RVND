# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the adapter loader and its supported adapter kinds."""

from __future__ import annotations

import pytest

# adapter_loader was absorbed into the workspaces package
# (was workspace-adapter/runtime/adapter_loader.py).
from workspaces import adapter_loader  # noqa: E402


# -----------------------------------------------------------------------------
# Schema validation
# -----------------------------------------------------------------------------


def _valid_mcp_tool_dict() -> dict:
    return {
        "name": "fetch-github-issue",
        "description": "Fetch a GitHub issue.",
        "kind": "mcp_tool",
        "input_schema": {
            "type": "object",
            "required": ["owner", "repo", "issue_number"],
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "issue_number": {"type": "integer"},
            },
        },
        "audit": {"side_effects": "read-only", "contains_pii": False},
        "mcp_tool": {"tool_name": "mcp__github__get_issue"},
    }


def test_load_dict_accepts_valid_mcp_tool_adapter():
    decl = adapter_loader.load_dict(_valid_mcp_tool_dict())
    assert decl.name == "fetch-github-issue"
    assert decl.kind == "mcp_tool"
    assert decl.kind_config["tool_name"] == "mcp__github__get_issue"
    assert decl.audit["side_effects"] == "read-only"


def test_load_dict_rejects_missing_required_keys():
    bad = _valid_mcp_tool_dict()
    del bad["description"]
    with pytest.raises(adapter_loader.AdapterValidationError) as exc:
        adapter_loader.load_dict(bad)
    assert "description" in str(exc.value)


def test_load_dict_rejects_unknown_kind():
    bad = _valid_mcp_tool_dict()
    bad["kind"] = "carrier-pigeon"
    with pytest.raises(adapter_loader.AdapterValidationError) as exc:
        adapter_loader.load_dict(bad)
    assert "carrier-pigeon" in str(exc.value)


def test_load_dict_rejects_kind_without_matching_config_block():
    bad = _valid_mcp_tool_dict()
    del bad["mcp_tool"]
    with pytest.raises(adapter_loader.AdapterValidationError) as exc:
        adapter_loader.load_dict(bad)
    # Error should mention both the declared kind and the missing block.
    msg = str(exc.value)
    assert "mcp_tool" in msg


def test_load_dict_rejects_non_dict_input():
    with pytest.raises(adapter_loader.AdapterValidationError):
        adapter_loader.load_dict(["this", "is", "not", "a", "dict"])  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# mcp_tool adapter — the working implementation
# -----------------------------------------------------------------------------


def test_mcp_tool_adapter_returns_call_descriptor():
    decl = adapter_loader.load_dict(_valid_mcp_tool_dict())
    adapter = adapter_loader.instantiate(decl)
    out = adapter.dispatch(
        {"owner": "anthropic", "repo": "claude", "issue_number": 42},
        folder_context="/projects/test",
    )
    assert out["kind"] == "mcp_tool_call_descriptor"
    assert out["tool_name"] == "mcp__github__get_issue"
    assert out["args"] == {
        "owner": "anthropic", "repo": "claude", "issue_number": 42,
    }
    assert out["adapter_provenance"]["adapter_name"] == "fetch-github-issue"
    assert out["adapter_provenance"]["folder_context"] == "/projects/test"


def test_mcp_tool_adapter_applies_arg_mapping():
    raw = _valid_mcp_tool_dict()
    raw["mcp_tool"]["arg_mapping"] = {"owner": "user", "repo": "repository"}
    decl = adapter_loader.load_dict(raw)
    adapter = adapter_loader.instantiate(decl)
    out = adapter.dispatch({"owner": "ant", "repo": "claude", "issue_number": 1})
    # Mapped keys should be renamed; unmapped keys pass through.
    assert out["args"] == {"user": "ant", "repository": "claude", "issue_number": 1}


def test_mcp_tool_adapter_injects_folder_context_when_schema_names_it():
    raw = _valid_mcp_tool_dict()
    raw["input_schema"]["properties"]["folder_context"] = {"type": "string"}
    decl = adapter_loader.load_dict(raw)
    adapter = adapter_loader.instantiate(decl)
    out = adapter.dispatch(
        {"owner": "a", "repo": "b", "issue_number": 1},
        folder_context="/projects/test",
    )
    # The adapter is conservative: it adds folder_context only when the
    # input_schema lists it explicitly.
    assert out["args"]["folder_context"] == "/projects/test"


def test_mcp_tool_adapter_does_not_inject_folder_context_when_schema_omits_it():
    decl = adapter_loader.load_dict(_valid_mcp_tool_dict())
    adapter = adapter_loader.instantiate(decl)
    out = adapter.dispatch(
        {"owner": "a", "repo": "b", "issue_number": 1},
        folder_context="/projects/test",
    )
    assert "folder_context" not in out["args"]


def test_mcp_tool_adapter_rejects_missing_tool_name():
    raw = _valid_mcp_tool_dict()
    raw["mcp_tool"] = {}  # Missing tool_name
    decl = adapter_loader.load_dict(raw)
    adapter = adapter_loader.instantiate(decl)
    with pytest.raises(ValueError) as exc:
        adapter.dispatch({"owner": "a", "repo": "b", "issue_number": 1})
    assert "tool_name" in str(exc.value)


# -----------------------------------------------------------------------------
# Unsupported kinds fail closed
# -----------------------------------------------------------------------------


def _adapter_decl(kind: str) -> dict:
    return {
        "name": f"test-{kind}-adapter",
        "description": f"Test declaration for the {kind} adapter.",
        "kind": kind,
        "input_schema": {"type": "object"},
        kind: {},
    }


@pytest.mark.parametrize("kind", ["rest", "shell", "unknown"])
def test_unimplemented_adapter_kinds_are_denied(kind):
    with pytest.raises(adapter_loader.AdapterValidationError):
        adapter_loader.load_dict(_adapter_decl(kind))


def test_local_llm_adapter_uses_audited_completion(monkeypatch, tmp_path):
    decl = adapter_loader.load_dict(_adapter_decl("local_llm"))
    adapter = adapter_loader.instantiate(decl)
    seen = {}

    def fake_complete(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "response": "local result", "captured": True}

    monkeypatch.setattr("workspaces.mcp_impl.local_llm_complete", fake_complete)
    out = adapter.dispatch(
        {"prompt": "Summarise", "max_tokens": 64},
        folder_context=str(tmp_path),
    )
    assert out["ok"] is True
    assert seen["prompt"] == "Summarise"
    assert seen["folder_context"] == str(tmp_path)
    assert seen["max_tokens"] == 64
    assert seen["capture"] is True


def test_local_llm_adapter_requires_prompt_and_folder(tmp_path):
    decl = adapter_loader.load_dict(_adapter_decl("local_llm"))
    adapter = adapter_loader.instantiate(decl)
    with pytest.raises(ValueError, match="folder_context"):
        adapter.dispatch({"prompt": "hello"})
    with pytest.raises(ValueError, match="non-empty prompt"):
        adapter.dispatch({}, folder_context=str(tmp_path))
