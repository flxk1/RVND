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

from pathlib import Path

import pytest


pytestmark = pytest.mark.security


def _patch_default_log_root(monkeypatch, root):
    """Redirect the DEFAULT registry root — and PROVE the redirect bites.

    ``LOG_ROOT_DEFAULT`` used to be a global of RVND's own
    ``workspaces.workspace_registry``, right next to the ``_registry_path`` that
    read it, so ``monkeypatch.setattr(workspace_registry, "LOG_ROOT_DEFAULT", …)``
    worked by construction. The workspace concept has since been retired onto
    ``loomground-workspace``: ``_registry_path`` lives upstream now and reads
    *that* module's global, while RVND's module is a zero-definition shim whose
    ``LOG_ROOT_DEFAULT`` is a re-exported COPY.

    Patching the copy alone would bind a value nothing reads, and every
    enforcement assertion in this module would then run against the operator's
    real ``~/.workspace/log`` registry instead of ``tmp_path`` — passing
    vacuously, which is the worst outcome available to a security test. So this
    patches the definition site, keeps RVND's re-exported copy honest for
    anyone reading it, and asserts the reader actually moved.
    """
    from workspaces import workspace_registry
    from workspaces.adapters.workspace import _registry as _upstream_registry

    monkeypatch.setattr(_upstream_registry, "LOG_ROOT_DEFAULT", root)
    monkeypatch.setattr(workspace_registry, "LOG_ROOT_DEFAULT", root)
    assert workspace_registry._registry_path() == Path(root) / "known-workspaces.json", (
        "LOG_ROOT_DEFAULT patch no longer reaches the code that reads it — the "
        "A6 enforcement assertions below would pass vacuously against the real "
        "registry")
    return root


def test_a6_workspace_registry_surface_exists():
    """Substrate check: ``list_known_workspaces`` exists so a future
    allowlist check has something to consult. Without this, A6 cannot be
    mitigated short of a fresh registry build."""
    from rvnd import workspace_registry

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
    from rvnd import workspace_registry
    from rvnd.folder_context import (
        FolderContextNotAllowed,
        resolve_folder_context,
    )

    _patch_default_log_root(monkeypatch, tmp_path / "logroot")
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
    from rvnd import workspace_registry
    from rvnd.folder_context import (
        FolderContextNotAllowed,
        resolve_folder_context,
    )

    _patch_default_log_root(monkeypatch, tmp_path / "logroot")
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
    from rvnd import workspace_registry
    from rvnd.folder_context import resolve_folder_context
    from rvnd.mcp_serving import clear_request_principal, set_request_principal
    from rvnd.parties import register_party

    log_root = tmp_path / "logroot"
    folder = tmp_path / "workspace"
    folder.mkdir()
    _patch_default_log_root(monkeypatch, log_root)
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

    from rvnd.folder_context import resolve_folder_context

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
    from rvnd import workspace_registry
    from rvnd.decisions.queue import DecisionQueue
    from rvnd.folder_context import FolderContextNotAllowed
    from rvnd.legal_corpus import EntityRegistry
    from rvnd.mutation_log import MutationLog
    from rvnd.rule_registry import RuleRegistry

    log_root = tmp_path / "logroot"
    _patch_default_log_root(monkeypatch, log_root)
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
    from rvnd import workspace_registry
    from rvnd.folder_context import (
        FolderContextNotAllowed,
        resolve_folder_context,
    )
    from rvnd.mutation_log import MutationLog

    default_root = tmp_path / "default-logroot"
    custom_root = tmp_path / "custom-logroot"
    _patch_default_log_root(monkeypatch, default_root)
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


# ---------------------------------------------------------------------------
# The bridge's trusted-path allowlist stays principal-scoped after the
# workspace concept was retired onto loomground-workspace.
# ---------------------------------------------------------------------------
# ``app/serve.py`` turns an attacker-supplied ``folder`` into a ``trusted`` path
# by looking it up in ``list_known_workspaces(log_root=…)`` — note: with NO
# scope argument — and that lookup sits directly upstream of the proxy-proof and
# session-token checks that authorize egress. RVND's retired copy of
# ``list_known_workspaces`` reached into ``mcp_serving`` for the request
# principal itself, so the bridge was scoped whether or not its author knew.
# The extracted package deliberately does not: access control is the host's
# business, and its ``list_known_workspaces`` returns the WHOLE registry unless
# a ``scope=`` filter is injected. RVND injects it inside
# ``adapters/workspace.py`` so the default is fail-closed and no call site can
# widen visibility by omission.
#
# If that default were ever lost, nothing would raise and nothing would be
# logged — the bridge would simply start accepting a sibling tenant's folder as
# "a registered workspace". These assert the emptiness, not the call.


