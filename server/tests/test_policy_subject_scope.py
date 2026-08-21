# SPDX-License-Identifier: AGPL-3.0-only
"""Policy is scoped to a subject; a folder decides only about itself.

RVND governs egress with no folder at all, so its enforcement posture cannot be
something a user's directory sets. `resolve_policy` takes the deployment's
policy as the base and lets a folder override only the fields that describe its
own contents.
"""
from __future__ import annotations

import json

import pytest

from rvnd import subject as S
from rvnd.policy import (DEPLOYMENT_POLICY_FILENAME, FolderPolicy,
                         deployment_policy, resolve_policy, save_policy)


@pytest.fixture
def deployed(tmp_path):
    """A deployment that has turned its Lock ON, and a folder that says OFF."""
    log_root = tmp_path / "logroot"
    log_root.mkdir()
    (log_root / DEPLOYMENT_POLICY_FILENAME).write_text(json.dumps({
        "privacy_lock_enabled": True,
        "oversight_default_level": "approve",
        "ai_training_optout": False,
    }), encoding="utf-8")
    folder = tmp_path / "ws"
    folder.mkdir()
    save_policy(folder, FolderPolicy(
        privacy_lock_enabled=False,        # the folder tries to switch it off
        oversight_default_level="autonomous",
        ai_training_optout=True,           # this one IS the folder's to decide
    ))
    return log_root, folder


def test_a_folder_cannot_switch_off_the_deployments_lock(deployed):
    log_root, folder = deployed
    eff = resolve_policy(S.folder(str(folder)), log_root=log_root)
    assert eff.privacy_lock_enabled is True, (
        "a .workspace-policy.json in a user directory switched off the "
        "deployment's Privacy Lock — the posture is the deployment's, and a "
        "folder must not be able to reach it")
    assert eff.oversight_default_level == "approve", (
        "a folder lowered the deployment's oversight level to autonomous")


def test_a_folder_does_decide_about_its_own_contents(deployed):
    log_root, folder = deployed
    eff = resolve_policy(S.folder(str(folder)), log_root=log_root)
    assert eff.ai_training_optout is True, (
        "ai_training_optout is about THIS folder's data; the folder must win it")


def test_the_global_subject_is_the_deployment_untouched(deployed):
    log_root, _folder = deployed
    assert resolve_policy(S.global_subject(), log_root=log_root).to_dict() == \
        deployment_policy(log_root).to_dict()


def test_agent_and_session_subjects_inherit_rather_than_borrow_a_folder(deployed):
    """They have no store yet. Inheriting the deployment is the safe answer;
    silently falling back to some folder's policy would not be."""
    log_root, _ = deployed
    base = deployment_policy(log_root).to_dict()
    for subj in (S.agent("bot-7"), S.session("sess-1")):
        assert resolve_policy(subj, log_root=log_root).to_dict() == base


def test_a_folder_with_no_policy_file_contributes_nothing(tmp_path):
    log_root = tmp_path / "lr"; log_root.mkdir()
    empty = tmp_path / "empty"; empty.mkdir()
    assert resolve_policy(S.folder(str(empty)), log_root=log_root).to_dict() == \
        deployment_policy(log_root).to_dict()


def test_a_corrupt_deployment_policy_fails_safe_to_full_protection(tmp_path):
    log_root = tmp_path / "lr"; log_root.mkdir()
    (log_root / DEPLOYMENT_POLICY_FILENAME).write_text("{not json", encoding="utf-8")
    assert deployment_policy(log_root).privacy_lock_enabled is True, (
        "a corrupt deployment policy must fail safe to full protection, never "
        "to an open posture")


def test_the_deployment_posture_is_settable(tmp_path):
    """Moving a decision out of the folder has to put it somewhere.

    The folder route no longer reaches the posture, so if nothing could write a
    deployment policy, an operator who needs the Lock off would have no
    supported path at all — the split would remove a capability rather than
    relocate it.
    """
    from rvnd.policy import save_deployment_policy

    log_root = tmp_path / "lr"
    assert deployment_policy(log_root).privacy_lock_enabled is True

    written = save_deployment_policy(FolderPolicy(privacy_lock_enabled=False), log_root)
    assert written.exists()
    assert deployment_policy(log_root).privacy_lock_enabled is False, (
        "the deployment set its own posture and it did not take effect")


def test_a_folder_still_cannot_widen_a_deployment_that_is_locked_down(tmp_path):
    """The direction that matters: deployment ON, folder asks OFF, ON wins."""
    from rvnd.policy import save_deployment_policy

    log_root = tmp_path / "lr"
    save_deployment_policy(FolderPolicy(privacy_lock_enabled=True), log_root)
    folder = tmp_path / "ws"; folder.mkdir()
    save_policy(folder, FolderPolicy(privacy_lock_enabled=False))
    assert resolve_policy(S.folder(str(folder)), log_root=log_root).privacy_lock_enabled is True


def test_enforcement_reads_the_deployment_posture_not_the_folders(tmp_path, monkeypatch):
    """The wiring, not just the resolver.

    `_folder_lock_on` / `_folder_lock_mode` decide whether the Lock applies. They
    used to read the FOLDER's policy, so a directory that had acknowledged and
    disabled its Lock switched enforcement off for itself. Under the split the
    posture is the deployment's; the folder's own declaration is still recorded
    and still readable, it just no longer decides this.
    """
    import rvnd.mcp_serving as MS
    import rvnd.policy as P
    from rvnd.workspace_registry import add_known_workspace

    log_root = tmp_path / "lr"; log_root.mkdir()
    ws = tmp_path / "ws"; ws.mkdir()
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    add_known_workspace(str(ws), log_root=log_root)

    P.disable_lock(ws, accepted_by="alex", reason="test", log_root=log_root)
    assert P.load_policy(ws).lock_is_active is False, "fixture must really disable it"

    assert MS._folder_lock_on(str(ws)) is True, (
        "a folder switched off enforcement for itself — the Lock posture is the "
        "deployment's")
    assert MS._folder_lock_mode(str(ws)) != "off"

    # The deployment CAN still turn it off — the capability moved, not vanished.
    # Note it needs the acknowledgement too: a flipped boolean alone never
    # disables a protection, at either level. That safeguard survives the move.
    P.save_deployment_policy(P.FolderPolicy(privacy_lock_enabled=False), log_root)
    assert MS._folder_lock_on(str(ws)) is True, (
        "an un-acknowledged boolean disabled the Lock — the acknowledgement "
        "requirement must apply to the deployment policy as well")

    P.disable_lock_for_deployment(accepted_by="alex", reason="test", log_root=log_root)
    assert MS._folder_lock_on(str(ws)) is False
