# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The axis-B (target-anchored) context-resolution seam for the enforcement hook.

A governed action has two coordinates. Axis A is the folder the *agent* is
acting from — the ``cwd`` the hook already carries on every event. Axis B is
the folder(s) the action's *target* actually reaches: a shell command with a
path argument, a file write into another tree, or an MCP tool addressing a
named workspace can each target a folder that differs from the acting one.
:func:`resolve_contexts` is where that second axis is resolved — it reads
``tool_name``/``tool_input`` and returns every folder-context the call should
be evaluated under, so a governance decision can be joined (strictest-wins)
across all of them rather than the single folder the call happened to be
issued from.

This module DECIDES and RECORDS which contexts govern a call; it does not
itself enforce anything against a target folder — governance for each
resolved context still runs through the same chokepoint
(:func:`rvnd.governance.decide_action`) the acting folder always used, so a
target folder is held to its own registered policy rather than to a rule
this module invents.

Two helpers do the work:

``resolve_targets`` reads the STRUCTURED file-write tools — ``Write``,
``Edit``, ``MultiEdit`` (``file_path``) and ``NotebookEdit``
(``notebook_path``) — and returns the absolute path(s) each call would write
to. Every other tool — ``Bash``, ``Read``, ``Glob``, ``Grep``, ``WebFetch``,
``WebSearch``, any ``mcp__*`` tool — returns no targets here: a shell command's
path arguments, a read's source, and an MCP tool's addressed resource are not
covered by this resolver and fall through to the acting-folder-only context,
same as before this module existed.

``_target_workspace`` maps an absolute path to the root of the registered
workspace it is at or under, by walking the workspace registry (consumed,
never reimplemented, through ``rvnd.adapters.workspace``) component-wise —
never by string prefix — a path that merely starts with a registered root's
characters, like a sibling folder whose name extends it, must not match.

Returning the singleton ``(cwd,)`` — the acting folder alone, and nothing
else — is always a correct answer here: it is the historical resolution, and
it is the safe fallback for any case that cannot be confidently resolved
further. Extending resolution means returning a longer tuple; the join on the
caller's side (see ``hook._meet_decisions``) already composes over however
many contexts come back, so a one-context caller and a many-context caller
share the same path.

Resolution must never raise. This function sits on the hook's fail-closed
critical path, so any exception inside a resolution step — including one
provoked by a malformed ``tool_input`` or an unavailable registry — is caught
here and answered with the acting folder alone, the same answer a call that
never reached resolution logic at all would get.

Internal by design: this module is consumed only by ``rvnd.hook`` — the same
PreToolUse/PostToolUse enforcement path ``hook`` itself is internal to — and
is not part of the MCP tool surface ``verify_surface`` tracks.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

# The tools resolve_targets reads a target from, and which field each names
# it in. Every other tool name — Bash, Read, Glob, Grep, WebFetch, WebSearch,
# any mcp__* tool — is out of scope for this resolver by design: a shell
# command's path arguments, a read's source, and an MCP tool's addressed
# resource are not covered here.
_FILE_PATH_TOOLS = ("Write", "Edit", "MultiEdit")
_NOTEBOOK_PATH_TOOL = "NotebookEdit"

# Operator-set log root for the workspace registry this resolver consults.
# Matches the convention the rest of RVND uses (``principal._log_root``,
# ``audit_drop._marker_path``): an explicit root always wins; absent one, this
# env var; absent that, the registry's own built-in default.
_WORKSPACE_LOG_ROOT_ENV = "WORKSPACE_L0_LOG_ROOT"


def _resolve_target_path(raw: str, cwd: str) -> Optional[str]:
    """Turn one tool-supplied path string into an absolute, expanded, resolved
    path. Relative paths are resolved against ``cwd``. Returns ``None`` for
    anything that is not a non-empty string."""
    if not isinstance(raw, str) or not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        base = Path(str(cwd or os.getcwd())).expanduser()
        p = base / p
    return str(p.resolve())


def resolve_targets(cwd: str, tool_name: str, tool_input: dict[str, Any]) -> tuple[str, ...]:
    """Return the absolute target path(s) a structured file-write call would
    write to.

    Covers exactly the four structured file-write tools: ``Write``, ``Edit``,
    and ``MultiEdit`` (all read ``file_path``), and ``NotebookEdit`` (reads
    ``notebook_path``). A relative path is resolved against ``cwd``; the
    result is expanded (``~``) and resolved to its real, absolute form.

    Every other tool — ``Bash``, ``Read``, ``Glob``, ``Grep``, ``WebFetch``,
    ``WebSearch``, any ``mcp__*`` tool, and anything unrecognised — returns
    the empty tuple: a shell command's path arguments, a read's source, and
    an MCP tool's addressed resource are not covered by this resolver. A
    malformed ``tool_input`` (not a dict, or missing/non-string path field)
    also returns the empty tuple.

    Never raises: any error encountered while inspecting the call is caught
    and answered with the empty tuple, the same answer an out-of-scope tool
    would get.
    """
    try:
        name = str(tool_name or "")
        ti = tool_input if isinstance(tool_input, dict) else {}
        if name in _FILE_PATH_TOOLS:
            raw = ti.get("file_path")
        elif name == _NOTEBOOK_PATH_TOOL:
            raw = ti.get("notebook_path")
        else:
            return ()
        resolved = _resolve_target_path(raw, cwd)
        return (resolved,) if resolved else ()
    except BaseException:  # noqa: BLE001 — resolution must degrade, never raise
        return ()