def _bridge_allowlist(log_root):
    """The bridge's line, verbatim: the same import, the same call shape, no
    ``scope=`` argument. If this stops being scoped, so does ``app/serve.py``."""
    from workspaces.workspace_registry import list_known_workspaces

    return list_known_workspaces(log_root=str(log_root))


def _register_two_tenants(tmp_path, monkeypatch):
    from workspaces import workspace_registry
    from workspaces.parties import register_party

    log_root = _patch_default_log_root(monkeypatch, tmp_path / "logroot")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")

    acme = tmp_path / "tenants" / "acme"
    globex = tmp_path / "tenants" / "globex"
    acme.mkdir(parents=True)
    globex.mkdir(parents=True)
    for folder in (acme, globex):
        workspace_registry.add_known_workspace(folder, log_root=log_root)
    register_party(str(acme), "acme-agent", "agent", log_root=str(log_root))
    register_party(str(globex), "globex-agent", "agent",
                   log_root=str(log_root))
    monkeypatch.delenv("WORKSPACES_ALLOW_UNREGISTERED")
    return log_root, acme, globex


def test_bridge_allowlist_is_empty_for_a_principal_that_matches_nothing(
    tmp_path, monkeypatch
):
    """THE acceptance criterion for the workspace retirement.

    A request principal who is an active party of no registered workspace must
    see an EMPTY allowlist — never the full registry — through the exact call
    ``app/serve.py`` makes. The control assertion (no principal → both
    workspaces) is what stops this passing for the wrong reason: a filter that
    returns nothing to everybody would satisfy the emptiness alone.
    """
    from workspaces.mcp_serving import (
        clear_request_principal,
        set_request_principal,
    )

    log_root, acme, globex = _register_two_tenants(tmp_path, monkeypatch)

    # control: local single-operator mode (no request principal) sees both.
    assert len(_bridge_allowlist(log_root)) == 2

    set_request_principal("intruder", "intruder", rung="proxy-verified")
    try:
        allowlist = _bridge_allowlist(log_root)
    finally:
        clear_request_principal()

    assert allowlist == [], (
        "EGRESS LOCK REGRESSION: the bridge's trusted-path allowlist returned "
        f"{[w.get('path') for w in allowlist]} to a principal who is a member "
        "of no workspace. app/serve.py would accept a foreign folder as "
        "'a registered workspace' before the proxy-proof and session-token "
        "checks run. The per-principal scope default in "
        "adapters/workspace.py is missing or bypassed.")


def test_bridge_allowlist_shows_a_principal_only_its_own_workspace(
    tmp_path, monkeypatch
):
    """The other half of fail-closed: a principal who IS a member sees exactly
    its own workspace, and the sibling tenant's folder never becomes
    ``trusted``. The final assertion is ``app/serve.py``'s own trusted-path
    expression, run over the scoped list."""
    import os as _os

    from workspaces.mcp_serving import (
        clear_request_principal,
        set_request_principal,
    )

    log_root, acme, globex = _register_two_tenants(tmp_path, monkeypatch)

    set_request_principal("acme-agent", "acme-agent", rung="proxy-verified")
    try:
        allowlist = _bridge_allowlist(log_root)
    finally:
        clear_request_principal()

    assert [w.get("path") for w in allowlist] == [str(acme.resolve())]

    # app/serve.py's trusted-path lookup, over the scoped list: the sibling
    # tenant's folder must not resolve to a trusted path.
    want = _os.path.realpath(str(globex))
    trusted = next((w.get("path") for w in allowlist
                    if _os.path.realpath(w.get("path", "")) == want), None)
    assert trusted is None, (
        "EGRESS LOCK REGRESSION: a sibling tenant's folder resolved to a "
        "trusted path for a principal that is not a member of it")


