# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""workspace_session — MCP facade over the session_io core (S12).

Thin I/O boundary: it stamps the real timestamp (the pure core takes `created`
as a param so it stays deterministic), captures live workspaces on save, and
turns the core's fail-closed exceptions into STRUCTURED results ({"ok": False,
report, forensic}) so an MCP caller gets a legible refusal, never a raw traceback.

Ops (workspace_session(op="help") lists them):
  save     — capture the given live workspaces + sign + write a .rvnd file
  verify   — read + verify a file (fail-closed); returns the report + card
  forensic — read-only view of a refused/corrupt file (never refuses)
  restore  — verify + reconstruct the whole environment under a dest root
  export   — slice one workspace into its own portable session
  import   — merge a workspace from another session into an environment file
  draft_save / draft_load / draft_discard — a workspace's authoring drafts
  template_list / template_new — starter environments, materialized from recipes
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from . import draft_store
from . import session_io as S
from . import session_templates
from .mcp_impl import _op_call


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def session_save(
    workspaces: list[dict],
    rail: dict,
    path: str,
    name: str,
    *,
    signed_by: str = "",
    origin_role: str = "user",
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Capture each live workspace ({folder_context, id, name?, log_root?,
    presentation?}) + sign + write one self-contained .rvnd file. Drafts are
    read from each workspace's draft store, never from the caller."""
    docs = [
        S.capture_workspace(
            w["folder_context"], workspace_id=w["id"], name=w.get("name", ""),
            log_root=w.get("log_root", log_root),
            presentation=w.get("presentation"))
        for w in workspaces
    ]
    bundle = S.build_session(docs, rail, name=name, created=_now(),
                             signed_by=signed_by, origin_role=origin_role)
    p = S.save_session(bundle, path)
    return {"ok": True, "path": str(p), "version": S.bundle_version(bundle),
            "card": S.describe_session(bundle)}


def session_verify(path: str) -> dict[str, Any]:
    """Read + verify (three checks + referential integrity). Fail-closed but
    structured: on refusal returns ok=False + the located report + a forensic view."""
    try:
        bundle, report = S.load_session(path)
        return {"ok": True, "report": report, "card": S.describe_session(bundle)}
    except S.SessionIntegrityError as e:
        return {"ok": False, "report": e.report, "forensic": S.read_session_forensic(path)}


def session_forensic(path: str) -> dict[str, Any]:
    """Read-only forensic view of a file — never refuses (fail-closed on write,
    not on looking); shows which workspaces are salvageable."""
    return S.read_session_forensic(path)


def session_restore(path: str, dest_root: str,
                    log_root_for: Optional[dict] = None) -> dict[str, Any]:
    """Verify (fail-closed) then reconstruct the whole environment under
    dest_root/<id>/. Returns the applied folders + the no-write presentation/rail."""
    try:
        bundle, report = S.load_session(path)
    except S.SessionIntegrityError as e:
        return {"ok": False, "report": e.report}
    applied = S.restore_environment(bundle, dest_root, log_root_for=log_root_for)
    return {"ok": True, "report": report, "folders": applied["folders"],
            "rail": applied["rail"], "presentation": applied["presentation"],
            "drafts": applied["drafts"],
            "drafts_refused": applied["drafts_refused"]}


def session_export(path: str, workspace_id: str, out_path: str,
                   *, signed_by: str = "") -> dict[str, Any]:
    """Slice one workspace out of a session file into its own portable file."""
    try:
        bundle, _ = S.load_session(path)
    except S.SessionIntegrityError as e:
        return {"ok": False, "report": e.report}
    try:
        track = S.export_workspace(bundle, workspace_id, created=_now(), signed_by=signed_by)
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    p = S.save_session(track, out_path)
    return {"ok": True, "path": str(p), "version": S.bundle_version(track)}


def session_import(env_path: str, track_path: str, workspace_id: str, out_path: str,
                   *, signed_by: str = "") -> dict[str, Any]:
    """Merge a workspace from track_path into the environment at env_path and
    write the resulting child session. Fail-closed on tamper + id collision."""
    try:
        env, _ = S.load_session(env_path)
        track, _ = S.load_session(track_path)
        merged = S.import_workspace(env, track, workspace_id, created=_now(),
                                    signed_by=signed_by)
    except S.SessionIntegrityError as e:
        return {"ok": False, "report": e.report}
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    p = S.save_session(merged, out_path)
    return {"ok": True, "path": str(p), "version": S.bundle_version(merged),
            "card": S.describe_session(merged)}


# --- content-based ops (the browser holds the .rvnd file client-side) --------
# The folders/chains are server-side; the .rvnd file is the user's, held in the
# browser (air-gap). So the surface builds a bundle (server captures live
# folders -> returns JSON the browser downloads), and verifies/restores from a
# bundle the browser uploads — no server-side file path needed.

def session_build(workspaces: list[dict], rail: dict, name: str,
                  *, signed_by: str = "", origin_role: str = "user",
                  log_root: Optional[str] = None) -> dict[str, Any]:
    """Capture live workspaces + sign; RETURN the bundle (no file write) for the
    browser to save. Same inputs as ``save`` minus ``path``; drafts come from
    the server-side draft store, never from the caller."""
    docs = [
        S.capture_workspace(
            w["folder_context"], workspace_id=w["id"], name=w.get("name", ""),
            log_root=w.get("log_root", log_root),
            presentation=w.get("presentation"))
        for w in workspaces
    ]
    bundle = S.build_session(docs, rail, name=name, created=_now(),
                             signed_by=signed_by, origin_role=origin_role)
    return {"ok": True, "bundle": bundle, "version": S.bundle_version(bundle),
            "card": S.describe_session(bundle)}


def session_verify_bytes(bundle: dict) -> dict[str, Any]:
    """Verify a bundle the browser uploaded (3 checks + referential). Structured:
    ok=False + report + forensic on refusal; never raises."""
    report = S.verify_full(bundle)
    if not report["ok"]:
        return {"ok": False, "report": report, "forensic": S.forensic_bundle(bundle)}
    # B: tell the surface whether this can be CONTINUED here or is view-only
    # (signed on another machine) — the Open dialog renders it either way.
    return {"ok": True, "report": report, "card": S.describe_session(bundle),
            "continuation": S.continuation_check(bundle)}


def session_restore_bytes(bundle: dict, dest_root: str,
                          log_root_for: Optional[dict] = None) -> dict[str, Any]:
    """Verify (fail-closed) an uploaded bundle, then restore the environment."""
    report = S.verify_full(bundle)
    if not report["ok"]:
        return {"ok": False, "report": report, "forensic": S.forensic_bundle(bundle)}
    applied = S.restore_environment(bundle, dest_root, log_root_for=log_root_for)
    return {"ok": True, "report": report, "card": S.describe_session(bundle),
            "folders": applied["folders"], "rail": applied["rail"],
            "presentation": applied["presentation"], "drafts": applied["drafts"],
            "drafts_refused": applied["drafts_refused"]}


def _rewrite_registry(folders: dict[str, str], names: dict[str, str], mode: str,
                      registry_log_root: Optional[str]) -> list[str]:
    """The non-destructive registry rewrite adopt and template_new share:
    register every restored/materialized folder (label = workspace name);
    mode="replace" also deregisters current entries whose folders aren't in
    the new set — their folders stay on disk (recoverable). Returns the
    retired paths."""
    from . import mcp_serving
    from . import workspace_registry as WR
    rlr = registry_log_root if registry_log_root is not None else mcp_serving._log_root()
    pre = list(WR.load_registry(log_root=rlr).get("workspaces") or [])
    for wid, path in folders.items():
        WR.add_known_workspace(path, label=names.get(wid, ""), log_root=rlr)
    retired: list[str] = []
    if mode == "replace":
        keep = set(folders.values())
        for w in pre:
            if w.get("path") not in keep:
                WR.remove_known_workspace(w["path"], log_root=rlr)
                retired.append(w["path"])
    return retired


def session_adopt(bundle: dict, dest_root: str, *, mode: str = "replace",
                  log_root_for: Optional[dict] = None,
                  registry_log_root: Optional[str] = None) -> dict[str, Any]:
    """Adopt a session AS the active environment (finding #3 reconciliation).

    The active environment is the workspace REGISTRY. Adopting is
    **non-destructive**: restore the session's workspaces into fresh folders
    (verify + foreign-key guard via restore_environment), then rewrite the
    registry — never delete a folder on disk.

    mode="replace": register the restored workspaces and DEREGISTER the current
      ones whose folders aren't in the restored set. Their folders remain on
      disk (recoverable — re-adopt their session or re-add the folder). This is
      "load replaces the active environment" done as a registry swap, not a
      destructive overwrite; consistent with append-only + fork-not-rewind.
    mode="beside": register the restored workspaces alongside the current ones
      (used by open-beside / import).
    """
    report = S.verify_full(bundle)
    if not report["ok"]:
        return {"ok": False, "report": report, "forensic": S.forensic_bundle(bundle)}
    try:
        applied = S.restore_environment(bundle, dest_root, log_root_for=log_root_for)
    except S.SessionIntegrityError as e:
        return {"ok": False, "report": e.report}      # foreign-key / view-only

    names = {ws.get("id"): ws.get("name", "") for ws in bundle.get("workspaces", [])}
    retired = _rewrite_registry(applied["folders"], names, mode, registry_log_root)
    return {"ok": True, "report": report, "mode": mode,
            "adopted": applied["folders"], "retired": retired,
            "rail": applied["rail"], "presentation": applied["presentation"],
            "drafts_refused": applied["drafts_refused"],
            "note": "retired workspaces were deregistered; their folders remain "
                    "on disk (recoverable)"}


# --- starter templates (S14) --------------------------------------------------
# A template is a RECIPE materialized in-process and signed with the local key
# — never a shipped signed fixture, which would be a foreign-key session =
# view-only (decision B). session_templates.py records the full rationale.

def session_template_list() -> dict[str, Any]:
    """The starter-template catalogue: id, name, description, and the
    workspaces each template would create."""
    return {"ok": True, "templates": session_templates.list_templates()}


def session_template_new(template_id: str, dest_root: str, *,
                         mode: str = "beside", signed_by: str = "",
                         log_root: Optional[str] = None,
                         registry_log_root: Optional[str] = None) -> dict[str, Any]:
    """Materialize a starter template into fresh folders under dest_root and
    register the result the way adopt does. mode="beside" (default — a starter
    never silently retires the current rail), "replace" (registry swap, folders
    stay on disk), or "none" (materialize only; returns the signed bundle)."""
    if mode not in ("beside", "replace", "none"):
        return {"ok": False,
                "error": f'mode must be "beside", "replace" or "none", got {mode!r}'}
    out = session_templates.materialize(template_id, dest_root, created=_now(),
                                        signed_by=signed_by, log_root=log_root)
    if not out["ok"]:
        return out
    names = {w["id"]: w["name"] for w in out["card"]["workspaces"]}
    retired: list[str] = []
    if mode != "none":
        retired = _rewrite_registry(out["folders"], names, mode, registry_log_root)
    return {**out, "mode": mode, "retired": retired}


# --- drafts (the authoring persistence gap) ----------------------------------
# The surface autosaves working state (pasted policy, map view, in-progress
# cards, officer roster edits, chat transcript) and rehydrates it on load, so
# a reload no longer loses authoring state. Drafts are unsigned files beside
# the chain (draft_store); session capture embeds them from there. Callers
# over MCP never pass log_root, so an omitted one resolves to the serving
# root (WORKSPACE_L0_LOG_ROOT) — the root the chain itself lives under.


def _draft_log_root(log_root: Optional[str]):
    if log_root is not None:
        return log_root
    from . import mcp_serving
    return mcp_serving._log_root()


def session_draft_save(folder_context: str, surface: str, payload: dict,
                       log_root: Optional[str] = None) -> dict[str, Any]:
    """Persist one draft surface. Refuses an unknown surface, a sealed
    workspace, or a payload over the per-surface cap; writes no chain event."""
    return draft_store.save(folder_context, surface, payload,
                            log_root=_draft_log_root(log_root))


def session_draft_load(folder_context: str, surface: Optional[str] = None,
                       log_root: Optional[str] = None) -> dict[str, Any]:
    """Read one draft surface, or all of them when ``surface`` is omitted.
    Corrupt files are named in ``unreadable``, never silently dropped."""
    return draft_store.load(folder_context, surface,
                            log_root=_draft_log_root(log_root))


def session_draft_discard(folder_context: str, surface: Optional[str] = None,
                          log_root: Optional[str] = None) -> dict[str, Any]:
    """Delete one draft surface (or all). Idempotent; the recovery for a
    corrupt draft file."""
    return draft_store.discard(folder_context, surface,
                               log_root=_draft_log_root(log_root))


def workspace_session(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """workspace_session facade — save/load a governance session (the whole
    environment). Path-based ops (save/verify/restore/export/import) use local
    files (air-gap); content-based ops (build/verify_bytes/restore_bytes) pass
    the bundle itself so the browser can hold the .rvnd file. Loads are
    fail-closed. workspace_session(op="help") lists ops."""
    return _op_call(op, {
        "save": session_save,
        "verify": session_verify,
        "forensic": session_forensic,
        "restore": session_restore,
        "export": session_export,
        "import": session_import,
        "build": session_build,
        "verify_bytes": session_verify_bytes,
        "restore_bytes": session_restore_bytes,
        "adopt": session_adopt,
        "template_list": session_template_list,
        "template_new": session_template_new,
        "draft_save": session_draft_save,
        "draft_load": session_draft_load,
        "draft_discard": session_draft_discard,
    }, params or {})
