# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""What a policy is scoped to.

RVND is a policy-based oversight tool, not a workspace tool. Its enforcement
path already proves this: ``lock_text`` takes no folder at all, ``gate_for_cloud``
takes one optionally, and an ``EgressProxy`` with ``track_folder=None`` gates
every request it sees. A folder was never the boundary being enforced -- it was
the key used to look up *which policy applies*, and for a global egress tool
that is the wrong key. "Which directory is this call filed under" is a question
about the user's filing, not about the traffic.

So the key generalises to a SUBJECT. A folder is one kind of subject, and it is
effective only for protections that are genuinely about a folder's contents.
Everything else belongs to the deployment, and no folder can reach it.

That last part is a hardening, not just a tidy-up. Today a ``.workspace-policy``
file dropped into a directory can carry ``privacy_lock_enabled`` -- a folder
weakening the tool's own enforcement posture. Under this split it cannot: those
fields are UNIVERSAL and only the global subject sets them. The same confusion
is what let a process-global fact (the enforcement posture, read from
``os.environ``) be read as a per-workspace one.

Resolving a folder subject's identity is the workspace layer's job, not this
one's -- see ``loomground-workspace``. This module holds only the key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

GLOBAL: Final = "global"
FOLDER: Final = "folder"
AGENT: Final = "agent"
SESSION: Final = "session"

KINDS: Final[frozenset[str]] = frozenset({GLOBAL, FOLDER, AGENT, SESSION})


@dataclass(frozen=True)
class Subject:
    """What a policy applies to. ``id`` is empty for :data:`GLOBAL` only."""

    kind: str
    id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown subject kind {self.kind!r}; expected one of {sorted(KINDS)}")
        if self.kind == GLOBAL and self.id:
            raise ValueError("the global subject carries no id")
        if self.kind != GLOBAL and not self.id:
            raise ValueError(f"a {self.kind} subject requires an id")

    def __str__(self) -> str:
        return self.kind if self.kind == GLOBAL else f"{self.kind}:{self.id}"


def global_subject() -> Subject:
    """The deployment itself — the scope every other subject refines."""
    return Subject(GLOBAL)


def folder(path: str) -> Subject:
    """A user folder. Its identity is resolved by the workspace layer, not here."""
    return Subject(FOLDER, str(path))


def agent(uid: str) -> Subject:
    return Subject(AGENT, str(uid))


def session(sid: str) -> Subject:
    return Subject(SESSION, str(sid))


# --------------------------------------------------------------------------
# Which policy fields a non-global subject may override.
#
# Conservative by construction: a field is listed here only when it is about
# the SUBJECT'S OWN CONTENT. Anything describing how this deployment enforces
# stays universal, so a subject can never relax the posture that governs it.
# Adding a field here widens what a folder can decide about itself; that is a
# product decision and should be argued in review, not defaulted into.
# --------------------------------------------------------------------------
FOLDER_SCOPED: Final[frozenset[str]] = frozenset({
    "ai_training_optout",      # may this folder's data train a model
    "juris_packs",             # which jurisdictions this folder's matter sits in
    "access_control_enabled",  # who may reach this folder
    "cost_cap_cents",          # spend budget for work on this folder
    "tdm_declaration",         # text-and-data-mining reservation over its content
})


#: What the DEPLOYMENT owns outright: whether a protection runs at all. A folder
#: value here could only ever WEAKEN it -- there is no stricter direction for a
#: boolean that is already on -- and RVND enforces where there is no folder to
#: consult. Everything else stays the subject's, so a new FolderPolicy field
#: keeps working with no edit here.
DEPLOYMENT_OWNED: Final[frozenset[str]] = frozenset({
    "privacy_lock_enabled",       # is the Lock on at all
    "oversight_enabled",          # is human oversight on at all
})

#: Graded fields a folder may still set, but only toward MORE restriction. The
#: deployment sets a floor; a folder may stand above it and not below. Taking
#: these outright would have removed a capability the split never needed to
#: remove: a folder asking for stricter handling of ITS contents threatens
#: nothing the deployment declared.
#:
#: Each entry maps the field to its strictness order, weakest first. A value not
#: in the order (an unset "" or None) means "the folder said nothing".
RATCHETED: Final[dict[str, tuple]] = {
    # off < clean_room < clean_room_with_algo
    "lock_mode_explicit": ("off", "clean_room", "clean_room_with_algo"),
    # autonomous < notify < review < approve < supervised < manual
    "oversight_default_level": ("autonomous", "notify", "review", "approve",
                                "supervised", "manual"),
    # a folder may turn its own discipline gate ON, never off
    "discipline_enabled": (False, True),
}


def strictest(field_name: str, folder_value, deployment_value):
    """The winning value for a ratcheted field: whichever is more restrictive.

    An unrecognised or unset folder value contributes nothing rather than
    winning by accident -- an unknown string must not be able to outrank the
    deployment's floor simply because it was not in the table.
    """
    order = RATCHETED[field_name]
    try:
        f_rank = order.index(folder_value)
    except ValueError:
        return deployment_value
    try:
        d_rank = order.index(deployment_value)
    except ValueError:
        d_rank = -1
    return folder_value if f_rank > d_rank else deployment_value


def weakens(field_name: str, folder_value, deployment_value) -> bool:
    """Would setting ``folder_value`` ask for LESS restriction than the floor?

    Setters call this so a weakening write is refused at the door, instead of
    being accepted, saved, and then quietly overruled by the resolver -- the
    caller being told it worked is the defect, not the resolver's correctness.
    """
    if field_name not in RATCHETED:
        return False
    return strictest(field_name, folder_value, deployment_value) != folder_value


#: Acknowledgements that opt OUT of a deployment-owned protection travel with it.
#: An acknowledgement is half of a decision — `lock_is_active` is the boolean AND
#: the absence of an opt-out — so leaving these with the folder would let a
#: folder's acknowledgement apply to the deployment's switch.
DEPLOYMENT_OWNED_ACKS: Final[frozenset[str]] = frozenset({
    "lock_disable", "lock_mode_change_to_off", "oversight_disable",
})


def may_override(subject: Subject, field_name: str) -> bool:
    """May ``subject`` set ``field_name``, or is it the deployment's to set?

    The global subject sets anything. Every other subject is limited to the
    fields that describe itself.
    """
    if subject.kind == GLOBAL:
        return True
    return field_name not in DEPLOYMENT_OWNED