def test_workspace_seam_scope_default_is_not_reachable_around(tmp_path,
                                                              monkeypatch):
    """The scoping must be a property of the SEAM, not of each caller.

    Two claims: (1) the name RVND's call sites import is the seam's wrapper,
    not the package's unscoped function; and (2) the package's own function,
    called without a filter, really does return everything — so the wrapper is
    doing load-bearing work rather than decorating a difference that is not
    there.
    """
    import loomground_workspace as lw
    from workspaces import workspace_registry
    from workspaces.adapters import workspace as seam
    from workspaces.mcp_serving import (
        clear_request_principal,
        set_request_principal,
    )

    log_root, acme, globex = _register_two_tenants(tmp_path, monkeypatch)

    assert workspace_registry.list_known_workspaces is seam.list_known_workspaces
    assert workspace_registry.list_known_workspaces is not lw.list_known_workspaces

    set_request_principal("intruder", "intruder", rung="proxy-verified")
    try:
        assert seam.list_known_workspaces(log_root=log_root) == []
        # the unscoped upstream function, for contrast — this is what a second
        # importer of loomground_workspace would get, and why
        # test_consumed_modules pins the seam as the only import site.
        unscoped = lw.workspace_registry.list_known_workspaces(log_root)
    finally:
        clear_request_principal()
    assert len(unscoped) == 2, (
        "the upstream function no longer returns the full registry, so this "
        "test can no longer tell a working scope default from a missing one")


def test_console_workspace_list_is_empty_for_a_principal_that_matches_nothing(
    tmp_path, monkeypatch
):
    """The other call site that hurts: the console's workspace list.

    ``mcp_server.list_known_workspaces`` is a different function that shadows
    the registry read's name in that module; it consumes the scoped read
    lazily and blanks the ``default`` pointer to match, so it never names a
    workspace the caller cannot see. Both properties are asserted, because the
    ``default`` pointer is read from the RAW registry and would otherwise leak
    a path on its own.
    """
    from workspaces import mcp_server
    from workspaces.mcp_serving import (
        clear_request_principal,
        set_request_principal,
    )

    log_root, acme, globex = _register_two_tenants(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))

    # control: no request principal -> the console sees both.
    assert len(mcp_server.list_known_workspaces()["workspaces"]) == 2

    set_request_principal("intruder", "intruder", rung="proxy-verified")
    try:
        out = mcp_server.list_known_workspaces()
    finally:
        clear_request_principal()

    assert out["workspaces"] == [], (
        f"console workspace list leaked {out['workspaces']} to a principal "
        "who is a member of nothing")
    assert out["default"] == "", (
        "the default pointer named a workspace the caller cannot see")


def test_audit_discovery_scan_is_empty_for_a_principal_that_matches_nothing(
    tmp_path, monkeypatch
):
    """The third unscoped-shaped call site: the cross-workspace audit scan.

    ``mcp_impl.get_audit_event`` without a ``folder_context`` walks EVERY known
    workspace looking for an ``audit_id``. Unscoped, that is a cross-tenant
    existence oracle — and a read of a sibling tenant's event. It consumes the
    same scoped registry read as the bridge and the console, with the same
    no-scope-argument call shape, so it is pinned here alongside them.
    """
    from workspaces import mcp_impl
    from workspaces.mcp_serving import (
        clear_request_principal,
        set_request_principal,
    )
    from workspaces.mutation_log import LogEvent, MutationLog

    log_root, acme, globex = _register_two_tenants(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log_root))

    # An event that exists only in globex's log.
    log = MutationLog(globex, log_root=log_root)
    event = LogEvent(event="ingest", folder_path=str(globex), pair_id="p1")
    log.append(event)
    audit_id = event.audit_id

    # control: no request principal -> the scan finds it.
    found = mcp_impl.get_audit_event(audit_id)
    assert found.get("ok") is True, found

    set_request_principal("acme-agent", "acme-agent", rung="proxy-verified")
    try:
        scoped = mcp_impl.get_audit_event(audit_id)
    finally:
        clear_request_principal()

    assert scoped.get("ok") is False, (
        "the cross-workspace audit scan reached a workspace the principal is "
        f"not a member of: {scoped}")
    assert str(globex) not in repr(scoped), (
        "the refusal leaked the sibling tenant's workspace path")
