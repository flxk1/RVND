# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Extension seams for an optional governance overlay.

These let a separately-installed add-on layer an org policy ceiling and
per-actor access over workspaces **without forking core**. The add-on calls
``set_policy_resolver`` / ``set_access_check`` at import; core code calls
``resolve_policy`` / ``check_access`` and never knows whether an overlay
is present.

Core behaviour is unchanged: the default policy resolver returns the folder's
own policy, and the default access check allows everything. Single-user,
local-first Workspaces runs exactly as before; all tenancy lives in the add-on.
"""
from __future__ import annotations

from typing import Any, Callable


def _default_policy_resolver(folder: str) -> Any:
    from .policy import load_policy
    return load_policy(folder)


def _default_access_check(actor: str, workspace: str) -> bool:
    return True


_policy_resolver: Callable[[str], Any] = _default_policy_resolver
_access_check: Callable[[str, str], bool] = _default_access_check


def set_policy_resolver(fn: Callable[[str], Any] | None) -> None:
    """Install an effective-policy resolver (add-on applies the org ceiling).
    ``None`` restores the core default (the folder's own policy)."""
    global _policy_resolver
    _policy_resolver = fn or _default_policy_resolver


def set_access_check(fn: Callable[[str, str], bool] | None) -> None:
    """Install an access check ``(actor, workspace) -> bool`` (add-on enforces
    per-actor workspace grants). ``None`` restores the core default (allow all)."""
    global _access_check
    _access_check = fn or _default_access_check


def reset_hooks() -> None:
    """Restore both defaults — used by tests and to uninstall an overlay."""
    set_policy_resolver(None)
    set_access_check(None)


def resolve_policy(folder: str) -> Any:
    """Effective policy for ``folder`` — the single door core reads policy
    through, so an overlay can cap it (min(workspace, org ceiling))."""
    return _policy_resolver(folder)


def check_access(actor: str, workspace: str) -> bool:
    """Whether ``actor`` may open ``workspace``. Default allows all; an overlay
    enforces grants. Not wrapped in try/except — an overlay that fails closed
    must be allowed to deny."""
    return bool(_access_check(actor, workspace))
