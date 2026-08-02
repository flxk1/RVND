# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The public snapshot gate must stay strict, local, and deterministic."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "release" / "public-snapshot.json"


def _mod():
    spec = importlib.util.spec_from_file_location(
        "verify_public_snapshot", REPO / "scripts" / "verify_public_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_live_snapshot_is_self_contained():
    mod = _mod()
    assert mod.violations(mod.load_manifest(), mod.tracked_entries()) == []


def test_malformed_manifest_is_refused(tmp_path):
    mod = _mod()
    good = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mutations = (
        lambda value: value.update(schema="unknown"),
        lambda value: value.update(required_paths=[]),
        lambda value: value.update(forbidden_path_prefixes=["work", "work"]),
        lambda value: value.update(forbidden_tracked_suffixes=[""]),
        lambda value: value.update(forbidden_content_markers=[]),
    )
    for mutate in mutations:
        candidate = json.loads(json.dumps(good))
        mutate(candidate)
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(candidate), encoding="utf-8")
        with pytest.raises(ValueError):
            mod.load_manifest(path)


def test_forbidden_content_and_submodules_fail_closed():
    mod = _mod()
    manifest = mod.load_manifest()
    entries = [("100644", path) for path in manifest["required_paths"]]
    entries += [
        ("100644", "work/release-plan.md"),
        ("100644", "deploy/private.key"),
        ("120000", "docs/external-link"),
        ("160000", "vendor/external"),
    ]
    failures = mod.violations(manifest, entries)
    assert any("workspace path" in item for item in failures)
    assert any("secret-bearing" in item for item in failures)
    assert any("symbolic link" in item for item in failures)
    assert any("submodule" in item for item in failures)


def test_private_key_content_is_refused_but_public_key_is_allowed(tmp_path):
    mod = _mod()
    manifest = mod.load_manifest()
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    private.write_text("-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n", encoding="utf-8")
    public.write_text("-----BEGIN PUBLIC KEY-----\nfixture-only\n", encoding="utf-8")
    entries = [("100644", path) for path in manifest["required_paths"]]
    entries += [("100644", "private.pem"), ("100644", "public.pem")]
    failures = mod.violations(manifest, entries, repo=tmp_path)
    assert failures == ["private-key marker is tracked: private.pem"]
