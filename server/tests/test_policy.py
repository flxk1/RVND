# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the per-folder policy (B6)."""

from __future__ import annotations

import json

import pytest

from workspaces import (
    FolderPolicy,
    MutationLog,
    POLICY_FILENAME,
    LOCK_DISCLAIMER,
    disable_oversight,
    disable_lock,
    enable_oversight,
    enable_lock,
    load_policy,
    policy_path,
    save_policy,
)


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "vault"
    f.mkdir()
    return f


@pytest.fixture
def log_root(tmp_path):
    return tmp_path / "logs"


# ===========================================================================
# Defaults
# ===========================================================================


def test_no_policy_file_returns_default(folder):
    pol = load_policy(folder)
    assert pol.privacy_lock_enabled is True
    assert pol.oversight_enabled is True
    assert pol.oversight_default_level == "approve"
    assert pol.acknowledgements == {}


def test_default_policy_has_lock_and_oversight_active(folder):
    pol = load_policy(folder)
    assert pol.lock_is_active is True
    assert pol.oversight_is_active is True


# ===========================================================================
# Round-trip + atomic write
# ===========================================================================


def test_save_then_load_round_trip(folder):
    pol = FolderPolicy(
        privacy_lock_enabled=False,
        oversight_enabled=True,
        oversight_default_level="supervised",
    )
    save_policy(folder, pol)
    loaded = load_policy(folder)
    assert loaded.privacy_lock_enabled is False
    assert loaded.oversight_default_level == "supervised"


def test_save_writes_to_expected_path(folder):
    pol = FolderPolicy()
    save_policy(folder, pol)
    assert (folder / POLICY_FILENAME).is_file()


def test_corrupt_policy_falls_back_to_default(folder):
    """A malformed policy file is treated as the default — fail-safe."""
    p = folder / POLICY_FILENAME
    p.write_text("not valid json")
    pol = load_policy(folder)
    assert pol.privacy_lock_enabled is True   # default → safe
    assert pol.oversight_enabled is True


def test_non_dict_policy_falls_back_to_default(folder):
    p = folder / POLICY_FILENAME
    p.write_text(json.dumps(["not a dict"]))
    pol = load_policy(folder)
    assert pol.privacy_lock_enabled is True


# ===========================================================================
# Belt-and-braces: boolean alone is not enough to disable
# ===========================================================================


def test_disabled_boolean_without_acknowledgement_still_active(folder):
    """If someone hand-edits the JSON to set the boolean False without an
    acknowledgement, the property still reports active. The acknowledgement
    is the load-bearing record."""
    p = folder / POLICY_FILENAME
    p.write_text(json.dumps({
        "privacy_lock_enabled": False,
        "oversight_enabled": False,
        # No acknowledgements.
    }))
    pol = load_policy(folder)
    assert pol.lock_is_active is True
    assert pol.oversight_is_active is True


def test_disabled_boolean_with_acknowledgement_is_inactive(folder):
    """Both the boolean AND the acknowledgement must agree to actually disable."""
    p = folder / POLICY_FILENAME
    p.write_text(json.dumps({
        "privacy_lock_enabled": False,
        "oversight_enabled": False,
        "acknowledgements": {
            "lock_disable": {
                "accepted_at": "2026-01-01T00:00:00Z",
                "accepted_by": "alex",
                "disclaimer_version": "1",
            },
            "oversight_disable": {
                "accepted_at": "2026-01-01T00:00:00Z",
                "accepted_by": "alex",
                "disclaimer_version": "1",
            },
        },
    }))
    pol = load_policy(folder)
    assert pol.lock_is_active is False
    assert pol.oversight_is_active is False


# ===========================================================================
# disable_lock + enable_lock + audit-log integration
# ===========================================================================


def test_disable_lock_writes_policy_and_audit(folder, log_root):
    disable_lock(folder, accepted_by="alex", reason="public-data scratch",
                   log_root=log_root)
    pol = load_policy(folder)
    assert pol.privacy_lock_enabled is False
    assert pol.lock_is_active is False
    assert "lock_disable" in pol.acknowledgements
    assert pol.acknowledgements["lock_disable"].accepted_by == "alex"
    assert pol.acknowledgements["lock_disable"].reason == "public-data scratch"

    log = MutationLog(folder, log_root=log_root)
    events = [e for e in log.replay()
              if e.extra.get("policy_change") == "lock_disabled"]
    assert len(events) == 1


def test_disable_lock_requires_accepted_by(folder, log_root):
    with pytest.raises(ValueError):
        disable_lock(folder, accepted_by="", log_root=log_root)


def test_enable_lock_clears_acknowledgement(folder, log_root):
    disable_lock(folder, accepted_by="alex", log_root=log_root)
    assert load_policy(folder).lock_is_active is False

    enable_lock(folder, actor="alex", log_root=log_root)
    pol = load_policy(folder)
    assert pol.lock_is_active is True
    assert "lock_disable" not in pol.acknowledgements


def test_enable_lock_writes_audit_entry(folder, log_root):
    enable_lock(folder, actor="alex", log_root=log_root)
    log = MutationLog(folder, log_root=log_root)
    events = [e for e in log.replay()
              if e.extra.get("policy_change") == "lock_enabled"]
    assert len(events) >= 1


