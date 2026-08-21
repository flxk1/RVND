# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND adapter seam over loomground-workspace — what a workspace *is*.

The workspaces boundary rule confines every direct import of an upstream
Loomground package to the ``adapters/`` seam (see
``tests/test_adapter_boundary.py``). This module is that seam for the
**workspace concept**: how a folder is scoped (``folder_context``), how it is
identified (``folder_hash`` / ``legacy_folder_hash``), how it is registered
(``workspace_registry``), and where it keeps what it accumulates (``paths``).

RVND used to carry its own copies of all four. They are retired here: the
package is a byte-faithful extraction, so this is a wiring job, not a port.
``folder_context.py``, ``workspace_registry.py`` and ``_storage_paths.py``
remain as zero-definition shims re-exporting from this seam, because ~60 import
sites across ``server/src``, ``app/`` and the test suite address those module
paths.

This module does two things:

1. **re-exports** the package's public surface unchanged — including the
   private names RVND's callers and tests touch
   (``_resolve_with_symlink_policy``, ``_enforce_allowlist``, the
   ``_SYMLINK_MODE_*`` constants, ``_registry_path``); and

2. **wires RVND's one provider into the package's one injected port** — the
   per-principal read scope on ``list_known_workspaces``.

**The scope default is the load-bearing decision in this seam.** Upstream took
the access-control policy OUT of the concept: its ``list_known_workspaces``
returns the whole registry unless the host passes a ``scope=`` filter. RVND's
retired copy reached into ``mcp_serving`` itself, so all of its call sites were
scoped whether or not their author knew it — including ``app/serve.py``, where
the bridge uses the list as its trusted-path allowlist immediately upstream of
the proxy-proof and session-token checks that authorize egress. Pushing
``scope=`` out to those call sites would make a cross-tenant disclosure one
forgotten keyword argument away, with no error and no log line. So the filter is
defaulted HERE: a caller gets today's fail-closed behaviour by default and has
to pass ``scope=`` explicitly to get anything else.

``tests/test_consumed_modules.py`` enforces that this file is the only importer
of ``loomground_workspace`` anywhere in ``server/src`` or ``app/``, so nothing
can reach the unscoped upstream function and bypass the default.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import loomground_workspace as _lw
from loomground_workspace import folder_context as _folder_context
from loomground_workspace import identity as _identity
from loomground_workspace import paths as _paths
from loomground_workspace import workspace_registry as _registry

# -- plain re-exports: the plane's public surface, unchanged ------------------

# scope
from loomground_workspace.folder_context import (  # noqa: F401
    ALLOW_UNREGISTERED_ENV,
    UNSCOPED_SENTINEL,
    FolderContextNotAllowed,
    NoFolderContextError,
    current_folder,
    folder_context,
    reset_folder,
    resolve_folder_context,
    set_folder,
    symlink_mode,
    with_folder_context,
)
from loomground_workspace.folder_context import (  # noqa: F401
    _ENV_VAR,
    _SYMLINK_MODE_ENV,
    _SYMLINK_MODE_FOLLOW,
    _SYMLINK_MODE_ISOLATE,
    _SYMLINK_MODES,
    _enforce_allowlist,
    _path_is_within,
    _resolve_with_symlink_policy,
)

# identity
from loomground_workspace.identity import (  # noqa: F401
    _filesystem_is_case_insensitive,
    folder_hash,
    legacy_folder_hash,
)

# registration — every name EXCEPT list_known_workspaces, which is wrapped below
from loomground_workspace.workspace_registry import (  # noqa: F401
    REGISTRY_FILE,
    REGISTRY_VERSION,
    _registry_path,
    _resolved,
    _save_registry,
    add_known_workspace,
    bootstrap_default_workspace,
    load_registry,
    remove_known_workspace,
)

# defaults
from loomground_workspace.paths import (  # noqa: F401
    DEFAULT_WORKSPACE_DIR,
    LOG_ROOT_DEFAULT,
)


# -- the one wired port: RVND's per-principal read scope ----------------------

def _principal_scope(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """RVND's registry read filter — reproduces the retired copy exactly.

    Under an active request principal (a proxy-fronted bridge request) the list
    is scoped server-side to the workspaces where the principal is a registered
    active party. Fail-closed: a principal matched nowhere gets an empty list,
    never the full registry. Without a request principal (local single-operator
    mode) the full list is returned unchanged.

    Taken from ``principal``, which is a leaf: it imports no first-party module
    at module scope. The retired copy reached these through ``mcp_serving`` and
    had to do it lazily, because ``mcp_serving`` transitively imports most of
    RVND including ``mutation_log``, which imports this module. That cycle is
    gone, so the import is plain.
    """
    from ..principal import get_request_principal, principal_workspace_member

    ctx = get_request_principal()
    if ctx is None:
        return rows
    principal = ctx.get("principal") or ""
    return [w for w in rows
            if principal_workspace_member(principal, w.get("path", ""))]


def list_known_workspaces(
    log_root: Optional[Any] = None,
    *,
    scope: Optional[Callable[[list[dict[str, Any]]], list[dict[str, Any]]]] = None,
) -> list[dict[str, Any]]:
    """RVND's registry read. Sorted by ``added_at`` ascending.

    The per-principal filter is the DEFAULT, not an opt-in, so no call site can
    silently widen visibility by forgetting it — see this module's docstring for
    why that matters at ``app/serve.py``'s trusted-path allowlist. Pass
    ``scope=`` only to override deliberately; there is no way to ask for "no
    filter" by omission.

    NOTE FOR THE NEXT READER: this is *not* the registry read that
    ``folder_context._enforce_allowlist`` uses, and it must never become it.
    The allowlist reads the RAW registry (``load_registry``) because the scope
    filter calls ``principal_workspace_member`` -> ``parties.list_parties`` ->
    ``MutationLog(...)`` -> ``resolve_folder_context`` -> back into
    ``_enforce_allowlist``: infinite recursion on every authenticated request.
    """
    return _registry.list_known_workspaces(
        log_root, scope=_principal_scope if scope is None else scope)


__all__ = [
    # scope
    "NoFolderContextError", "FolderContextNotAllowed", "UNSCOPED_SENTINEL",
    "ALLOW_UNREGISTERED_ENV", "current_folder", "set_folder", "reset_folder",
    "folder_context", "with_folder_context", "resolve_folder_context",
    "symlink_mode", "_resolve_with_symlink_policy", "_enforce_allowlist",
    "_path_is_within", "_ENV_VAR", "_SYMLINK_MODE_ENV", "_SYMLINK_MODE_FOLLOW",
    "_SYMLINK_MODE_ISOLATE", "_SYMLINK_MODES",
    # identity
    "folder_hash", "legacy_folder_hash", "_filesystem_is_case_insensitive",
    # registration
    "load_registry", "add_known_workspace", "remove_known_workspace",
    "list_known_workspaces", "bootstrap_default_workspace",
    "REGISTRY_FILE", "REGISTRY_VERSION", "_registry_path", "_resolved",
    "_save_registry",
    # defaults
    "LOG_ROOT_DEFAULT", "DEFAULT_WORKSPACE_DIR",
    # upstream modules, for callers that need module-attribute access
    "_lw", "_folder_context", "_identity", "_paths", "_registry",
]
