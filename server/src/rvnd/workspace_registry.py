# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim — the workspace registry now lives in ``loomground-workspace``.

Which folders are known workspaces, and the ``<log_root>/known-workspaces.json``
file that records them, moved to ``loomground_workspace.workspace_registry``.
RVND consumes it through ``adapters.workspace``. This module holds ZERO
definitions of its own; it exists so the call sites and 18 test modules that
address ``workspaces.workspace_registry`` keep working unchanged.
``tests/test_no_parallel_structures.py`` asserts it re-grows none.

**``list_known_workspaces`` here is RVND's SCOPED read, not upstream's.**
Upstream took the access-control policy out of the concept: its version returns
the whole registry unless the host injects a ``scope=`` filter. RVND's filter —
per-request principal, fail-closed — is defaulted inside
``adapters.workspace``, so this name carries the same semantics it always did
and no caller has to remember anything. That matters most at
``app/serve.py``'s trusted-path allowlist, which sits directly upstream of the
proxy-proof and session-token checks that authorize egress.

One thing DID move: ``LOG_ROOT_DEFAULT`` is re-exported here, but the code that
reads it (``_registry_path``) now lives upstream and reads *its* module global.
Patching this module's copy is therefore a no-op. Tests that need to redirect
the default registry root must patch
``loomground_workspace.workspace_registry.LOG_ROOT_DEFAULT`` — the module that
reads it — or, better, pass the explicit ``log_root=`` argument that the A6
work threaded everywhere. ``server/tests/security/test_attack_folder_context_traversal.py``
carries a guard that fails loudly if that ever stops biting, because a
silently-ineffective patch there would make the traversal tests pass vacuously.
"""

from __future__ import annotations

from .adapters.workspace import (  # noqa: F401
    DEFAULT_WORKSPACE_DIR,
    LOG_ROOT_DEFAULT,
    REGISTRY_FILE,
    REGISTRY_VERSION,
    _registry_path,
    _resolved,
    _save_registry,
    add_known_workspace,
    bootstrap_default_workspace,
    list_known_workspaces,
    load_registry,
    remove_known_workspace,
)

__all__ = [
    "REGISTRY_FILE",
    "REGISTRY_VERSION",
    "DEFAULT_WORKSPACE_DIR",
    "LOG_ROOT_DEFAULT",
    "load_registry",
    "add_known_workspace",
    "remove_known_workspace",
    "list_known_workspaces",
    "bootstrap_default_workspace",
]