# ===========================================================================
# disable_oversight + enable_oversight
# ===========================================================================


def test_disable_oversight_works(folder, log_root):
    disable_oversight(folder, accepted_by="alex",
                      reason="unattended pipeline", log_root=log_root)
    pol = load_policy(folder)
    assert pol.oversight_is_active is False
    assert "oversight_disable" in pol.acknowledgements


def test_enable_oversight_clears_acknowledgement(folder, log_root):
    disable_oversight(folder, accepted_by="alex", log_root=log_root)
    enable_oversight(folder, actor="alex", log_root=log_root)
    pol = load_policy(folder)
    assert pol.oversight_is_active is True
    assert "oversight_disable" not in pol.acknowledgements


def test_disable_lock_does_not_affect_oversight(folder, log_root):
    """Disabling Lock leaves Oversight in its current state."""
    disable_lock(folder, accepted_by="alex", log_root=log_root)
    pol = load_policy(folder)
    assert pol.lock_is_active is False
    assert pol.oversight_is_active is True


def test_disable_oversight_does_not_affect_lock(folder, log_root):
    disable_oversight(folder, accepted_by="alex", log_root=log_root)
    pol = load_policy(folder)
    assert pol.oversight_is_active is False
    assert pol.lock_is_active is True


# ===========================================================================
# Folder isolation: policy in one folder doesn't affect a sibling
# ===========================================================================


def test_policy_in_one_folder_does_not_affect_sibling(tmp_path, log_root):
    hr = tmp_path / "HR"
    eng = tmp_path / "Engineering"
    hr.mkdir()
    eng.mkdir()

    disable_lock(hr, accepted_by="hr-lead", log_root=log_root)

    hr_pol = load_policy(hr)
    eng_pol = load_policy(eng)

    assert hr_pol.lock_is_active is False
    # Engineering still has full protection.
    assert eng_pol.lock_is_active is True


def test_policy_at_parent_does_not_affect_child(tmp_path, log_root):
    """The asymmetric rule holds for policy too: a policy at /acme/ does
    NOT silently disable lock in /acme/HR/. Each folder has its own
    explicit policy file."""
    acme = tmp_path / "acme"
    hr = tmp_path / "acme" / "HR"
    acme.mkdir(parents=True)
    hr.mkdir()

    disable_lock(acme, accepted_by="ceo", log_root=log_root)

    hr_pol = load_policy(hr)
    assert hr_pol.lock_is_active is True  # HR is unaffected; its own policy is default.


# ===========================================================================
# Disclaimer text is real + non-empty
# ===========================================================================


def test_lock_disclaimer_mentions_consequences():
    assert "DISABLE" in LOCK_DISCLAIMER
    assert "audit" in LOCK_DISCLAIMER.lower()
    # Should explain when NOT to disable.
    assert "GDPR" in LOCK_DISCLAIMER or "confidential" in LOCK_DISCLAIMER.lower()


# ===========================================================================
# CLI
# ===========================================================================


def test_cli_policy_show_default(folder, log_root, capsys):
    from workspaces.cli import main
    rc = main(["--log-root", str(log_root), "policy", "show",
               "--folder", str(folder)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "privacy_lock_enabled:   True" in out
    assert "lock_is_active:         True" in out


def test_cli_policy_disable_lock_requires_flag(folder, log_root, capsys):
    """Without --i-accept-the-risk, the CLI prints the disclaimer + refuses."""
    from workspaces.cli import main
    rc = main(["--log-root", str(log_root), "policy", "disable-lock",
               "--folder", str(folder)])
    assert rc == 2
    out = capsys.readouterr().out
    assert "DISABLE" in out
    # Policy file should NOT have been written.
    assert not (folder / POLICY_FILENAME).exists()


def test_cli_policy_disable_lock_with_flag(folder, log_root, capsys):
    from workspaces.cli import main
    rc = main(["--log-root", str(log_root), "policy", "disable-lock",
               "--folder", str(folder), "--i-accept-the-risk",
               "--accepted-by", "alex",
               "--reason", "public scratch"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DISABLED" in out
    pol = load_policy(folder)
    assert pol.lock_is_active is False
    assert pol.acknowledgements["lock_disable"].reason == "public scratch"


def test_cli_policy_enable_lock(folder, log_root, capsys):
    from workspaces.cli import main
    # First disable.
    disable_lock(folder, accepted_by="alex", log_root=log_root)
    # Then enable (no disclaimer required — protection direction).
    rc = main(["--log-root", str(log_root), "policy", "enable-lock",
               "--folder", str(folder)])
    assert rc == 0
    assert load_policy(folder).lock_is_active is True


def test_cli_policy_disable_oversight_requires_flag(folder, log_root, capsys):
    from workspaces.cli import main
    rc = main(["--log-root", str(log_root), "policy", "disable-oversight",
               "--folder", str(folder)])
    assert rc == 2
    out = capsys.readouterr().out
    assert "DISABLE" in out


def test_cli_policy_disable_oversight_with_flag(folder, log_root, capsys):
    from workspaces.cli import main
    rc = main(["--log-root", str(log_root), "policy", "disable-oversight",
               "--folder", str(folder), "--i-accept-the-risk",
               "--accepted-by", "alex"])
    assert rc == 0
    assert load_policy(folder).oversight_is_active is False
