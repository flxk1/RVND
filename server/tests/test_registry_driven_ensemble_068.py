# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B8.4 (0.6.8.1) — registry-driven Tier C ensemble + generic role resolver.

This file pins the contract added in 0.6.8.1: ``tier_c_semantic_check``
no longer hard-codes its two-model ensemble. Instead it asks the local
model registry which ids are registered under the ``"lock-c"`` role
and uses those. When the registry has nothing registered the call falls
back to the historical defaults so out-of-the-box behaviour is
preserved.

The same lookup primitive (``resolve_models_for_role``) is exposed at the
``workspaces.local_llm`` level so any future role-keyed dispatcher
(validator, code-fix, drafter…) can use it without re-implementing the
glue.

Local LLMs are NEVER invoked in these tests — ``local_llm_classify`` is
monkey-patched and the registry is pointed at a tmp directory.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures: isolate the model registry to a tmp dir so we never read or
# write the real ~/.workspace/models/registry.json.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_models_dir(tmp_path, monkeypatch):
    """Point WORKSPACE_MODELS_DIR at a fresh tmp path."""
    root = tmp_path / "models"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(root))
    return root


def _register(model_id: str, role: str) -> None:
    """Register ``model_id`` under ``role`` in the isolated registry."""
    from workspaces import models_registry
    models_registry.register_model(model_id, role)


def _make_classify_stub(per_model_label: dict[str, str], ok: bool = True):
    """Return a stub for local_llm_classify keyed by ``model``."""

    def stub(text, categories, folder_context="", model=""):
        label = per_model_label.get(model, "insufficient")
        return {
            "ok": ok,
            "category": label,
            "model_used": model,
            "raw_response": label,
            "latency_ms": 1,
        }

    return stub


def _patch_classify(monkeypatch, fn):
    import workspaces.mcp_server as mcp_server
    monkeypatch.setattr(mcp_server, "local_llm_classify", fn, raising=True)


# ---------------------------------------------------------------------------
# tier_c_semantic_check — explicit models always win
# ---------------------------------------------------------------------------


def test_tier_c_uses_explicit_models_when_passed(isolated_models_dir, monkeypatch):
    """When a non-empty ``models=`` tuple is passed it must be used verbatim,
    even if the registry has different entries registered for lock-c."""
    # Registry says use "registry-model"; explicit arg says use "explicit-model".
    _register("registry-model", "lock-c")

    seen_models: list[str] = []

    def stub(text, categories, folder_context="", model=""):
        seen_models.append(model)
        return {"ok": True, "category": "pii_yes", "model_used": model}

    _patch_classify(monkeypatch, stub)

    from workspaces.lock.core import tier_c_semantic_check

    tier_c_semantic_check("Sample text", models=("explicit-model",))

    assert seen_models == ["explicit-model"], (
        "explicit models arg must override registry lookup"
    )


def test_tier_c_reads_from_registry_when_models_none(isolated_models_dir, monkeypatch):
    """With ``models=None`` (the default), the ensemble must be sourced from
    the registry's ``lock-c`` role entries."""
    _register("my-phi", "lock-c")
    _register("my-qwen", "lock-c")

    seen_models: list[str] = []

    def stub(text, categories, folder_context="", model=""):
        seen_models.append(model)
        return {"ok": True, "category": "pii_no", "model_used": model}

    _patch_classify(monkeypatch, stub)

    from workspaces.lock.core import tier_c_semantic_check

    result = tier_c_semantic_check("nothing personal here")

    assert result is not None
    assert set(seen_models) == {"my-phi", "my-qwen"}, (
        f"expected the two registered lock-c models, got {seen_models}"
    )
    assert result.label == "pii_no"


def test_tier_c_falls_back_to_defaults_when_registry_empty(
    isolated_models_dir, monkeypatch,
):
    """Registry has no lock-c entries → fall back to ENSEMBLE_MODELS_DEFAULT."""
    seen_models: list[str] = []

    def stub(text, categories, folder_context="", model=""):
        seen_models.append(model)
        return {"ok": True, "category": "pii_no", "model_used": model}

    _patch_classify(monkeypatch, stub)

    from workspaces.lock.core import ENSEMBLE_MODELS_DEFAULT, tier_c_semantic_check

    tier_c_semantic_check("Hello world")

    assert set(seen_models) == set(ENSEMBLE_MODELS_DEFAULT), (
        "empty registry must fall back to the historical default ensemble"
    )


def test_tier_c_uses_only_role_matched_models(isolated_models_dir, monkeypatch):
    """Registry has BOTH lock-c models AND validator models — the Tier C
    call must use only the lock-c ones, never the validator ones."""
    _register("lock-only-model", "lock-c")
    _register("validator-only-model", "validator")

    seen_models: list[str] = []

    def stub(text, categories, folder_context="", model=""):
        seen_models.append(model)
        return {"ok": True, "category": "pii_no", "model_used": model}

    _patch_classify(monkeypatch, stub)

    from workspaces.lock.core import tier_c_semantic_check

    tier_c_semantic_check("text")

    assert seen_models == ["lock-only-model"], (
        f"tier_c must use only lock-c role models, got {seen_models}"
    )
    assert "validator-only-model" not in seen_models


# ---------------------------------------------------------------------------
# resolve_models_for_role — the generic role lookup primitive
# ---------------------------------------------------------------------------


def test_resolve_models_for_role_returns_registered_list(isolated_models_dir):
    """Models registered under a role come back in positional-slot order
    (``order_n1`` first, ``order_n2`` second). Slot order is positional, not
    a quality ranking."""
    _register("phi", "lock-c")
    _register("qwen", "lock-c")

    from workspaces.local_llm import resolve_models_for_role

    assert resolve_models_for_role("lock-c") == ["phi", "qwen"]


def test_resolve_models_for_role_returns_empty_when_no_role_match(
    isolated_models_dir,
):
    """A role with no registered models returns an empty list (no exception)."""
    _register("phi", "lock-c")  # registered under a different role

    from workspaces.local_llm import resolve_models_for_role

    assert resolve_models_for_role("validator") == []
    assert resolve_models_for_role("code-fix") == []


def test_resolve_models_for_role_role_validator_works(isolated_models_dir):
    """The same primitive must work for any role — proves the pattern
    generalises beyond lock-c."""
    _register("phi-validator", "validator")
    _register("qwen-validator", "validator")

    from workspaces.local_llm import resolve_models_for_role

    out = resolve_models_for_role("validator")
    assert out == ["phi-validator", "qwen-validator"], (
        f"validator role should yield the two registered ids, got {out}"
    )
