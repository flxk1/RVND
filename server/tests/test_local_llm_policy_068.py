# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the local_llm policy block (0.6.8.2 — re-panel P0).

Validates that the BYOM + air-gap commitment YAML schema is actually parsed
by ``rvnd.policy``, not just published as design intent. The schema
Local model policy:

    policy:
      local_llm:
        route_by_kind:
          validator: phi-3.5-mini-q4
          lock-c: phi-3.5-mini-q4
          intent-router: qwen-2.5-coder-3b-q4
        mode: cloud-allowed       # or: local-only, cloud-fallback
        on_insufficient: escalate-to-cloud  # or: escalate-to-human, refuse
"""

from __future__ import annotations

import json

import pytest

from rvnd import (
    FolderPolicy,
    InvalidPolicy,
    LocalLlmPolicy,
    LOCAL_LLM_MODE_CLOUD_ALLOWED,
    LOCAL_LLM_MODE_LOCAL_ONLY,
    LOCAL_LLM_MODE_CLOUD_FALLBACK,
    LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD,
    LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_HUMAN,
    LOCAL_LLM_ON_INSUFFICIENT_REFUSE,
    POLICY_FILENAME,
    load_policy,
    save_policy,
)


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "vault"
    f.mkdir()
    return f


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_policy_has_empty_local_llm_section(folder):
    """A folder with no policy file has the documented local_llm defaults:
    empty route map, cloud-allowed, escalate-to-cloud."""
    pol = load_policy(folder)
    assert pol.local_llm.route_by_kind == {}
    assert pol.local_llm.mode == LOCAL_LLM_MODE_CLOUD_ALLOWED
    assert pol.local_llm.on_insufficient == LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD


# ---------------------------------------------------------------------------
# Parse — round trip + happy paths
# ---------------------------------------------------------------------------


def test_load_policy_parses_route_by_kind(folder):
    (folder / POLICY_FILENAME).write_text(json.dumps({
        "local_llm": {
            "route_by_kind": {
                "validator": "phi-3.5-mini-q4",
                "lock-c": "phi-3.5-mini-q4",
                "intent-router": "qwen-2.5-coder-3b-q4",
            },
        },
    }))
    pol = load_policy(folder)
    assert pol.local_llm.route_by_kind["validator"] == "phi-3.5-mini-q4"
    assert pol.local_llm.route_by_kind["lock-c"] == "phi-3.5-mini-q4"
    assert pol.local_llm.route_by_kind["intent-router"] == "qwen-2.5-coder-3b-q4"
    # Other defaults untouched.
    assert pol.local_llm.mode == LOCAL_LLM_MODE_CLOUD_ALLOWED
    assert pol.local_llm.on_insufficient == LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_CLOUD


def test_load_policy_parses_mode_local_only(folder):
    """The air-gap claim (D22 = YES) requires local-only to actually load."""
    (folder / POLICY_FILENAME).write_text(json.dumps({
        "local_llm": {"mode": "local-only"},
    }))
    pol = load_policy(folder)
    assert pol.local_llm.mode == LOCAL_LLM_MODE_LOCAL_ONLY


def test_load_policy_parses_mode_cloud_fallback(folder):
    (folder / POLICY_FILENAME).write_text(json.dumps({
        "local_llm": {"mode": "cloud-fallback"},
    }))
    pol = load_policy(folder)
    assert pol.local_llm.mode == LOCAL_LLM_MODE_CLOUD_FALLBACK


def test_load_policy_parses_on_insufficient_escalate_human(folder):
    (folder / POLICY_FILENAME).write_text(json.dumps({
        "local_llm": {"on_insufficient": "escalate-to-human"},
    }))
    pol = load_policy(folder)
    assert pol.local_llm.on_insufficient == LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_HUMAN


def test_load_policy_parses_on_insufficient_refuse(folder):
    (folder / POLICY_FILENAME).write_text(json.dumps({
        "local_llm": {"on_insufficient": "refuse"},
    }))
    pol = load_policy(folder)
    assert pol.local_llm.on_insufficient == LOCAL_LLM_ON_INSUFFICIENT_REFUSE


# ---------------------------------------------------------------------------
# Validation — wrong values must raise, not silently fall through
# ---------------------------------------------------------------------------


def test_load_policy_rejects_invalid_mode(folder):
    """Silent fall-through on a bad mode would let the controller believe
    air-gap is on when it isn't. Raise instead."""
    (folder / POLICY_FILENAME).write_text(json.dumps({
        "local_llm": {"mode": "yolo"},
    }))
    with pytest.raises(InvalidPolicy):
        load_policy(folder)