def _iter_registered_roots() -> list[Path]:
    """The raw, unscoped workspace registry — every registered root, each
    expanded and resolved.

    Raw and unscoped deliberately, and read through RVND's adapter seam
    (``rvnd.adapters.workspace``) rather than the upstream package directly —
    the same boundary ``rvnd.folder_context``/``rvnd.registry`` observe, and
    the one ``test_adapter_boundary``/``test_consumed_modules`` enforce.
    ``load_registry`` (not the seam's principal-scoped ``list_known_workspaces``)
    is the right read here, for the same reason
    ``loomground_workspace.folder_context._enforce_allowlist`` uses it: this
    establishes physical folder containment, not an authorization decision, so
    it must see every registered root, not a principal-filtered subset of them.
    """
    from .adapters.workspace import load_registry

    log_root_env = os.environ.get(_WORKSPACE_LOG_ROOT_ENV)
    log_root = Path(log_root_env) if log_root_env else None
    data = load_registry(log_root=log_root)
    known = data.get("workspaces") or []
    roots: list[Path] = []
    for w in known:
        raw = w.get("path") if isinstance(w, dict) else None
        if not raw:
            continue
        try:
            roots.append(Path(str(raw)).expanduser().resolve())
        except Exception:
            continue
    return roots


def _target_workspace(path: str) -> Optional[str]:
    """Map an absolute path to the root of the registered governed workspace
    it is at or under.

    Reads the workspace registry through ``rvnd.adapters.workspace`` (the
    sanctioned consumer seam over ``loomground_workspace`` — reused, not
    reimplemented) and walks each candidate root's ancestry, comparing whole
    path components via ``folder_hash`` at every level — never by string
    prefix, so a sibling folder whose name merely extends a registered root's
    characters (for instance a folder named like a registered one with extra
    characters appended) does not match it. When more than one registered
    root contains ``path`` (nested registrations), the most specific —
    deepest — root wins.

    Returns ``None`` if ``path`` is under no registered workspace, if the
    workspace registry is unavailable, or on any error. Never raises.
    """
    try:
        from .adapters.workspace import folder_hash as _folder_hash
    except Exception:
        return None
    try:
        p = Path(str(path)).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        p = p.resolve()
    except Exception:
        return None
    try:
        roots = _iter_registered_roots()
    except Exception:
        return None

    best_root: Optional[Path] = None
    best_depth = -1
    for root in roots:
        try:
            root_hash = _folder_hash(root)
        except Exception:
            continue
        cur = p
        matched = False
        while True:
            try:
                if _folder_hash(cur) == root_hash:
                    matched = True
                    break
            except Exception:
                break
            if cur.parent == cur:  # reached the filesystem root without a match
                break
            cur = cur.parent
        if matched and len(root.parts) > best_depth:
            best_depth = len(root.parts)
            best_root = root
    return str(best_root) if best_root is not None else None


def resolve_contexts(cwd: str, tool_name: str, tool_input: dict[str, Any]) -> tuple[str, ...]:
    """Resolve the folder-context(s) a proposed call should be governed under.

    ``cwd`` is the acting folder (axis A) and is always the first element and
    the fallback answer. ``tool_name``/``tool_input`` describe the proposed
    call; :func:`resolve_targets` reads them to find any absolute target
    path(s) a structured file-write reaches, and :func:`_target_workspace`
    maps each target to its registered workspace root (axis B).

    The result is ``(cwd,)`` followed by each DISTINCT foreign target
    workspace: a target workspace is added only when it differs from cwd's
    own registered workspace (comparing resolved roots) and has not already
    been added. A target that lands within cwd's own workspace, or that maps
    to no registered workspace at all, adds nothing — the result stays the
    singleton ``(cwd,)`` exactly as before target resolution existed.

    On any doubt — an unrecognised tool, a malformed ``tool_input``, an
    unavailable registry, or an error raised while inspecting any of the
    above — this returns ``(cwd,)``, never more and never less, and never
    raises.
    """
    try:
        name = str(tool_name or "")
        ti = tool_input if isinstance(tool_input, dict) else {}
        cwd_str = str(cwd)

        cwd_workspace = _target_workspace(cwd_str)
        cwd_workspace_real: Optional[str] = None
        if cwd_workspace:
            try:
                cwd_workspace_real = str(Path(cwd_workspace).resolve())
            except Exception:
                cwd_workspace_real = cwd_workspace

        contexts: list[str] = [cwd]
        seen: set[str] = {cwd_workspace_real} if cwd_workspace_real else set()

        for target in resolve_targets(cwd_str, name, ti):
            workspace = _target_workspace(target)
            if not workspace:
                continue
            try:
                workspace_real = str(Path(workspace).resolve())
            except Exception:
                workspace_real = workspace
            if workspace_real == cwd_workspace_real or workspace_real in seen:
                continue
            seen.add(workspace_real)
            contexts.append(workspace)

        return tuple(contexts)
    except BaseException:  # noqa: BLE001 — resolution must degrade, never raise
        return (cwd,)
