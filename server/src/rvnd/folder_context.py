# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Skill-runtime ``folder_context`` injection — Phase 3 (A3).

The plugin runtime threads a folder path into every skill invocation. Skills
that read ``WorkspaceMemory()`` pick up the current scope automatically; skills that
explicitly pass ``folder_context=`` still work unchanged.

Three ways to set the context, in priority order:

1. **Explicit argument** — ``WorkspaceMemory(folder_context="/path/to/folder")``.
2. **Context manager / contextvar** — ``with folder_context("/path/to/folder"):``.
   Threadsafe + asyncio-safe via :mod:`contextvars`.
3. **Environment variable** — ``WORKSPACE_FOLDER_CONTEXT=/path/to/folder``.
   Fallback for hosts that can't set contextvars from outside the Python process.

If none of the three is set and the caller asks for WorkspaceMemory, the constructor
raises :class:`NoFolderContextError` by default. Pass ``allow_unscoped=True``
to bypass — that mode emits a warning to ``stderr`` + the audit chain (when one
is wired) on every memory access.
"""

from __future__ import annotations

import contextvars
import functools
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Callable, TypeVar


_ENV_VAR = "WORKSPACE_FOLDER_CONTEXT"

# ---------------------------------------------------------------------------
# B6.3 (0.6.8): WORKSPACE_SYMLINK_MODE
# ---------------------------------------------------------------------------
# Default behaviour (``follow``) calls ``Path.resolve()`` which dereferences
# symlinks. That means two paths that point at the same target — e.g. a
# user's home folder mounted both at ``/Users/x/Workspace`` and via a
# symlink at ``/mnt/workspace`` — collapse to the SAME workspace identity
# and share one mutation log.
#
# Setting ``WORKSPACE_SYMLINK_MODE=isolate`` switches to ``Path.absolute()``
# without resolving — each symlink path becomes its OWN workspace. This is
# an explicit opt-in: it BREAKS symlink-merged workspaces. We keep the
# default at ``follow`` for back-compat. ``workspaces status`` surfaces the
# active mode so the user can see what they're running with.
_SYMLINK_MODE_ENV = "WORKSPACE_SYMLINK_MODE"
_SYMLINK_MODE_FOLLOW = "follow"
_SYMLINK_MODE_ISOLATE = "isolate"


def symlink_mode() -> str:
    """Return the active symlink-resolution mode (``follow`` or ``isolate``).

    Reads :envvar:`WORKSPACE_SYMLINK_MODE`. Unknown values fall back to
    ``follow`` and are silently normalised (we never crash a workspace open
    over a typo in an env var).
    """
    raw = (os.environ.get(_SYMLINK_MODE_ENV) or "").strip().lower()
    if raw == _SYMLINK_MODE_ISOLATE:
        return _SYMLINK_MODE_ISOLATE
    return _SYMLINK_MODE_FOLLOW


def _resolve_with_symlink_policy(path: str | Path) -> Path:
    """Resolve ``path`` honouring :envvar:`WORKSPACE_SYMLINK_MODE`.

    - ``follow`` (default): ``Path.resolve()`` — dereferences symlinks.
      Same physical folder via two paths → same workspace.
    - ``isolate``: ``Path.absolute()`` — keeps the symlink path distinct
      from its target. Same physical folder via two paths → two workspaces.
      Explicit opt-in; intentionally breaks symlink-merged workspaces for
      users who want strict per-path isolation.

    Returns an absolute :class:`Path`. Does NOT raise if the path does
    not exist; symlink resolution is purely lexical for ``isolate`` mode
    and best-effort for ``follow`` mode (Path.resolve(strict=False)).
    """
    p = Path(path).expanduser()
    if symlink_mode() == _SYMLINK_MODE_ISOLATE:
        return p.absolute()
    return p.resolve()


_current_folder: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workspace_l0_folder_context", default=None
)


# ---------------------------------------------------------------------------
# Errors + sentinels
# ---------------------------------------------------------------------------


class NoFolderContextError(RuntimeError):
    """Raised when WorkspaceMemory is constructed with no folder_context available.

    Either pass ``folder_context=`` explicitly, set
    :envvar:`WORKSPACE_FOLDER_CONTEXT`, or wrap the call with the
    :func:`folder_context` context manager.
    """


class FolderContextNotAllowed(RuntimeError):
    """Raised when an explicit folder_context resolves outside the known-workspaces
    allowlist (A6 traversal mitigation).

    The resolved path is neither a registered workspace nor a descendant of one,
    and ``WORKSPACES_ALLOW_UNREGISTERED=1`` was not set. Register the folder with
    ``add_known_workspace`` or set the override to allow unregistered paths.
    """


UNSCOPED_SENTINEL = "<unscoped>"

# A6: opt-out for the allowlist check. Default (unset) is ENFORCE — an explicit
# folder_context must resolve into a registered workspace (or a descendant).
# Set to "1" to allow any resolvable path (e.g. ad-hoc CLI use on an
# unregistered folder, or a test harness that operates on scratch folders).
ALLOW_UNREGISTERED_ENV = "WORKSPACES_ALLOW_UNREGISTERED"


def _path_is_within(child: Path, parent: Path) -> bool:
    """True iff ``child`` is ``parent`` or a descendant of it."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _enforce_allowlist(resolved: Path, *, log_root: str | Path | None = None) -> Path:
    """A6: refuse an explicit folder_context that escapes the known-workspaces
    allowlist, unless the override env var is set.

    A session "scoped" to ``/tenants/acme`` could otherwise pass
    ``folder_context="/tenants/acme/../competitor"`` (or any absolute path the
    OS process can read) and reach a sibling tenant's folder. The registry is
    the allowlist: the resolved path must be a registered workspace or live
    under one. Descendants are allowed (the asymmetric folder rule).

    ``log_root`` selects which registry file backs the allowlist —
    ``<log_root>/known-workspaces.json`` — and MUST match the log root the
    operation runs under so enforcement reads the same registry that
    ``add_known_workspace`` / ``bootstrap_default_workspace`` write to. When
    ``None`` it falls back to the default log root (the historic behaviour).
    ``log_root`` is always an operator setting (the ``--log-root`` flag or the
    ``WORKSPACE_L0_LOG_ROOT`` startup env), never request-derived, so honouring
    it opens no path-escape the caller couldn't already reach with the env
    override — it only stops a custom-``--log-root`` operator's own
    registrations from being invisible to enforcement.
    """
    if os.environ.get(ALLOW_UNREGISTERED_ENV) == "1":
        return resolved
    try:
        # Path containment must use the raw registry, not the principal-scoped
        # listing.  The scoped listing proves membership by replaying this
        # workspace's MutationLog, whose constructor resolves the folder and
        # therefore returns here.  Calling it here would recurse forever for
        # every authenticated request.  Authorization and response filtering
        # remain in the serving/registry layer; this check only establishes
        # that the path is inside a configured workspace boundary.
        from .workspace_registry import load_registry
        roots: list[Path] = []
        for w in load_registry(log_root=log_root).get("workspaces", []):
            p = w.get("path")
            if not p:
                continue
            try:
                roots.append(Path(p).resolve())
            except Exception:
                roots.append(Path(p))
    except Exception:
        roots = []
    for root in roots:
        if resolved == root or _path_is_within(resolved, root):
            return resolved
    raise FolderContextNotAllowed(
        f"folder_context {str(resolved)!r} is not in the known-workspaces "
        f"allowlist (unregistered, and not a descendant of any registered "
        f"workspace). Register it with add_known_workspace, or set "
        f"{ALLOW_UNREGISTERED_ENV}=1 to allow unregistered folders."
    )
