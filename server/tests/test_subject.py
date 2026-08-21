# SPDX-License-Identifier: AGPL-3.0-only
"""A policy is scoped to a subject; a folder is one kind, with limited reach."""
from __future__ import annotations

import pytest

from rvnd import subject as S


def test_a_folder_cannot_reach_the_deployment_posture():
    """The hardening this split exists for.

    A `.workspace-policy.json` is a file in a user directory. If it could set
    `privacy_lock_enabled`, dropping a file into a folder would weaken the
    tool's own enforcement for that scope. Those fields belong to the
    deployment, so a folder subject cannot set them at all.
    """
    f = S.folder("/some/user/dir")
    for posture in ("privacy_lock_enabled", "oversight_enabled"):
        assert not S.may_override(f, posture), (
            f"a folder must not be able to set {posture} — it governs the "
            f"deployment, not the folder's contents")

    # These three were in the list above at first, which took a capability the
    # split had no reason to take: a folder asking for MORE restriction over its
    # own contents threatens nothing the deployment declared. They ratchet
    # instead — settable upward, refused downward (test_policy_scope_is_enforced).
    for graded in ("oversight_default_level", "lock_mode_explicit",
                   "discipline_enabled"):
        assert graded in S.RATCHETED
        assert S.weakens(graded, S.RATCHETED[graded][0], S.RATCHETED[graded][-1]), (
            f"{graded} would accept the weakest value over the strictest floor")

    # lock_confidence_threshold is per-folder BY DEFINITION (policy.py says so
    # at its declaration): it filters findings over that folder's contents.
    assert S.may_override(f, "lock_confidence_threshold")


def test_a_folder_may_decide_about_its_own_contents():
    f = S.folder("/some/user/dir")
    for own in ("ai_training_optout", "juris_packs", "access_control_enabled"):
        assert S.may_override(f, own)


def test_every_folder_scoped_field_is_about_the_folder_not_the_engine():
    """Guards the list against quiet growth: adding a posture field here would
    re-open the hole the split closes."""
    posture = {"privacy_lock_enabled", "oversight_enabled", "lock_mode_explicit",
               "oversight_default_level", "lock_confidence_threshold",
               "discipline_enabled", "local_llm", "policy_matrix"}
    assert not (S.FOLDER_SCOPED & posture), (
        f"these describe the engine, not a folder: {sorted(S.FOLDER_SCOPED & posture)}")


def test_global_sets_anything():
    g = S.global_subject()
    assert S.may_override(g, "privacy_lock_enabled")
    assert S.may_override(g, "ai_training_optout")
    assert S.may_override(g, "anything_at_all")


def test_agent_and_session_are_first_class_subjects():
    """The point of the generalisation: for a global egress tool the natural
    keys are the agent and the session, not the user's directory layout."""
    assert str(S.agent("bot-7")) == "agent:bot-7"
    assert str(S.session("sess-1")) == "session:sess-1"
    assert str(S.global_subject()) == "global"
    assert str(S.folder("/x")) == "folder:/x"


@pytest.mark.parametrize("kind,ident", [("folder", ""), ("agent", ""), ("session", "")])
def test_a_non_global_subject_without_an_id_is_refused(kind, ident):
    with pytest.raises(ValueError, match="requires an id"):
        S.Subject(kind, ident)


def test_the_global_subject_carries_no_id():
    with pytest.raises(ValueError, match="no id"):
        S.Subject(S.GLOBAL, "something")


def test_an_unknown_kind_is_refused_rather_than_silently_scoped():
    with pytest.raises(ValueError, match="unknown subject kind"):
        S.Subject("workspace", "/x")
