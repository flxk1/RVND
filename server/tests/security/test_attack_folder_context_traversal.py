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
