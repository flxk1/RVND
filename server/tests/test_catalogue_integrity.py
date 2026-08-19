# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for catalogue_integrity — checksums + HMAC + mutation audit.

Covers the security hardening of the Workspace MCP skill catalogue:
    1. Clean catalogue verifies in warn + enforce mode
    2. Tampered SKILL.md fails per-plugin checksum
    3. Modified catalogue without re-sign fails HMAC
    4. warn mode does not block; enforce mode does
    5. Mutation audit roundtrip via read_mutation_audit
    6. Legacy catalogue (no integrity block) — warn mode emits warning + proceeds
    7. Bootstrap of HMAC secret on first use
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from rvnd import catalogue_integrity as ci


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def plugin_root(tmp_path: Path) -> Path:
    """Create a tiny fake plugin tree with plugin.json + two SKILL.md files."""
    root = tmp_path / "fake-plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "fake-plugin", "version": "1.0.0"}),
        encoding="utf-8",
    )
    (root / "skills" / "alpha").mkdir(parents=True)
    (root / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: \"a\"\n---\n# alpha\n",
        encoding="utf-8",
    )
    (root / "skills" / "beta").mkdir(parents=True)
    (root / "skills" / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: \"b\"\n---\n# beta\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def secret_path(tmp_path: Path) -> Path:
    """Use a tmp HMAC secret path so tests don't touch the user's ~/.workspace."""
    return tmp_path / "catalogue-hmac.key"


@pytest.fixture
def signed_catalogue(plugin_root: Path, secret_path: Path, tmp_path: Path) -> tuple[Path, dict]:
    """A catalogue file with the fake plugin registered + signed."""
    cat_path = tmp_path / "skill-companions.json"
    catalogue = {
        "version": 1,
        "families": {
            "fake-plugin": {
                "label": "Fake test plugin",
                "skills": ["fake-plugin:alpha", "fake-plugin:beta"],
            },
        },
        "integrity": {
            "checksums": {
                "fake-plugin": ci.compute_plugin_checksums(plugin_root),
            },
        },
    }
    written = ci.sign_and_save_catalogue(catalogue, cat_path, secret_path=secret_path)
    return cat_path, written


# ---------------------------------------------------------------------------
# 1. Clean catalogue verifies
# ---------------------------------------------------------------------------

def test_clean_catalogue_verifies_warn_mode(signed_catalogue, secret_path):
    _, catalogue = signed_catalogue
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_WARN)
    assert result.ok
    assert result.catalogue_hmac_ok is True
    assert result.errors == []
    assert result.warnings == []
    assert result.plugin_results["fake-plugin"]["ok"]


def test_clean_catalogue_verifies_enforce_mode(signed_catalogue, secret_path):
    _, catalogue = signed_catalogue
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_ENFORCE)
    assert result.ok
    assert result.catalogue_hmac_ok is True
    assert result.errors == []


def test_legacy_mode_skips_checks(signed_catalogue, secret_path):
    _, catalogue = signed_catalogue
    # Even if we corrupt the HMAC, legacy mode passes
    catalogue["integrity"]["hmac"] = "00" * 32
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_LEGACY)
    assert result.ok
    assert result.warnings == []
    assert result.errors == []


# ---------------------------------------------------------------------------
# 2. Tampered SKILL.md fails per-plugin checksum
# ---------------------------------------------------------------------------

def test_tampered_skill_md_fails_warn_mode(signed_catalogue, plugin_root, secret_path):
    _, catalogue = signed_catalogue
    # Modify a SKILL.md on disk WITHOUT re-running register
    (plugin_root / "skills" / "alpha" / "SKILL.md").write_text(
        "tampered content", encoding="utf-8",
    )
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_WARN)
    # warn mode: ok=True (does not block), warnings populated
    assert result.ok
    assert any("fake-plugin" in w and "checksum mismatch" in w
               for w in result.warnings)
    assert not result.plugin_results["fake-plugin"]["ok"]


def test_tampered_skill_md_blocks_in_enforce_mode(signed_catalogue, plugin_root, secret_path):
    _, catalogue = signed_catalogue
    (plugin_root / "skills" / "beta" / "SKILL.md").write_text(
        "evil content", encoding="utf-8",
    )
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_ENFORCE)
    assert not result.ok
    assert any("fake-plugin" in e and "checksum mismatch" in e
               for e in result.errors)


def test_missing_skill_md_detected(signed_catalogue, plugin_root, secret_path):
    _, catalogue = signed_catalogue
    (plugin_root / "skills" / "beta" / "SKILL.md").unlink()
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_ENFORCE)
    assert not result.ok
    assert any("missing file" in e for e in result.errors)