def test_load_policy_rejects_invalid_on_insufficient(folder):
    (folder / POLICY_FILENAME).write_text(json.dumps({
        "local_llm": {"on_insufficient": "ignore-it"},
    }))
    with pytest.raises(InvalidPolicy):
        load_policy(folder)


def test_load_policy_rejects_non_dict_local_llm_block(folder):
    (folder / POLICY_FILENAME).write_text(json.dumps({
        "local_llm": ["not", "a", "dict"],
    }))
    with pytest.raises(InvalidPolicy):
        load_policy(folder)


def test_load_policy_rejects_non_dict_route_by_kind(folder):
    (folder / POLICY_FILENAME).write_text(json.dumps({
        "local_llm": {"route_by_kind": "validator=phi"},
    }))
    with pytest.raises(InvalidPolicy):
        load_policy(folder)


# ---------------------------------------------------------------------------
# Round trip — save then load preserves the block
# ---------------------------------------------------------------------------


def test_save_policy_round_trips_local_llm_section(folder):
    pol = FolderPolicy(
        local_llm=LocalLlmPolicy(
            route_by_kind={
                "validator": "phi-3.5-mini-q4",
                "lock-c": "qwen-2.5-coder-3b-q4",
            },
            mode=LOCAL_LLM_MODE_LOCAL_ONLY,
            on_insufficient=LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_HUMAN,
        ),
    )
    save_policy(folder, pol)
    loaded = load_policy(folder)
    assert loaded.local_llm.route_by_kind == {
        "validator": "phi-3.5-mini-q4",
        "lock-c": "qwen-2.5-coder-3b-q4",
    }
    assert loaded.local_llm.mode == LOCAL_LLM_MODE_LOCAL_ONLY
    assert loaded.local_llm.on_insufficient == LOCAL_LLM_ON_INSUFFICIENT_ESCALATE_HUMAN


def test_save_policy_omits_local_llm_block_when_defaults(folder):
    """Default-only local_llm shouldn't grow the on-disk JSON."""
    pol = FolderPolicy()
    save_policy(folder, pol)
    raw = json.loads((folder / POLICY_FILENAME).read_text())
    assert "local_llm" not in raw


# ---------------------------------------------------------------------------
# Integration with the models_registry
# ---------------------------------------------------------------------------


def test_policy_with_route_by_kind_resolves_validator_model(folder, tmp_path, monkeypatch):
    """The validator role bound by route_by_kind should be resolvable from the
    registry. End-to-end smoke: write a policy + register the model + look it up.
    """
    # Point the registry at a tmp dir so the test is hermetic.
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(tmp_path / "models"))

    from rvnd import models_registry
    models_registry.register_model(
        "phi-3.5-mini-q4",
        role="validator",
        artifact_path=str(tmp_path / "phi.gguf"),
    )

    pol = FolderPolicy(
        local_llm=LocalLlmPolicy(
            route_by_kind={"validator": "phi-3.5-mini-q4"},
            mode=LOCAL_LLM_MODE_LOCAL_ONLY,
        ),
    )
    save_policy(folder, pol)
    loaded = load_policy(folder)
    bound = loaded.local_llm.route_by_kind.get("validator")
    assert bound == "phi-3.5-mini-q4"
    # And the registry knows it.
    assert bound in models_registry.models_for_role("validator")
