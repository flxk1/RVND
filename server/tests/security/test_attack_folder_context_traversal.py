# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""A6 — folder_context path traversal (J-r4).

Folder-context traversal regression.
Tier: T1 (MCP client thinks it's scoped to folder A; T3 sibling-folder
      scenario also covered).

``_enforce_allowlist`` refuses a folder_context that resolves outside the
known-workspaces registry with ``FolderContextNotAllowed``; the explicit
``WORKSPACES_ALLOW_UNREGISTERED=1`` override restores the permissive
behavior (the suite's conftest sets it globally, so enforcement tests
remove it). Tracked in docs/reviews/red-team-findings.md.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.security


def test_a6_workspace_registry_surface_exists():
    """Substrate check: ``list_known_workspaces`` exists so a future
    allowlist check has something to consult. Without this, A6 cannot be
    mitigated short of a fresh registry build."""
    from workspaces import workspace_registry

    assert hasattr(workspace_registry, "list_known_workspaces"), (
        "VULNERABILITY: workspace_registry.list_known_workspaces missing — "
        "the allowlist substrate for the A6 mitigation is not in place."
    )
    assert hasattr(workspace_registry, "add_known_workspace")
    assert hasattr(workspace_registry, "remove_known_workspace")


def test_a6_unregistered_path_resolves_only_under_override(tmp_path, monkeypatch):
    """The permissive behavior is opt-in: an unregistered folder_context
    resolves only while ``WORKSPACES_ALLOW_UNREGISTERED=1`` is set, and is
    refused the moment the override is absent."""
    from workspaces import workspace_registry
    from workspaces.folder_context import (
        FolderContextNotAllowed,
        resolve_folder_context,
    )

    monkeypatch.setattr(workspace_registry, "LOG_ROOT_DEFAULT",
                        tmp_path / "logroot")
    rogue_path = tmp_path / "unregistered_target"
    rogue_path.mkdir()

    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    resolved = resolve_folder_context(str(rogue_path))
    assert Path(resolved).resolve() == rogue_path.resolve()

    monkeypatch.delenv("WORKSPACES_ALLOW_UNREGISTERED")
    with pytest.raises(FolderContextNotAllowed):
        resolve_folder_context(str(rogue_path))


def test_a6_path_traversal_to_unregistered_sibling_is_refused(tmp_path, monkeypatch):
    """J-r4 pinned closed: a session scoped to a registered folder cannot
    reach an unregistered sibling by `..` traversal. The registry is the
    allowlist — descendants of a registered workspace pass, the sibling
    does not."""
    from workspaces import workspace_registry
    from workspaces.folder_context import (
        FolderContextNotAllowed,
        resolve_folder_context,
    )

    monkeypatch.setattr(workspace_registry, "LOG_ROOT_DEFAULT",
                        tmp_path / "logroot")
    monkeypatch.delenv("WORKSPACES_ALLOW_UNREGISTERED", raising=False)

    acme = tmp_path / "tenants" / "acme"
    competitor = tmp_path / "tenants" / "competitor"
    acme.mkdir(parents=True)
    competitor.mkdir(parents=True)
    workspace_registry.add_known_workspace(acme,
                                           log_root=tmp_path / "logroot")

    # The registered folder and its descendants resolve.
    assert Path(resolve_folder_context(str(acme))).resolve() == acme.resolve()

    # The traversal out of it is refused.
    traversal = str(acme / ".." / "competitor")
    with pytest.raises(FolderContextNotAllowed):
        resolve_folder_context(traversal)


def test_allowlist_resolution_does_not_reenter_principal_membership(
    tmp_path, monkeypatch
):
    """A verified request may resolve a registered workspace without the
    allowlist recursively trying to prove membership through MutationLog."""
    from workspaces import workspace_registry
    from workspaces.folder_context import resolve_folder_context
    from workspaces.mcp_serving import clear_request_principal, set_request_principal
    from workspaces.parties import register_party

    log_root = tmp_path / "logroot"
    folder = tmp_path / "workspace"
    folder.mkdir()
    monkeypatch.setattr(workspace_registry, "LOG_ROOT_DEFAULT", log_root)
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    register_party(str(folder), "agent", "agent", log_root=str(log_root))
    workspace_registry.add_known_workspace(folder, log_root=log_root)
    monkeypatch.delenv("WORKSPACES_ALLOW_UNREGISTERED")

    set_request_principal("local-session", "agent", rung="loopback-session")
    try:
        assert Path(resolve_folder_context(folder)).resolve() == folder.resolve()
    finally:
        clear_request_principal()


def test_a6_allowlist_blocks_unregistered_folder_context(tmp_path, monkeypatch):
    """Full mitigation: with no registered workspaces and no
    WORKSPACES_ALLOW_UNREGISTERED override, resolve must REFUSE an unknown
    folder_context with a structured error."""
    monkeypatch.delenv("WORKSPACES_ALLOW_UNREGISTERED", raising=False)

    from workspaces.folder_context import resolve_folder_context

    rogue = tmp_path / "totally_unregistered"
    rogue.mkdir()

    # When the mitigation lands, this must raise (or return a sentinel /
    # refuse-marker). Today it silently resolves.
    with pytest.raises(Exception) as exc:
        resolve_folder_context(str(rogue))
    err = str(exc.value).lower()
    assert "unregistered" in err or "allowlist" in err or "not known" in err, (
        f"resolve refused but error doesn't name the allowlist: {exc.value!r}"
    )