def test_unregistered_skill_md_detected(signed_catalogue, plugin_root, secret_path):
    _, catalogue = signed_catalogue
    injected = plugin_root / "skills" / "injected"
    injected.mkdir()
    (injected / "SKILL.md").write_text("unregistered", encoding="utf-8")
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_ENFORCE)
    assert not result.ok
    assert any("unregistered integrity-relevant file" in e for e in result.errors)


def test_missing_plugin_checksums_rejected_in_enforce(secret_path):
    catalogue = {
        "version": 1,
        "families": {"unsigned-plugin": {"skills": []}},
        "integrity": {"checksums": {}},
    }
    catalogue["integrity"]["hmac"] = ci.compute_hmac(catalogue, secret_path)
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_ENFORCE)
    assert not result.ok
    assert any("has no recorded checksums" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 3. Modified catalogue without re-sign fails HMAC
# ---------------------------------------------------------------------------

def test_modified_catalogue_fails_hmac(signed_catalogue, secret_path):
    _, catalogue = signed_catalogue
    # Inject a fake plugin into the catalogue without re-signing
    catalogue["families"]["evil-plugin"] = {
        "label": "I am the attacker",
        "skills": ["evil-plugin:exfiltrate"],
    }
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_ENFORCE)
    assert not result.ok
    assert result.catalogue_hmac_ok is False
    assert any("HMAC verification failed" in e for e in result.errors)


