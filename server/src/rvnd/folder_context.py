# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim — workspace scope now lives in ``loomground-workspace``.

What a workspace is — how a folder path is scoped, and whether a symlink is a
boundary — is not RVND's to define. It moved to
``loomground_workspace.folder_context`` and RVND consumes it through
``adapters.workspace``. This module holds ZERO definitions of its own; it exists
so the ~15 call sites in ``server/src``, ``app/serve.py``, the CLI and the test
suite that address ``workspaces.folder_context`` keep working unchanged.

``tests/test_no_parallel_structures.py`` asserts this file re-grows no ``def``
or ``class`` of its own, so the retired copy cannot come back.

The behaviour is identical, including the parts that are easy to lose:

* the three-way priority (explicit argument, then contextvar, then
  ``WORKSPACE_FOLDER_CONTEXT``) and the raise-rather-than-guess default;
* ``WORKSPACE_SYMLINK_MODE`` (``follow`` / ``isolate``);
* the A6 allowlist — ``_enforce_allowlist`` reads the RAW registry
  (``load_registry``), never the principal-scoped list, or every authenticated
  request recurses through ``parties`` -> ``MutationLog`` -> back here.
"""

from __future__ import annotations

from .adapters.workspace import (  # noqa: F401
    ALLOW_UNREGISTERED_ENV,
    UNSCOPED_SENTINEL,
    FolderContextNotAllowed,
    NoFolderContextError,
    _ENV_VAR,
    _SYMLINK_MODE_ENV,
    _SYMLINK_MODE_FOLLOW,
    _SYMLINK_MODE_ISOLATE,
    _SYMLINK_MODES,
    _enforce_allowlist,
    _path_is_within,
    _resolve_with_symlink_policy,
    current_folder,
    folder_context,
    reset_folder,
    resolve_folder_context,
    set_folder,
    symlink_mode,
    with_folder_context,
)

__all__ = [
    "NoFolderContextError",
    "FolderContextNotAllowed",
    "UNSCOPED_SENTINEL",
    "ALLOW_UNREGISTERED_ENV",
    "current_folder",
    "set_folder",
    "reset_folder",
    "folder_context",
    "with_folder_context",
    "resolve_folder_context",
    "symlink_mode",
]
