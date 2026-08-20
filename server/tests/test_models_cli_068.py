# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the `workspaces models` CLI subcommand family (0.6.8.1 shell).

Local-model packaging CLI regression:

- ``workspaces models list`` returns no models on a fresh install.
- ``workspaces models register --role <r> --model <m>`` writes the registry.
- ``workspaces models list --health`` returns a structured health-check report.

The actual model download is exercised by the marketplace package's
``pull_models.sh`` script; here we test the registry + CLI plumbing only
(no real downloads — that's user-runtime).
"""
from __future__ import annotations

import json

import pytest

from workspaces import cli as cli_mod
from workspaces import models_registry


@pytest.fixture
def isolated_models_dir(tmp_path, monkeypatch):
    """Point WORKSPACE_MODELS_DIR at a tmp path so we don't touch the user's
    real ~/.workspace/models/."""
    models_root = tmp_path / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WORKSPACE_MODELS_DIR", str(models_root))
    return models_root


# ---------------------------------------------------------------------------
# `workspaces models list` — empty registry
# ---------------------------------------------------------------------------


def test_workspaces_models_list_empty_returns_no_models(isolated_models_dir, capsys):
    """A fresh install has no registered models; list must say so and exit 0."""
    rc = cli_mod.main(["models", "list"])
    assert rc == 0, "models list on empty registry should exit 0"
    captured = capsys.readouterr()
    # Friendly, actionable message — points the user at the register command.
    assert "no models registered" in captured.out
    assert "register" in captured.out
    # Registry file should NOT be created until a register call runs.
    registry_file = isolated_models_dir / "registry.json"
    assert not registry_file.exists(), (
        "listing on empty registry must not create the registry file"
    )


def test_workspaces_models_list_empty_json(isolated_models_dir, capsys):
    """JSON output on empty registry returns an empty array, exit 0."""
    rc = cli_mod.main(["models", "list", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed == []


# ---------------------------------------------------------------------------
# `workspaces models register` — writes the registry
# ---------------------------------------------------------------------------


def test_workspaces_models_register_writes_registry(isolated_models_dir, capsys):
    """After register, the registry file exists with the expected shape."""
    rc = cli_mod.main([
        "models", "register",
        "--role", "validator",
        "--model", "phi-3.5-mini-q4",
    ])
    assert rc == 0, "register with valid args should exit 0"

    registry_file = isolated_models_dir / "registry.json"
    assert registry_file.exists(), "register must persist registry.json"

    with registry_file.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    # Schema version recorded
    assert data["schema_version"] == 1
    # Model entry present
    assert "phi-3.5-mini-q4" in data["models"]
    entry = data["models"]["phi-3.5-mini-q4"]
    assert entry["roles"] == ["validator"]
    assert entry["registered_via"] == "register"
    assert entry["registered_at"]  # ISO timestamp
    # Default artifact path inside the models dir
    assert "phi-3.5-mini-q4" in entry["artifact_path"]
    # Role map updated — first positional slot taken. Slot keys are neutral
    # (order_n1 / order_n2); they are positional, NOT quality verdicts.
    assert data["role_map"]["validator"]["order_n1"] == "phi-3.5-mini-q4"
    assert data["role_map"]["validator"]["order_n2"] == ""

    # CLI output mentions what got registered
    captured = capsys.readouterr()
    assert "registered" in captured.out
    assert "phi-3.5-mini-q4" in captured.out
    assert "validator" in captured.out


def test_workspaces_models_register_second_model_fills_order_n2_slot(
    isolated_models_dir, capsys,
):
    """Registering a second model under the same role fills the second
    positional slot (``order_n2``). Slot names are positional, not preferential."""
    rc = cli_mod.main([
        "models", "register",
        "--role", "validator",
        "--model", "phi-3.5-mini-q4",
    ])
    assert rc == 0
    capsys.readouterr()

    rc = cli_mod.main([
        "models", "register",
        "--role", "validator",
        "--model", "qwen-2.5-coder-3b-q4",
    ])
    assert rc == 0

    data = json.loads((isolated_models_dir / "registry.json").read_text())
    assert data["role_map"]["validator"]["order_n1"] == "phi-3.5-mini-q4"
    assert data["role_map"]["validator"]["order_n2"] == "qwen-2.5-coder-3b-q4"
    # Both models recorded
    assert set(data["models"].keys()) == {
        "phi-3.5-mini-q4", "qwen-2.5-coder-3b-q4",
    }


def test_workspaces_models_register_invalid_role_refuses(
    isolated_models_dir, capsys,
):
    """An unknown role string must refuse cleanly with a non-zero exit."""
    rc = cli_mod.main([
        "models", "register",
        "--role", "bogus-role",
        "--model", "phi-3.5-mini-q4",
    ])
    assert rc != 0, "invalid role must NOT exit 0"
    captured = capsys.readouterr()
    assert "ERROR" in captured.err or "role" in captured.err.lower()


def test_workspaces_models_register_offline_flag_recorded(
    isolated_models_dir, capsys,
):
    """--offline records the registered_via='offline' marker for air-gap audit."""
    rc = cli_mod.main([
        "models", "register",
        "--role", "validator",
        "--model", "phi-3.5-mini-q4",
        "--offline",
    ])
    assert rc == 0
    data = json.loads((isolated_models_dir / "registry.json").read_text())
    assert data["models"]["phi-3.5-mini-q4"]["registered_via"] == "offline"


def test_workspaces_models_register_sha256_recorded(
    isolated_models_dir, capsys,
):
    """An explicit --sha256 hex lands on the entry for later verification."""
    fake_sha = "e4165e3a71af97f1b4820da61079826d8752a2088e313af0c7d346796c38eff5"
    rc = cli_mod.main([
        "models", "register",
        "--role", "validator",
        "--model", "phi-3.5-mini-q4",
        "--sha256", fake_sha,
    ])
    assert rc == 0
    data = json.loads((isolated_models_dir / "registry.json").read_text())
    assert data["models"]["phi-3.5-mini-q4"]["sha256_verified"] == fake_sha


# ---------------------------------------------------------------------------
# `workspaces models list --health` — health check
# ---------------------------------------------------------------------------


def test_workspaces_models_list_health_shows_status(isolated_models_dir, capsys):
    """After register + a synthetic artifact on disk, health reports 'ok'."""
    # Register the model first
    rc = cli_mod.main([
        "models", "register",
        "--role", "validator",
        "--model", "phi-3.5-mini-q4",
    ])
    assert rc == 0
    capsys.readouterr()

    # Create a synthetic artifact at the registered path so health-check has
    # something to find. (Real usage downloads ~2 GB of GGUF; tests just need
    # the file present.)
    artifact = isolated_models_dir / "phi-3.5-mini-q4" / "phi-3.5-mini-q4.gguf"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"fake-gguf-bytes-for-health-check")

    rc = cli_mod.main(["models", "list", "--health"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "phi-3.5-mini-q4" in captured.out
    # 0.6.8.2: new format uses glyphs — artifact=✓ when the file is present.
    assert "artifact=✓" in captured.out
    assert "validator" in captured.out


def test_workspaces_models_list_health_reports_missing_artifact(
    isolated_models_dir, capsys,
):
    """Registered model with no artifact on disk reports 'artifact=✗'."""
    rc = cli_mod.main([
        "models", "register",
        "--role", "validator",
        "--model", "phi-3.5-mini-q4",
    ])
    assert rc == 0
    capsys.readouterr()

    # Deliberately do NOT create the artifact file.
    rc = cli_mod.main(["models", "list", "--health"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "phi-3.5-mini-q4" in captured.out
    assert "artifact=✗" in captured.out


def test_workspaces_models_list_health_reports_empty_artifact(
    isolated_models_dir, capsys,
):
    """Zero-byte artifact reports 'empty' (distinct from 'missing')."""
    rc = cli_mod.main([
        "models", "register",
        "--role", "validator",
        "--model", "phi-3.5-mini-q4",
    ])
    assert rc == 0
    capsys.readouterr()

    artifact = isolated_models_dir / "phi-3.5-mini-q4" / "phi-3.5-mini-q4.gguf"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.touch()  # zero bytes

    rc = cli_mod.main(["models", "list", "--health", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert len(parsed) == 1
    assert parsed[0]["health"]["status"] == "empty"


def test_workspaces_models_list_health_json_shape(isolated_models_dir, capsys):
    """JSON --health output is a list of objects with the expected keys."""
    rc = cli_mod.main([
        "models", "register",
        "--role", "lock-tier-C",
        "--model", "phi-3.5-mini-q4",
    ])
    assert rc == 0
    capsys.readouterr()

    rc = cli_mod.main(["models", "list", "--health", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    row = parsed[0]
    for k in ("id", "artifact_path", "roles", "registered_via", "registered_at",
              "sha256_verified", "health"):
        assert k in row
    for k in ("id", "status", "detail", "size_bytes", "exists"):
        assert k in row["health"]
    # Role canonicalisation: lock-tier-C uppercase preserved
    assert "lock-tier-C" in row["roles"]


# ---------------------------------------------------------------------------
# Pull plumbing — wraps the marketplace package's pull_models.sh
# ---------------------------------------------------------------------------


def test_workspaces_models_pull_invokes_package_script(
    isolated_models_dir, tmp_path, capsys, monkeypatch,
):
    """`workspaces models pull` shells out to the marketplace package's
    pull_models.sh with --only <id>. Test with a stub script that just echoes
    its args (no actual download)."""
    # Create a stub package with a fake pull_models.sh
    pkg = tmp_path / "fake-validator-pkg"
    scripts = pkg / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    stub = scripts / "pull_models.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "echo \"stub pull invoked: $@\"\n"
        "exit 0\n"
    )
    stub.chmod(0o755)

    rc = cli_mod.main([
        "models", "pull", "phi-3.5-mini-q4",
        "--package-root", str(pkg),
    ])
    assert rc == 0, "pull with a successful stub script should exit 0"
    captured = capsys.readouterr()
    assert "stub pull invoked" in captured.out
    assert "--only" in captured.out
    assert "phi-3.5-mini-q4" in captured.out


def test_workspaces_models_pull_missing_package_root_returns_error(
    isolated_models_dir, tmp_path, capsys,
):
    """If the package root has no pull_models.sh, exit non-zero with a message."""
    empty_pkg = tmp_path / "no-scripts-here"
    empty_pkg.mkdir()
    rc = cli_mod.main([
        "models", "pull", "phi-3.5-mini-q4",
        "--package-root", str(empty_pkg),
    ])
    assert rc != 0
    captured = capsys.readouterr()
    # The error went to stderr per the registry module's pattern.
    assert "pull script not found" in captured.err or "not found" in captured.err


# ---------------------------------------------------------------------------
# Neutral positional role slots — order_n1 / order_n2 (replaces primary / backup)
# ---------------------------------------------------------------------------


def test_models_registry_reads_order_n1_n2_keys(isolated_models_dir):
    """A registry file written with the new ``order_n1`` / ``order_n2`` keys
    is read back correctly by ``models_for_role`` — slot N1 comes first, N2
    second. The slot names are positional, not preferential."""
    # Hand-build a registry in the new shape (no register_model needed —
    # this test pins the *reader* contract).
    registry_file = isolated_models_dir / "registry.json"
    payload = {
        "schema_version": 1,
        "models": {
            "phi-new": {"roles": ["validator"], "artifact_path": "",
                          "sha256_verified": "", "registered_at": "",
                          "registered_via": "register"},
            "qwen-new": {"roles": ["validator"], "artifact_path": "",
                           "sha256_verified": "", "registered_at": "",
                           "registered_via": "register"},
        },
        "role_map": {
            "validator": {"order_n1": "phi-new", "order_n2": "qwen-new"},
        },
    }
    registry_file.write_text(json.dumps(payload))

    got = models_registry.models_for_role("validator")
    assert got == ["phi-new", "qwen-new"], (
        f"expected positional slot order [n1, n2], got {got}"
    )


def test_models_registry_back_compat_reads_legacy_primary_backup_keys_with_warning(
    isolated_models_dir, caplog,
):
    """A legacy registry file using the old ``primary`` / ``backup`` keys is
    still readable — translated to ``order_n1`` / ``order_n2`` on the fly —
    and a one-time deprecation warning is emitted via the module logger.
    Legacy-key support is scheduled for removal in 0.7."""
    import logging

    registry_file = isolated_models_dir / "registry.json"
    legacy_payload = {
        "schema_version": 1,
        "models": {
            "phi-legacy": {"roles": ["validator"], "artifact_path": "",
                            "sha256_verified": "", "registered_at": "",
                            "registered_via": "register"},
            "qwen-legacy": {"roles": ["validator"], "artifact_path": "",
                             "sha256_verified": "", "registered_at": "",
                             "registered_via": "register"},
        },
        "role_map": {
            "validator": {"primary": "phi-legacy", "backup": "qwen-legacy"},
        },
    }
    registry_file.write_text(json.dumps(legacy_payload))

    # Reset the one-time warning cache so this test reliably sees the warning
    # regardless of test ordering.
    models_registry._LEGACY_KEYS_WARNED.clear()

    with caplog.at_level(logging.WARNING, logger="workspaces.models_registry"):
        got = models_registry.models_for_role("validator")

    # Reader returns the same list as the new-key form would.
    assert got == ["phi-legacy", "qwen-legacy"], (
        f"legacy primary/backup must read as order_n1/order_n2; got {got}"
    )

    # Warning was emitted and mentions both the legacy and new key names.
    warning_messages = [r.getMessage() for r in caplog.records
                        if r.levelno >= logging.WARNING]
    assert any("primary" in m and "backup" in m for m in warning_messages), (
        f"expected a legacy-key warning mentioning primary/backup; got {warning_messages}"
    )
    assert any("order_n1" in m and "order_n2" in m for m in warning_messages), (
        f"warning should name the new keys; got {warning_messages}"
    )


def test_models_register_cli_uses_neutral_n1_n2_output(
    isolated_models_dir, capsys,
):
    """``workspaces models register`` and ``workspaces models list`` must not emit
    the legacy ``primary`` / ``backup`` strings in user-facing output. Those
    words imply a quality ranking the registry does not assert."""
    rc = cli_mod.main([
        "models", "register",
        "--role", "validator",
        "--model", "phi-3.5-mini-q4",
    ])
    assert rc == 0
    register_out = capsys.readouterr()
    combined_register = register_out.out + register_out.err
    assert "primary" not in combined_register.lower(), (
        f"register output should not say 'primary'; got: {combined_register!r}"
    )
    assert "backup" not in combined_register.lower(), (
        f"register output should not say 'backup'; got: {combined_register!r}"
    )

    rc = cli_mod.main(["models", "list"])
    assert rc == 0
    list_out = capsys.readouterr()
    combined_list = list_out.out + list_out.err
    assert "primary" not in combined_list.lower(), (
        f"list output should not say 'primary'; got: {combined_list!r}"
    )
    assert "backup" not in combined_list.lower(), (
        f"list output should not say 'backup'; got: {combined_list!r}"
    )