def test_modified_catalogue_warns_in_warn_mode(signed_catalogue, secret_path):
    _, catalogue = signed_catalogue
    catalogue["families"]["evil-plugin"] = {"label": "x", "skills": []}
    result = ci.verify_catalogue(catalogue, secret_path=secret_path, mode=ci.MODE_WARN)
    assert result.ok
    assert result.catalogue_hmac_ok is False
    assert any("HMAC verification failed" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 4. Re-sign after a legitimate change clears the failure
# ---------------------------------------------------------------------------

def test_resign_clears_failure(signed_catalogue, plugin_root, secret_path):
    cat_path, catalogue = signed_catalogue
    # Make a legitimate change: rewrite a SKILL.md + re-register checksums + re-sign
    (plugin_root / "skills" / "alpha" / "SKILL.md").write_text(
        "new version of alpha", encoding="utf-8",
    )
    catalogue["integrity"]["checksums"]["fake-plugin"] = ci.compute_plugin_checksums(plugin_root)
    ci.sign_and_save_catalogue(catalogue, cat_path, secret_path=secret_path)
    # Reload + verify
    catalogue2 = json.loads(cat_path.read_text(encoding="utf-8"))
    result = ci.verify_catalogue(catalogue2, secret_path=secret_path, mode=ci.MODE_ENFORCE)
    assert result.ok
    assert result.catalogue_hmac_ok is True


# ---------------------------------------------------------------------------
# 5. Mutation audit roundtrip
# ---------------------------------------------------------------------------

def test_mutation_audit_roundtrip(signed_catalogue, tmp_path):
    cat_path, _ = signed_catalogue
    e1 = ci.append_mutation_audit(
        cat_path,
        action="add", plugin_id="fake-plugin", actor="test",
        reason="initial", before_hmac="", after_hmac="abc",
    )
    e2 = ci.append_mutation_audit(
        cat_path,
        action="update", plugin_id="fake-plugin", actor="test",
        reason="checksum refresh", before_hmac="abc", after_hmac="def",
    )
    entries = ci.read_mutation_audit(cat_path, limit=10)
    # Newest first
    assert len(entries) == 2
    assert entries[0]["action"] == "update"
    assert entries[1]["action"] == "add"
    assert entries[0]["after_hmac"] == "def"
    assert entries[1]["after_hmac"] == "abc"
    # Sidecar file is at the expected path
    sidecar = cat_path.parent / "catalogue-mutations.jsonl"
    assert sidecar.exists()
    raw = sidecar.read_text(encoding="utf-8")
    assert "fake-plugin" in raw
    assert "checksum refresh" in raw


def test_mutation_audit_rejects_unknown_action(signed_catalogue):
    cat_path, _ = signed_catalogue
    with pytest.raises(ValueError):
        ci.append_mutation_audit(
            cat_path, action="hack", plugin_id="x", actor="t", reason="r",
        )


# ---------------------------------------------------------------------------
# 6. Legacy entry (no integrity block) — warn mode does not block
# ---------------------------------------------------------------------------

def test_legacy_catalogue_no_integrity_block(tmp_path, secret_path):
    cat_path = tmp_path / "skill-companions.json"
    legacy = {
        "version": 1,
        "families": {"legacy-plugin": {"label": "old", "skills": []}},
    }
    cat_path.write_text(json.dumps(legacy), encoding="utf-8")
    result = ci.verify_catalogue(legacy, secret_path=secret_path, mode=ci.MODE_WARN)
    assert result.ok
    assert result.catalogue_hmac_ok is None  # no integrity block
    assert any("no integrity block" in w for w in result.warnings)


def test_legacy_catalogue_enforce_mode_rejects_no_integrity(tmp_path, secret_path):
    legacy = {"version": 1, "families": {}}
    result = ci.verify_catalogue(legacy, secret_path=secret_path, mode=ci.MODE_ENFORCE)
    assert not result.ok
    assert result.errors


# ---------------------------------------------------------------------------
# 7. Secret bootstrap
# ---------------------------------------------------------------------------

def test_secret_generated_on_first_use(tmp_path):
    p = tmp_path / "subdir" / "secret.key"
    assert not p.exists()
    secret = ci._ensure_secret(p)
    assert p.exists()
    assert len(secret) >= 32
    # Mode 0600 (owner read/write only) — best-effort on platforms that support it
    if os.name == "posix":
        mode = oct(p.stat().st_mode & 0o777)
        assert mode == "0o600", f"expected 0o600 but got {mode}"


def test_secret_reused_on_subsequent_calls(tmp_path):
    p = tmp_path / "secret.key"
    s1 = ci._ensure_secret(p)
    s2 = ci._ensure_secret(p)
    assert s1 == s2


def test_secret_with_whitespace_edge_bytes_is_not_stripped(tmp_path):
    """Regression: a 32-byte binary secret whose first/last byte is an ASCII
    whitespace value must be read back verbatim, not .strip()-ed to 31 bytes
    (which used to regenerate the key on read and break HMAC verification).
    """
    p = tmp_path / "secret.key"
    # 32 bytes, deliberately newline-led and space-trailed.
    seeded = b"\n" + bytes(range(1, 31)) + b" "
    assert len(seeded) == 32
    p.write_bytes(seeded)
    p.chmod(0o600)
    got = ci._ensure_secret(p)
    assert got == seeded, "secret was altered on read (regenerated or stripped)"
    assert len(got) == 32


def test_verify_missing_secret_does_not_create_it(tmp_path):
    p = tmp_path / "absent" / "secret.key"
    catalogue = {"version": 1, "families": {}, "integrity": {"hmac": "0" * 64}}
    result = ci.verify_catalogue(catalogue, secret_path=p, mode=ci.MODE_ENFORCE)
    assert not result.ok
    assert not p.exists()
    assert not p.parent.exists()


def test_existing_short_secret_is_rejected_without_rotation(tmp_path):
    p = tmp_path / "secret.key"
    p.write_bytes(b"x" * 31)
    p.chmod(0o600)
    before = p.read_bytes()
    with pytest.raises(ci.CatalogueSecretError):
        ci._ensure_secret(p)
    assert p.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_existing_insecure_secret_permissions_are_rejected(tmp_path):
    p = tmp_path / "secret.key"
    p.write_bytes(b"x" * 32)
    p.chmod(0o644)
    with pytest.raises(ci.CatalogueSecretError, match="mode 0600"):
        ci._ensure_secret(p)
    assert p.read_bytes() == b"x" * 32


@pytest.mark.skipif(os.name != "posix", reason="POSIX atomic publication contract")
def test_concurrent_first_creation_returns_canonical_winner(tmp_path):
    p = tmp_path / "secret.key"
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _n: ci._ensure_secret(p), range(48)))
    assert len(set(results)) == 1
    assert results[0] == p.read_bytes()
    assert (p.stat().st_mode & 0o777) == 0o600
    assert not list(tmp_path.glob(f".{p.name}.*"))


# ---------------------------------------------------------------------------
# 8. Mode resolution from env var
# ---------------------------------------------------------------------------

def test_current_mode_default_is_warn(monkeypatch):
    monkeypatch.delenv("WORKSPACE_CATALOGUE_MODE", raising=False)
    assert ci.current_mode() == ci.MODE_WARN


def test_current_mode_env_var_enforce(monkeypatch):
    monkeypatch.setenv("WORKSPACE_CATALOGUE_MODE", "enforce")
    assert ci.current_mode() == ci.MODE_ENFORCE


def test_current_mode_invalid_falls_back_to_warn(monkeypatch):
    monkeypatch.setenv("WORKSPACE_CATALOGUE_MODE", "tellEveryone")
    assert ci.current_mode() == ci.MODE_WARN