"""Folder path used for unscoped memory operations.

Resolves to a sentinel folder under ``~/.workspace/unscoped/`` so backwards-
compat callers operate in an isolated scratch space rather than leaking into
any real folder. Audit log marks these operations.
"""


# ---------------------------------------------------------------------------
# Read / write the current folder context
# ---------------------------------------------------------------------------


def current_folder() -> str | None:
    """Return the current folder context, or ``None`` if none is set.

    Lookup order:
      1. The contextvar (set via :func:`set_folder` or :class:`folder_context`).
      2. The ``WORKSPACE_FOLDER_CONTEXT`` environment variable.
      3. ``None``.
    """
    explicit = _current_folder.get()
    if explicit:
        return explicit
    env = os.environ.get(_ENV_VAR)
    return env if env else None


def set_folder(path: str | Path) -> contextvars.Token:
    """Set the current folder context. Returns a token for :func:`reset_folder`.

    Use when you want imperative scoping (typically: at the entry point of a
    plugin invocation handler). For most callers prefer the :class:`folder_context`
    context manager — it auto-resets on exit.
    """
    resolved = str(_resolve_with_symlink_policy(path))
    return _current_folder.set(resolved)


def reset_folder(token: contextvars.Token) -> None:
    """Restore the previous folder context using a token from :func:`set_folder`."""
    _current_folder.reset(token)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class folder_context:
    """Scoped folder context — restores the prior context on exit.

    .. code-block:: python

        with folder_context("/companies/acme/HR/"):
            mem = WorkspaceMemory()           # automatically scoped to HR
            mem.search("...")
        # context is restored to whatever it was before

    Nestable: an inner ``with`` block temporarily overrides the outer one.
    Thread- and asyncio-safe via :mod:`contextvars`.
    """

    def __init__(self, path: str | Path):
        self._path = path
        self._token: contextvars.Token | None = None

    def __enter__(self) -> "folder_context":
        self._token = set_folder(self._path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            reset_folder(self._token)
            self._token = None


# ---------------------------------------------------------------------------
# Decorator — skills that want their folder context to come from an argument
# ---------------------------------------------------------------------------


F = TypeVar("F", bound=Callable[..., Any])


def with_folder_context(
    arg_name: str = "folder_context",
) -> Callable[[F], F]:
    """Decorator: take a kwarg, set it as the folder context for the call.

    .. code-block:: python

        @with_folder_context("folder")
        def my_skill(query: str, folder: str) -> list:
            mem = WorkspaceMemory()           # picks up `folder` automatically
            return mem.search(query)

    If the named arg is absent, the context is not changed. Useful for skills
    invoked over MCP where the host passes the folder as a regular argument.
    """
    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            path = kwargs.get(arg_name)
            if path:
                with folder_context(path):
                    return fn(*args, **kwargs)
            return fn(*args, **kwargs)
        return wrapper  # type: ignore[return-value]
    return deco


# ---------------------------------------------------------------------------
# Resolution helper used by WorkspaceMemory
# ---------------------------------------------------------------------------


def resolve_folder_context(
    explicit: str | Path | None,
    *,
    allow_unscoped: bool = False,
    log_root: str | Path | None = None,
) -> str:
    """Resolve a final folder path for an WorkspaceMemory call.

    Priority:
      1. ``explicit`` (passed directly to :class:`WorkspaceMemory`).
      2. :func:`current_folder` (contextvar or env var).
      3. If ``allow_unscoped`` is True → :data:`UNSCOPED_SENTINEL` with a warning.
      4. Else → raise :class:`NoFolderContextError`.

    ``log_root`` scopes the A6 allowlist to ``<log_root>/known-workspaces.json``
    (see :func:`_enforce_allowlist`); pass the same log root the operation runs
    under so a folder registered under a custom ``--log-root`` is honoured.
    Only the ``explicit`` branch is allowlist-checked, so ``log_root`` is
    ignored for the contextvar/env and unscoped branches.

    Returns the resolved absolute path string (or the sentinel for unscoped).
    """
    if explicit is not None:
        return str(_enforce_allowlist(
            _resolve_with_symlink_policy(explicit), log_root=log_root))

    found = current_folder()
    if found is not None:
        return found

    if allow_unscoped:
        unscoped_path = str((Path.home() / ".workspace" / "unscoped").resolve())
        warnings.warn(
            f"WorkspaceMemory called with no folder context — operating in unscoped "
            f"mode at {unscoped_path}. Set WORKSPACE_FOLDER_CONTEXT or use "
            f"`with folder_context(...):` to scope properly.",
            RuntimeWarning,
            stacklevel=3,
        )
        # Also write to stderr for visibility in non-Python hosts.
        print(
            f"[workspace-l0-memory] WARNING: unscoped memory access — "
            f"see WORKSPACE_FOLDER_CONTEXT or `with folder_context(...):`.",
            file=sys.stderr,
        )
        return unscoped_path

    raise NoFolderContextError(
        "WorkspaceMemory() requires a folder context.\n"
        "  Either:\n"
        "    (1) pass folder_context= explicitly,\n"
        "    (2) wrap the call in `with folder_context('/path/to/folder'):`,\n"
        "    (3) set the WORKSPACE_FOLDER_CONTEXT environment variable,\n"
        "    (4) pass allow_unscoped=True to operate in the unscoped scratch space."
    )