def test_a6_persistence_stores_enforce_workspace_allowlist(tmp_path, monkeypatch):
    """The storage sinks themselves refuse an unregistered workspace.

    Dispatch validation is defence in depth, not the sole boundary: direct
    stdio-MCP or Python call paths must not turn a folder argument into an
    arbitrary host write.
    """
    from workspaces import workspace_registry
    from workspaces.decisions.queue import DecisionQueue
    from workspaces.folder_context import FolderContextNotAllowed
    from workspaces.legal_corpus import EntityRegistry
    from workspaces.mutation_log import MutationLog
    from workspaces.rule_registry import RuleRegistry

    log_root = tmp_path / "logroot"
    monkeypatch.setattr(workspace_registry, "LOG_ROOT_DEFAULT", log_root)
    monkeypatch.delenv("WORKSPACES_ALLOW_UNREGISTERED", raising=False)
    rogue = tmp_path / "unregistered-store-target"

    constructors = (
        lambda: DecisionQueue(rogue, log_root=log_root),
        lambda: EntityRegistry(rogue, log_root=log_root),
        lambda: MutationLog(rogue, log_root=log_root),
        lambda: RuleRegistry(rogue, user_root=tmp_path / "users",
                             log_root=log_root),
    )
    for construct in constructors:
        with pytest.raises(FolderContextNotAllowed):
            construct()

    assert not rogue.exists()


def test_a6_allowlist_is_scoped_to_the_active_log_root(tmp_path, monkeypatch):
    """The allowlist is read from the registry co-located with the log root the
    operation runs under — not always the default log root.

    Regression: ``_enforce_allowlist`` called ``load_registry()`` with no
    ``log_root``, so it always read ``<LOG_ROOT_DEFAULT>/known-workspaces.json``
    even when the operation ran under a custom ``--log-root``. Two consequences,
    both pinned below: a folder registered under the custom root was invisible
    to enforcement (legitimate op refused), and a folder registered only under
    the default root leaked through when operating under a different root. The
    prior tests never caught it because they monkeypatch ``LOG_ROOT_DEFAULT`` to
    equal the ``log_root`` they register under; here the two roots differ.
    """
    from workspaces import workspace_registry
    from workspaces.folder_context import (
        FolderContextNotAllowed,
        resolve_folder_context,
    )
    from workspaces.mutation_log import MutationLog

    default_root = tmp_path / "default-logroot"
    custom_root = tmp_path / "custom-logroot"
    monkeypatch.setattr(workspace_registry, "LOG_ROOT_DEFAULT", default_root)
    monkeypatch.delenv("WORKSPACES_ALLOW_UNREGISTERED", raising=False)

    under_custom = tmp_path / "ws-registered-under-custom"
    under_default = tmp_path / "ws-registered-under-default"
    under_custom.mkdir()
    under_default.mkdir()
    workspace_registry.add_known_workspace(under_custom, log_root=custom_root)
    workspace_registry.add_known_workspace(under_default, log_root=default_root)

    # (1) Registered under the CUSTOM root → honoured when the op runs under it.
    # This is the reported bug: pre-fix, enforcement read the default registry
    # and refused this legitimate folder.
    assert Path(resolve_folder_context(
        str(under_custom), log_root=custom_root)).resolve() == under_custom.resolve()
    # The authoritative construction path threads its own log_root and agrees.
    MutationLog(under_custom, log_root=custom_root)  # must not raise

    # (2) Registered ONLY under the default root → NOT consulted under the custom
    # root. Pre-fix this leaked through (default entry honoured everywhere).
    with pytest.raises(FolderContextNotAllowed):
        resolve_folder_context(str(under_default), log_root=custom_root)

    # (3) Symmetry: under the default root, the default-registered folder passes
    # and the custom-registered one is refused.
    assert Path(resolve_folder_context(
        str(under_default), log_root=default_root)).resolve() == under_default.resolve()
    with pytest.raises(FolderContextNotAllowed):
        resolve_folder_context(str(under_custom), log_root=default_root)


def test_a6_documented_gap_is_in_red_team_findings():
    """Meta-test: ensure the gap stays surfaced in
    docs/reviews/red-team-findings.md so the historical finding and mitigation
    remain auditable."""
    # security → tests → runtime → repo root; the register ships in docs/.
    repo_root = Path(__file__).resolve().parents[3]
    findings_doc = repo_root / "docs" / "reviews" / "red-team-findings.md"
    assert findings_doc.is_file(), (
        f"docs/reviews/red-team-findings.md missing — A6 gap has no durable home. "
        f"expected at: {findings_doc}"
    )
    body = findings_doc.read_text(encoding="utf-8")
    assert "A6" in body, (
        "docs/reviews/red-team-findings.md exists but does not mention A6. "
        "The traversal gap must stay surfaced."
    )
