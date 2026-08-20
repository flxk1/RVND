# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""MCP implementation — the handlers the workspaces-mcp facades dispatch to.

Split out of mcp_server.py so that file is the server SURFACE (the @mcp.tool
facades + _DECLARED_TOOLS) and this is the implementation. One-way deps:
mcp_serving <- mcp_impl <- mcp_server. No FastMCP tools are defined here.
"""

from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Any, Optional
from .memory import WorkspaceMemory
from workspaces.adapters.solver.dimensions import Dimension, classify_predicate, classify_query_dimension
from .llm_capture import (
    IngestMode,
    LLMExchange,
    OversightLevel,
    capture_llm_exchange,
)
from .policy import load_policy
from .authorization import check_access
from .web_capture import (
    WebSearchExchange,
    WebSearchResult,
    capture_web_search,
)
from .mcp_serving import (
    _log_root,
    _lock_string,
    _resolve_mode_for_folder,
    _lock_gate_text,
    _folder_lock_mode,
    _rerank_by_dimension,
    _safe_view,
    _wrap_scanned,
)

# Dynamic seam: resolve _log_root through mcp_serving so tests that patch
# workspaces.mcp_serving._log_root take effect in every module (split-safe).
from . import mcp_serving as _mcp_serving
def _log_root():
    return _mcp_serving._log_root()


def _default_actor() -> str:
    return os.environ.get("WORKSPACE_L0_DEFAULT_ACTOR", "mcp:l0")

def _parse_mode(value: str | None) -> IngestMode:
    if not value:
        return IngestMode.AGENTIC
    v = value.strip().lower()
    if v == "interactive":
        return IngestMode.INTERACTIVE
    return IngestMode.AGENTIC

def _parse_oversight(value: str | int | None) -> OversightLevel:
    if value is None:
        return OversightLevel.APPROVE
    if isinstance(value, int):
        try:
            return OversightLevel(value)
        except ValueError:
            return OversightLevel.APPROVE
    v = str(value).strip().lower()
    mapping = {ol.name.lower(): ol for ol in OversightLevel}
    return mapping.get(v, OversightLevel.APPROVE)

def _serialise_capture_result(r: Any) -> dict[str, Any]:
    """Normalise a CaptureResult (LLM or web) to a transport-friendly dict."""
    verbosity = r.verbosity
    if hasattr(verbosity, "value"):
        verbosity = verbosity.value
    out = {
        "captured": bool(r.captured),
        "pair_id": r.pair_id,
        "verbosity": str(verbosity),
        "prompted_user": bool(getattr(r, "prompted_user", False)),
        "audit_id": r.audit_id,
        "mode": (r.mode.value if hasattr(r.mode, "value") else str(r.mode)),
        "skipped_reason": getattr(r, "skipped_reason", "") or "",
        "oversight_bypassed": bool(getattr(r, "oversight_bypassed", False)),
    }
    return out

def capture_llm(
    folder_context: str,
    model: str,
    prompt_context: str,
    response: str,
    cited_sources: list[str] | None = None,
    cost_estimate_cents: float | None = None,
    tool_call_trace: list[dict[str, Any]] | None = None,
    request_id: str = "",
    mode: str = "agentic",
    oversight: str | int = "approve",
    actor: str | None = None,
) -> dict[str, Any]:
    """Record an LLM exchange into the folder's L0 memory.

    Args:
        folder_context: absolute path of the folder the exchange is scoped to.
        model: model identifier (e.g. ``"claude-sonnet-4-6"``).
        prompt_context: the outgoing prompt + context.
        response: the LLM's response.
        cited_sources: optional list of URLs / source IDs the response cites.
        cost_estimate_cents: optional cost estimate.
        tool_call_trace: optional list of tool-call records from the run.
        request_id: optional caller-supplied request id (for correlation).
        mode: ``"agentic"`` (mandatory audit floor) or ``"interactive"``
            (opt-in by oversight level).
        oversight: ``"autonomous"`` / ``"notify"`` / ``"review"`` /
            ``"approve"`` / ``"supervised"`` / ``"manual"`` (or 1..6).
        actor: who is performing the capture (default: env or ``"mcp:l0"``).

    Returns:
        A dict with ``captured``, ``pair_id``, ``verbosity``, ``audit_id``,
        ``mode``, ``oversight_bypassed`` plus ``skipped_reason`` if any.
    """
    exchange = LLMExchange(
        model=model,
        prompt_context=prompt_context,
        response=response,
        cited_sources=list(cited_sources or []),
        cost_estimate_cents=cost_estimate_cents,
        tool_call_trace=list(tool_call_trace or []),
        request_id=request_id,
    )
    result = capture_llm_exchange(
        exchange,
        mode=_parse_mode(mode),
        oversight=_parse_oversight(oversight),
        folder_context=folder_context,
        log_root=_log_root(),
        actor=actor or _default_actor(),
    )
    return _serialise_capture_result(result)

def capture_web(
    folder_context: str,
    query: str,
    engine: str,
    results: list[dict[str, Any]],
    cost_estimate_cents: float | None = None,
    request_id: str = "",
    mode: str = "agentic",
    oversight: str | int = "approve",
    actor: str | None = None,
) -> dict[str, Any]:
    """Record a web-search exchange into the folder's L0 memory.

    Args:
        folder_context: absolute path of the folder the search is scoped to.
        query: the search query string.
        engine: identifier of the search engine used.
        results: list of result dicts. Each dict may include
            ``url`` / ``title`` / ``snippet`` / ``full_text`` / ``rank``.
            Missing keys default to empty strings / 0.
        cost_estimate_cents: optional cost estimate.
        request_id: optional caller-supplied request id.
        mode: ``"agentic"`` or ``"interactive"``.
        oversight: as in ``capture_llm``.
        actor: as in ``capture_llm``.

    Returns:
        Same shape as ``capture_llm``.
    """
    web_results = [
        WebSearchResult(
            url=str(r.get("url", "")),
            title=str(r.get("title", "")),
            snippet=str(r.get("snippet", "")),
            full_text=str(r.get("full_text", "")),
            rank=int(r.get("rank", 0)),
        )
        for r in results
    ]
    exchange = WebSearchExchange(
        query=query,
        engine=engine,
        results=web_results,
        cost_estimate_cents=cost_estimate_cents,
        request_id=request_id,
    )
    result = capture_web_search(
        exchange,
        mode=_parse_mode(mode),
        oversight=_parse_oversight(oversight),
        folder_context=folder_context,
        log_root=_log_root(),
        actor=actor or _default_actor(),
    )
    return _serialise_capture_result(result)


def capture_read(folder_context: str, limit: int = 200,
                 actor: str | None = None) -> dict[str, Any]:
    """Read the capture ledger — the LLM / web exchanges recorded for this
    folder (the 'what left vs what stayed' history). Read-only; honours
    the same memory scope. Also totals any captured cost estimate (the spend
    lane). Cost is only present at the verbosity the folder's policy stored."""
    from .memory import WorkspaceMemory
    from .llm_capture import _is_spend_pair, _pair_cost
    actor = actor or _default_actor()
    mem = WorkspaceMemory(folder_context, log_root=_log_root(), actor=actor)
    rows: list[dict[str, Any]] = []
    spend = 0.0
    # One pass; the read and the cost-cap enforcement share the SAME predicate
    # (_is_spend_pair) and cost rule (_pair_cost), so the readable spend and the
    # enforced spend can never drift. The row shows the raw recorded facet (for
    # display); the total uses the validated contribution (no NaN/negative).
    for p in mem.all_pairs():
        prob = p.get("problem") or {}
        if not _is_spend_pair(prob):
            continue
        facets = prob.get("facets") or {}
        spend += _pair_cost(prob)
        rows.append({
            "id": p.get("id"),
            "scope": prob.get("scope") or prob.get("type") or "",
            "summary": prob.get("summary", ""),
            "model": facets.get("model") or facets.get("engine") or "",
            "verbosity": facets.get("verbosity_level", ""),
            "cost_cents": facets.get("cost_estimate_cents"),
        })
    rows = rows[: max(0, int(limit))]
    return {"folder_context": folder_context, "captures": rows,
            "count": len(rows), "spend_cents": round(spend, 4)}


def policy_snapshot(folder_context: str) -> dict[str, Any]:
    """Read the folder's policy and return a transport-friendly snapshot.

    Returns:
        Dict with:
        - ``privacy_lock_enabled``: raw boolean from the file
        - ``oversight_enabled``: raw boolean
        - ``oversight_default_level``: e.g. ``"approve"``
        - ``lock_is_active``: belt-and-braces effective state
          (True if no opt-out acknowledgement exists, regardless of boolean)
        - ``oversight_is_active``: same belt-and-braces logic for oversight
        - ``acknowledgements``: list of which protections were opt-ed out,
          each with ``accepted_at`` / ``accepted_by`` / ``disclaimer_version``
          / ``reason``
        - ``folder_context``: the folder this snapshot describes
    """
    policy = load_policy(folder_context)
    return {
        "folder_context": str(Path(folder_context).expanduser().resolve()),
        "privacy_lock_enabled": policy.privacy_lock_enabled,
        "oversight_enabled": policy.oversight_enabled,
        "oversight_default_level": policy.oversight_default_level,
        "lock_is_active": policy.lock_is_active,
        "lock_mode": policy.lock_mode,
        "lock_mode_explicit": policy.lock_mode_explicit,
        "oversight_is_active": policy.oversight_is_active,
        # AI-training (TDM) opt-out state, so the Policy drawer can show it
        # truthfully instead of guessing (the set op already returns it).
        "ai_training_optout": policy.ai_training_optout,
        # Tier-M moderation rules so the cross-process (MCP) policy snapshot carries
        # them too — otherwise the gate's moderation layer silently no-ops over MCP.
        "moderation_rules": policy.moderation_rules,
        "acknowledgements": {
            k: {
                "accepted_at": v.accepted_at,
                "accepted_by": v.accepted_by,
                "disclaimer_version": v.disclaimer_version,
                "reason": v.reason,
            }
            for k, v in policy.acknowledgements.items()
        },
        "at_rest": _at_rest_state(folder_context),
    }

def _at_rest_state(folder_context: str) -> dict[str, Any]:
    """At-rest Workspace Lock state (sealed/unlocked/wall) for snapshots + UI."""
    try:
        from . import workspace_lock
        return workspace_lock.state(folder_context, log_root=_log_root())
    except Exception as e:
        return {"sealed": False, "unlocked": False, "wall": "down", "error": str(e)}

def _workspace_lock_guard(folder_context: str):
    """Read-tool guard for the Workspace Lock. Returns one of:
      ("open", None)               — not sealed; proceed with the normal disk read
      ("locked", resp_dict)        — sealed + locked; caller returns resp_dict
      ("served", {pid: pair})      — sealed + unlocked; serve from memory
    Never raises. Fails closed: if the seal state cannot be determined, it
    returns "locked" (refuse), never "open" — an internal error must not
    downgrade a possibly-sealed workspace to a direct disk read.
    """
    try:
        from . import workspace_lock
        lr = _log_root()
        if not workspace_lock.is_sealed(folder_context, log_root=lr):
            return ("open", None)
        resolved = str(Path(folder_context).expanduser().resolve())
        if not workspace_lock.is_unlocked(folder_context, log_root=lr):
            return ("locked", {"folder_context": resolved, "locked": True,
                               "detail": "workspace is locked — workspace_lock_unlock to read it, "
                                         "or unseal for direct access"})
        return ("served", workspace_lock.read_pairs(folder_context, log_root=lr))
    except Exception as e:
        try:
            resolved = str(Path(folder_context).expanduser().resolve())
        except Exception:
            resolved = str(folder_context)
        return ("locked", {"folder_context": resolved, "locked": True,
                           "detail": "workspace lock state could not be determined; refusing "
                                     "the read (fail-closed). Retry, or unseal for direct access.",
                           "error": str(e)})

def _rank_served(pairs: dict, query, k: int) -> list:
    """Cheap keyword rank over served pairs (used only for sealed workspaces; the
    on-disk path keeps the engine's real ranking)."""
    q = {t for t in str(query).lower().split() if t}
    def score(p):
        text = " ".join([
            str((p.get("problem") or {}).get("summary", "")),
            str((p.get("solution") or {}).get("body", "")),
        ]).lower()
        return len(q & set(text.split()))
    ranked = sorted(pairs.values(), key=score, reverse=True)
    hit = [p for p in ranked if score(p) > 0][:k]
    return hit if hit else list(pairs.values())[:k]

def workspace_lock_unlock(folder_context: str, passphrase: str) -> dict[str, Any]:
    """Unlock a sealed workspace for this session: verify the passphrase and hold the
    derived key in memory so the workspace can be served (read-through) without
    unsealing it to disk. Wrong passphrase fails cleanly; never writes plaintext."""
    from . import workspace_lock
    lr = _log_root()
    try:
        out = workspace_lock.unlock(folder_context, passphrase=passphrase, log_root=lr)
        return {"ok": True, **out, **workspace_lock.state(folder_context, log_root=lr)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def workspace_lock_lock(folder_context: str) -> dict[str, Any]:
    """Re-lock a workspace for reading: drop the in-memory session key. The on-disk
    store stays sealed. (Full direct access is the separate `unseal` escape.)"""
    from . import workspace_lock
    lr = _log_root()
    out = workspace_lock.lock(folder_context, log_root=lr)
    return {"ok": True, **out, **workspace_lock.state(folder_context, log_root=lr)}

def lock_setup_status(config_path: str = "") -> dict[str, Any]:
    """The persisted lock onboarding config, read-only. ``configured`` is the
    drawer's setup-CTA signal (setup never completed → offer setup); backend
    health belongs to the model-status op, not here."""
    from .lock import default_config_path, load_config
    p = Path(config_path) if config_path else default_config_path()
    cfg = load_config(p)
    return {"ok": True,
            "configured": bool(cfg.setup_completed_at),
            "backend_spec": cfg.backend_spec,
            "default_mode": cfg.default_mode,
            "default_oversight": cfg.default_oversight,
            "audit_log_path": cfg.audit_log_path or None,
            "config_path": str(p),
            "config_exists": p.exists()}

def lock_setup_run(backend_spec: str = "", audit_log_path: str = "",
                   skip_smoke_test: bool = False, config_path: str = "",
                   accepted_by: str = "", reason: str = "") -> dict[str, Any]:
    """Run the lock onboarding wizard headlessly (the same stages as the CLI
    ``agent-tool-lock setup``): detect environment, discover models, choose the
    backend (empty ``backend_spec`` accepts the recommendation), smoke test,
    persist config, apply to the running process.

    Replacing a real backend with ``mock`` weakens Tier C from fail-closed to
    permissive, so that transition — requested outright, or reached via the
    wizard's smoke-test fallback — requires ``accepted_by`` + ``reason`` and is
    otherwise refused with the prior config kept."""
    import io
    from .lock import (apply_config_to_env, default_config_path,
                                         load_config, save_config)
    from .lock import run_wizard
    from .lock import reset_backend_cache

    path = Path(config_path) if config_path else default_config_path()
    prior = load_config(path)
    prior_real = bool(prior.setup_completed_at) and prior.backend_spec != "mock"
    acknowledged = bool((accepted_by or "").strip()) and bool((reason or "").strip())

    if prior_real and (backend_spec or "").strip() == "mock" and not acknowledged:
        return {"ok": False,
                "error": "replacing a real backend with mock weakens the semantic "
                         "scan to permissive; pass accepted_by and reason to accept"}

    out = io.StringIO()
    result = run_wizard(stdout=out,
                        auto_answers=[(backend_spec or "").strip(),
                                      (audit_log_path or "").strip()],
                        skip_smoke_test=bool(skip_smoke_test),
                        config_path=path)

    if prior_real and result.config.backend_spec == "mock" and not acknowledged:
        # The wizard fell back to mock (smoke failure); persisting that would
        # silently weaken protection. Keep the prior config instead.
        save_config(prior, path=path)
        apply_config_to_env(prior)
        reset_backend_cache()
        return {"ok": False,
                "error": "smoke test failed and the fallback would replace a real "
                         "backend with mock; prior config kept. Pass accepted_by "
                         "and reason to accept the weaker backend.",
                "notes": list(result.notes or []),
                "transcript": out.getvalue()[-4000:]}

    return {"ok": bool(result.completed),
            "backend_spec": result.config.backend_spec,
            "smoke_test_passed": bool(result.smoke_test_passed),
            "smoke_results": list(result.smoke_test_results or []),
            "notes": list(result.notes or []),
            "config_path": str(result.config_path),
            "accepted_by": (accepted_by or "").strip() or None,
            "reason": (reason or "").strip() or None,
            "transcript": out.getvalue()[-4000:]}

def recent(folder_context: str, limit: int = 20, actor: str = "") -> dict[str, Any]:
    """Return up to ``limit`` recent live pairs in scope. Diagnostic / admin use.

    Honours the asymmetric rule + ancestor-distributed pairs. M5/A6: when the folder
    opts into access control, a read by an unauthorized actor is denied (fail-closed).
    """
    actor = actor or _default_actor()
    if not check_access(folder_context, actor, "read", log_root=_log_root()):
        return {"error": "access denied",
                "folder_context": str(Path(folder_context).expanduser().resolve()),
                "count": 0, "results": []}
    limit = max(1, min(int(limit), 200))
    resolved = str(Path(folder_context).expanduser().resolve())
    # Workspace Lock: a sealed workspace is served from memory when unlocked, and refused
    # (not silently empty) when locked. Unsealed workspaces skip this entirely.
    _state, _payload = _workspace_lock_guard(folder_context)
    if _state == "locked":
        return {**_payload, "count": 0, "results": []}
    if _state == "served":
        served = list(_payload.values())
        return {"folder_context": resolved, "served_sealed": True,
                "count": len(served[:limit]), "results": served[:limit]}
    mem = WorkspaceMemory(folder_context, log_root=_log_root(), actor=actor)
    pairs = mem.all_pairs()
    # all_pairs() already filters deleted; just cap the count.
    return {
        "folder_context": resolved,
        "count": len(pairs[:limit]),
        "results": pairs[:limit],
    }

def lane_capabilities(
    folder_context: str,
    actor: str,
    kinds: list[str] | None = None,
    risks: list[str] | None = None,
) -> dict[str, Any]:
    """Read-only agent-facing projection of ONE agent's governance-lane
    boundaries: per candidate (kind, risk) the verdict the gate would dispose,
    the grade required, the escalation point, and the governing guard.

    A projection of the same .lg policy the gate enforces — advisory, never
    dispositive — stamped with the policy_fingerprint it reflects. Access-gated
    like the other governed reads (check_access, fail-closed); appends nothing
    to the mutation log. Fail-closed throughout: a denied or unreadable read
    yields NO capabilities, never "everything allowed".
    """
    if not check_access(folder_context, actor, "read", log_root=_log_root()):
        from .lane_capabilities import SCHEMA_KIND
        return {"ok": False, "kind": SCHEMA_KIND,
                "folder_context": str(Path(folder_context).expanduser().resolve()),
                "actor": actor, "advisory": True, "readable": False,
                "reason": "access denied", "capabilities": []}
    from .lane_capabilities import lane_capabilities as _project
    return _project(folder_context, actor, kinds=kinds, risks=risks,
                    log_root=_log_root())


_FS_SKIP_DIRS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules",
    ".git", ".idea", ".vscode", ".venv", "venv", ".tox",
    "build", "dist", ".eggs",
}

def list_folder(path: str) -> dict[str, Any]:
    """List a directory's contents — subdirectories and (separately) files.

    Used by the dashboard's file explorer to navigate the user's actual
    filesystem. Skips hidden entries (starting with ``.``) and common
    tool/system directories. Returns a transport-friendly dict.

    Args:
        path: Absolute path to a directory.

    Returns:
        Dict with:
        - ``path``: the resolved absolute path
        - ``home``: user's home directory (helpful for breadcrumbs)
        - ``parent``: parent directory (or empty string if at root)
        - ``subfolders``: list of subdirectory names (no path prefix)
        - ``files``: list of regular-file names
        - ``error``: present only when the listing failed
    """
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return {"path": str(p), "error": "not found"}
        if not p.is_dir():
            return {"path": str(p), "error": "not a directory"}
        subfolders = []
        files = []
        for entry in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if entry.name in _FS_SKIP_DIRS:
                    continue
                subfolders.append(entry.name)
            elif entry.is_file():
                files.append(entry.name)
        parent = "" if str(p) == "/" else str(p.parent)
        return {
            "path": str(p),
            "home": str(Path.home()),
            "parent": parent,
            "subfolders": subfolders,
            "files": files,
        }
    except PermissionError as e:
        return {"path": path, "error": f"permission denied: {e}"}
    except OSError as e:
        return {"path": path, "error": f"os error: {e}"}

def create_folder(path: str) -> dict[str, Any]:
    """Create a directory (including parents). No-op if it already exists.

    Args:
        path: Absolute path of the directory to create.

    Returns:
        Dict with ``path``, ``created`` (bool: True if newly created),
        or ``error``.
    """
    try:
        p = Path(path).expanduser().resolve()
        already = p.exists()
        p.mkdir(parents=True, exist_ok=True)
        return {"path": str(p), "created": not already}
    except PermissionError as e:
        return {"path": path, "error": f"permission denied: {e}"}
    except OSError as e:
        return {"path": path, "error": f"os error: {e}"}

def pair_safe_context(
    folder_context: str,
    pair_id: str,
    mode: str = "auto",
    actor: str = "",
) -> dict[str, Any]:
    """Boundary-respecting view of a single pair, ready for cloud-LLM prompts.

    Returns the layered safe view (fingerprint + triples + optionally
    lock-redacted spans) — never raw confidential content from a
    lock-ON folder. The caller (artifact, agent, skill) builds its
    prompt from this view, not from ``recent()``/``by_id()``.

    The response crosses an egress boundary, so it is wrapped in a
    ``ScannedResponse`` and the runtime egress guard asserts the wrap is
    in place. Returning a raw dict from this tool is a build error.

    Args:
        folder_context: workspace path (asymmetric rule applies).
        pair_id:        the pair to view.
        mode:           'auto' | 'fingerprint_only' | 'triples_only' |
                        'safe_minimal' (fingerprint + triples) |
                        'safe_full'    (fingerprint + triples + anonymised
                        spans). 'auto' resolves from folder policy.

    Returns:
        ``{found: bool, mode_applied, view: {fingerprint, triples,
                                             anonymised_spans, lock},
           _lock_egress: {tier, total_findings, ...}}``
    """
    actor = actor or _default_actor()
    resolved = _resolve_mode_for_folder(folder_context, mode)
    if not check_access(folder_context, actor, "read", log_root=_log_root()):
        # Wrapped: this op crosses an egress boundary, so even a denial must be
        # a ScannedResponse (the runtime egress guard asserts the wrap).
        return _wrap_scanned({"found": False, "pair_id": pair_id,
                              "mode_applied": resolved, "error": "access denied"})
    mem = WorkspaceMemory(folder_context, log_root=_log_root(), actor=actor)
    pair = mem.by_id(pair_id)
    if pair is None:
        return _wrap_scanned({
            "found": False, "pair_id": pair_id, "mode_applied": resolved,
        })
    view = _safe_view(pair, folder_context, resolved)
    return _wrap_scanned({
        "found": True, "pair_id": pair_id,
        "mode_applied": resolved, "view": view,
    }, views=[view])

def pairs_safe_context_for_query(
    folder_context: str,
    query: str,
    k: int = 8,
    mode: str = "auto",
    source_paths: list = None,
    actor: str = "",
) -> dict[str, Any]:
    """Search + safe-context in one round-trip — the chat-with-folder spine.

    Runs the folder's keyword search for ``query``, takes the top ``k``
    matches, and returns each pair's safe-context view honouring the
    folder's lock policy. Falls back to ``recent()`` when search returns
    nothing.

    Args:
        folder_context: workspace path.
        query: free-text query.
        k: max number of views to return.
        mode: safe-context mode (see pair_safe_context).
        source_paths: optional list of source-document paths to constrain
            retrieval to. Empty / None = no filter. When non-empty, only
            pairs whose ``problem.source_document`` is in this list are
            returned. Powers the Workspace artifact's per-source
            checkboxes — lets the user say "answer using only these
            three PDFs". Best-effort: if no pair matches the filter, the
            fallback recent() pool is also filtered before being used.

    Returns:
        ``{folder_context, mode_applied, query, count, views: [safe-view]}``.
        Each safe-view follows the schema documented on ``pair_safe_context``.
    """
    actor = actor or _default_actor()
    if not check_access(folder_context, actor, "read", log_root=_log_root()):
        # Crosses the egress boundary → even a denial is a ScannedResponse.
        return _wrap_scanned({"error": "access denied",
                              "folder_context": str(Path(folder_context).expanduser().resolve()),
                              "query": query, "count": 0, "views": []})
    resolved = _resolve_mode_for_folder(folder_context, mode)
    k = max(1, min(int(k), 50))
    _st, _pl = _workspace_lock_guard(folder_context)
    if _st == "locked":
        return {**_pl, "mode_applied": resolved, "query": query, "count": 0, "views": []}
    mem = WorkspaceMemory(folder_context, log_root=_log_root(), actor=actor)
    # Resolve source_paths into a set of resolved absolute paths for cheap O(1)
    # membership checks. Empty / None means no filter.
    src_filter: set[str] = set()
    if isinstance(source_paths, list) and source_paths:
        for sp in source_paths:
            if isinstance(sp, str) and sp:
                try:
                    src_filter.add(str(Path(sp).expanduser().resolve()))
                except Exception:
                    src_filter.add(sp)

    def _pair_in_filter(p: dict[str, Any]) -> bool:
        if not src_filter:
            return True
        sd = ((p.get("problem") or {}).get("source_document") or "")
        if not sd:
            return False
        try:
            return str(Path(sd).expanduser().resolve()) in src_filter
        except Exception:
            return sd in src_filter

    if _st == "served":
        # sealed + unlocked: serve pairs from memory, rank, then apply the SAME
        # per-pair safe-context (shield) view used on the unsealed path. The
        # disk store stays ciphertext; only decrypted bytes in memory are used.
        served_pairs = [pp for pp in _pl.values() if _pair_in_filter(pp)]
        ranked = _rank_served({i: pp for i, pp in enumerate(served_pairs)},
                              query, max(k * 3, k))
        hint = classify_query_dimension(query)
        ranked = _rerank_by_dimension(ranked, hint)
        views = [_safe_view(pp, folder_context, resolved) for pp in ranked[:k]]
        return _wrap_scanned({
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "mode_applied": resolved, "served_sealed": True,
            "count": len(views),
            "dimension_hint": hint.value if hint is not None else None,
            "views": views,
        }, views=views)

    # Replicate the search() tool's logic so we don't go through a second
    # MCP hop; keep behaviour aligned with the rest of the surface.
    results = mem.search({"summary": query, "facets": {"keywords": query}},
                         k=max(k * 3, k))  # over-fetch so filter has headroom
    pairs: list[dict[str, Any]] = []
    for r in results:
        pid = (r.get("problem") or {}).get("id")
        body = r.get("body")
        if isinstance(body, dict) and "pair_id" in body:
            full = mem.by_id(body["pair_id"])
            if full is not None and _pair_in_filter(full):
                pairs.append(full)
                continue
        if pid:
            full = mem.by_id(r.get("id") or pid)
            if full is not None and _pair_in_filter(full):
                pairs.append(full)
                continue
        # last-resort: treat the search hit itself as the pair
        if r.get("id") and _pair_in_filter(r):
            pairs.append(r)
    if not pairs:
        # Fallback: recent pool, also filtered.
        fallback = [p for p in mem.all_pairs() if _pair_in_filter(p)]
        pairs = fallback[:k]

    # Dimension-guided re-rank: if the question leans toward a reasoning
    # dimension, gently promote pairs whose edges carry it (see below).
    hint = classify_query_dimension(query)
    pairs = _rerank_by_dimension(pairs, hint)

    views = [_safe_view(p, folder_context, resolved) for p in pairs[:k]]
    # Do NOT echo the query string back in the response. Echoing would let a
    # prompt-injection attacker prime the LLM via the next turn: their
    # injection appears in the response that becomes context for the next
    # call. Caller already has their own query string client-side.
    return _wrap_scanned({
        "folder_context": str(Path(folder_context).expanduser().resolve()),
        "mode_applied": resolved,
        "count": len(views),
        "dimension_hint": hint.value if hint is not None else None,
        "views": views,
    }, views=views)

def workspace_remember(
    folder_context: str,
    subject: str,
    predicate: str,
    object: str,
    dimension: str = "",
    confidence: float = 1.0,
    source: str = "",
) -> dict[str, Any]:
    """Remember a fact as a typed triple (subject, predicate, object).

    A triple is stored as a dimensioned edge inside a fact pair, so it lands on
    the signed log (auditable), is found by ``pairs_search``,
    and is composed by ``reason``. The edge's dimension is taken from
    ``dimension`` if given, otherwise inferred from the predicate
    (``classify_predicate``). The pair id is derived from the triple, so
    remembering the same triple twice is idempotent.
    """
    import hashlib
    if not (subject and predicate and object):
        return {"remembered": False, "error": "subject, predicate, and object are required"}
    valid = {d.value for d in Dimension}
    dim = dimension if dimension in valid else classify_predicate(predicate).value
    folder_abs = str(Path(folder_context).expanduser().resolve())
    h = hashlib.sha256(f"{folder_abs}|{subject}|{predicate}|{object}".encode("utf-8")).hexdigest()[:32]
    pid = f"sha256:fact-{h}"
    conf = max(0.0, min(1.0, float(confidence)))
    pair = {
        "id": pid,
        "problem": {
            "id": f"{pid}-p", "scope": "fact", "type": "triple",
            "summary": f"{subject} {predicate} {object}",
            "facets": {"subject": subject, "predicate": predicate,
                       "object": object, "dimension": dim, "source": source},
        },
        "solution": {
            "id": pid, "problem_id": f"{pid}-p",
            "body": f"{subject} {predicate} {object}", "body_format": "triple",
            "authority_tier": 4, "confidence": conf,
        },
        "edges": [{"subject": subject, "predicate": predicate,
                   "object": object, "dimension": dim}],
    }
    mem = WorkspaceMemory(folder_context, log_root=_log_root(), actor=_default_actor())
    pair_id = mem.remember(pair, channel="fact")
    # Knowledge plane: the triple is a runtime fact — write it into the folder's
    # Versum store so it is a first-class node/relation reachable by
    # workspace_query / reason (closing the remember->query loop). The signed
    # mutation-log event above remains the audit record; Versum holds the
    # knowledge. Best-effort: a Versum write failure must not lose the audited
    # remember.
    try:
        from .adapters.versum import append_fact as _append_fact
        store = Path(folder_abs) / ".versum"
        store.mkdir(parents=True, exist_ok=True)
        _append_fact(store, subject=subject, predicate=predicate,
                     object=object, dimension=dim, actor=_default_actor())
    except Exception:
        pass
    return {
        "remembered": True,
        "pair_id": pair_id,
        "triple": {"subject": subject, "predicate": predicate,
                   "object": object, "dimension": dim, "confidence": conf},
    }

def workspace_query(
    folder_context: str,
    subject: str = "",
    predicate: str = "",
    object: str = "",
    limit: int = 50,
    actor: str = "",
) -> dict[str, Any]:
    """Query the folder's triples (subject, predicate, object).

    Returns every stored edge matching the given components — each of
    ``subject`` / ``predicate`` / ``object`` is an exact filter, and an empty
    string is a wildcard. Reads across all of the folder's pairs (asserted
    facts, extracted edges, and derived inferences alike), so it is the
    triple-level view over everything the folder knows. Each result carries its
    dimension and the id of the pair it came from (provenance). M5/A6: gated by
    check_access when the folder opts into access control (fail-closed).
    """
    actor = actor or _default_actor()
    if not check_access(folder_context, actor, "read", log_root=_log_root()):
        return {"error": "access denied",
                "folder_context": str(Path(folder_context).expanduser().resolve()),
                "count": 0, "triples": []}
    from .adapters.versum import VersumKnowledgeStore, VersumSolverSource
    # Versum is the ONLY knowledge plane; fail-closed on an unindexed workspace
    # ("index the folder first") rather than serving a non-Versum overlay.
    knowledge = VersumKnowledgeStore(folder_context)
    if not knowledge.has_records:
        return {
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "knowledge_backend": None,
            "error": "versum index required — index the folder with "
                     "loomground-versum before querying",
            "count": 0, "triples": [],
        }
    backend = "loomground-versum"
    edges = VersumSolverSource(knowledge).edges()
    out = []
    for e in edges:
        if subject and e.subject != subject:
            continue
        if predicate and e.predicate != predicate:
            continue
        if object and e.object != object:
            continue
        out.append({
            "subject": e.subject, "predicate": e.predicate, "object": e.object,
            "dimension": e.dimension.value, "weight": e.weight,
            "source_pair": e.source_pair,
        })
        if len(out) >= max(1, min(int(limit), 1000)):
            break
    return {
        "folder_context": str(Path(folder_context).expanduser().resolve()),
        "knowledge_backend": backend,
        "count": len(out),
        "triples": out,
    }

_UNSAFE_FILENAME_CHARS = '/\x00'      # forward slash + nulls (never permitted)

def _sanitise_filename(name: str) -> str:
    """Strip path components and unsafe characters from a user-supplied name.

    - Forward slashes and nulls are rejected (would escape the folder).
    - Backslashes are normalised to underscores.
    - Leading dots are stripped (no surprise hidden files).
    - Empty → ``"untitled"``.
    """
    if not name:
        return "untitled"
    # Take only the basename, no directory traversal.
    name = name.replace("\\", "_")
    name = name.split("/")[-1]
    for c in _UNSAFE_FILENAME_CHARS:
        name = name.replace(c, "_")
    name = name.lstrip(".")
    return name or "untitled"

def ingest_path(
    folder_context: str,
    file_path: str,
) -> dict[str, Any]:
    """Ingest a single file into a folder's L0 memory.

    Runs the default extractor over the file, dispatches to any registered
    domain NDs (GDPR / AI Act / music-rights / contracts), and writes the
    resulting pairs into the folder. Idempotent: re-ingesting the same
    file is a no-op (returns ``pair_ids: []``).

    Args:
        folder_context: workspace path that owns the memory.
        file_path:      absolute path of the file to ingest. Must already
                        exist on disk (use ``write_file_to_folder`` first).

    Returns:
        ``{ingested: True, pair_ids, count}`` or ``{error}``.
    """
    try:
        from .inbox_watcher import ingest_file
        pair_ids = ingest_file(
            file_path=file_path,
            folder_context=folder_context,
            log_root=_log_root(),
            actor=_default_actor(),
            extractor=_make_full_extractor(),
        )
        from .ingest.versum import ingest_into_versum
        graph_ingest = ingest_into_versum(file_path, folder_context)
        result = {
            "ingested": True,
            "file_path": str(Path(file_path).expanduser().resolve()),
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "pair_ids": pair_ids,
            "count": len(pair_ids),
            "idempotent_noop": len(pair_ids) == 0,
            "versum": graph_ingest.get("write", graph_ingest),
        }
        # Best-effort: register any legal instruments the file cites into the
        # folder's legal-entity corpus (grows the retrievable URL corpus). A
        # corpus hiccup must never affect the ingest itself.
        try:
            from .corpus.ingest import register_into_corpus
            text = Path(file_path).expanduser().read_text(
                encoding="utf-8", errors="ignore")
            corpus = register_into_corpus(folder_context, text, log_root=_log_root())
            if corpus.get("found"):
                result["corpus"] = corpus
            # place each span-norm (clause/rule) onto the legal map, per workspace+user
            from .rule_registry import place_into_registry
            rules = place_into_registry(folder_context, text,
                                        source_document=str(file_path),
                                        log_root=_log_root())
            if rules.get("count"):
                result["rules"] = {"count": rules["count"],
                                   "created": rules.get("created", 0)}
        except Exception:                                      # noqa: BLE001
            pass
        return result
    except FileNotFoundError as e:
        return {"error": f"not a file: {e}"}
    except PermissionError as e:
        return {"error": f"permission denied: {e}"}
    except Exception as e:
        return {"error": f"ingest failed: {type(e).__name__}: {e}"}

def ingest_url(
    folder_context: str,
    url: str,
    actor: str = "",
    allow_robots_override: bool = False,
    block_on_tdm_reservation: bool = False,
) -> dict[str, Any]:
    """Save a user-chosen URL to the workspace and fetch its content (robots-permitting).

    The URL is always recorded in ``<folder>/sources/urls.jsonl`` — even when
    robots disallows or the fetch fails. On success the content is written
    under ``<folder>/sources/<host>/`` with provenance front-matter and run
    through the full extractor stack (domain NDs + erase-guard), exactly like
    a dropped file.

    Args:
        folder_context: workspace path that owns the memory.
        url: an http(s) URL the user chose (typed / dragged / pasted).
        actor: actor id (defaults to the configured L0 actor).
        allow_robots_override: if True, fetch even when robots disallows. The
            override is recorded in the ledger row for audit.
        block_on_tdm_reservation: if True, refuse ingest when a machine-readable
            Art. 4 DSM opt-out (``tdm-reservation`` / ``X-Robots-Tag: noai``)
            is present. Default False (record-only).

    Returns:
        The ledger row: ``{url, state, robots_allowed, tdm_reservation,
        http_status, content_hash, saved_path, pair_ids, ...}``. ``state`` is
        one of ``fetched / unchanged / robots_blocked / tdm_reserved /
        fetch_error``.
    """
    try:
        from .url_ingest import ingest_url as _ingest_url
        return _ingest_url(
            folder_context=folder_context,
            url=url,
            actor=actor or _default_actor(),
            log_root=_log_root(),
            extractor=_make_full_extractor(),
            allow_robots_override=allow_robots_override,
            block_on_tdm_reservation=block_on_tdm_reservation,
        )
    except Exception as e:  # noqa: BLE001
        return {"url": url, "state": "fetch_error",
                "error": f"{type(e).__name__}: {e}"}

def list_urls(folder_context: str) -> dict[str, Any]:
    """Return the workspace's saved-URL watchlist (``sources/urls.jsonl``).

    Returns ``{folder_context, count, urls: [row, ...]}`` where each row is the
    newest record for that URL (state, provenance, pair_ids).
    """
    try:
        from .url_ingest import read_ledger
        rows = read_ledger(folder_context)
        return {"folder_context": folder_context, "count": len(rows), "urls": rows}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}

def policy_set_lock_mode(
    folder_context: str,
    mode: str,
    accepted_by: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Set the tri-state lock_mode for a folder.

    Args:
        folder_context: workspace path.
        mode: one of "clean_room_with_algo" / "clean_room" / "off".
        accepted_by: required when transitioning to a less-protective state.
        reason: explanation, persisted in the acknowledgement audit record.

    Returns:
        ``{ok, mode, lock_mode, lock_is_active, oversight_is_active,
            oversight_default_level}``.
    """
    try:
        from .policy import set_lock_mode
        actor = accepted_by or _default_actor()
        pol = set_lock_mode(
            folder_context, mode,
            accepted_by=actor, reason=reason, log_root=_log_root(),
        )
        return {
            "ok": True,
            "mode_requested":          mode,
            "lock_mode":             pol.lock_mode,
            "lock_is_active":        bool(pol.lock_is_active),
            "oversight_is_active":     bool(pol.oversight_is_active),
            "oversight_default_level": pol.oversight_default_level,
            "accepted_by":             actor,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def policy_enable_lock(folder_context: str) -> dict[str, Any]:
    """Turn Privacy Lock ON for the folder. No disclaimer required —
    enabling protection is the safer direction.

    Returns ``{ok, lock_is_active, oversight_is_active, oversight_default_level}``.
    """
    try:
        from .policy import enable_lock
        pol = enable_lock(folder_context, actor=_default_actor(),
                            log_root=_log_root())
        return {
            "ok": True,
            "lock_is_active":         bool(pol.lock_is_active),
            "oversight_is_active":      bool(pol.oversight_is_active),
            "oversight_default_level":  pol.oversight_default_level,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def policy_disable_lock(
    folder_context: str,
    accepted_by: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Turn Privacy Lock OFF for the folder.

    Disabling a protection is a governed step: it REQUIRES an explicit
    ``accepted_by`` AND a non-empty ``reason``, both recorded in the
    acknowledgement. There is NO silent default — an empty ``accepted_by`` is
    refused here rather than substituted with a system actor, so the MCP path
    cannot disable a protection without attribution.
    """
    if not (accepted_by or "").strip() or not (reason or "").strip():
        return {"ok": False, "error": "accepted_by and reason are required to disable a protection (no silent disable)"}
    try:
        from .policy import disable_lock
        pol = disable_lock(folder_context, accepted_by=accepted_by,
                             reason=reason, log_root=_log_root())
        return {
            "ok": True,
            "lock_is_active":         bool(pol.lock_is_active),
            "oversight_is_active":      bool(pol.oversight_is_active),
            "oversight_default_level":  pol.oversight_default_level,
            "accepted_by": accepted_by,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def policy_enable_oversight(folder_context: str) -> dict[str, Any]:
    """Turn Oversight prompts ON for the folder."""
    try:
        from .policy import enable_oversight
        pol = enable_oversight(folder_context, actor=_default_actor(),
                               log_root=_log_root())
        return {
            "ok": True,
            "lock_is_active":         bool(pol.lock_is_active),
            "oversight_is_active":      bool(pol.oversight_is_active),
            "oversight_default_level":  pol.oversight_default_level,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def policy_disable_oversight(
    folder_context: str,
    accepted_by: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Turn Oversight prompts OFF for the folder. Same governed rule as
    ``policy_disable_lock``: explicit ``accepted_by`` + non-empty ``reason``
    required, no silent default."""
    if not (accepted_by or "").strip() or not (reason or "").strip():
        return {"ok": False, "error": "accepted_by and reason are required to disable a protection (no silent disable)"}
    try:
        from .policy import disable_oversight
        pol = disable_oversight(folder_context, accepted_by=accepted_by,
                                reason=reason, log_root=_log_root())
        return {
            "ok": True,
            "lock_is_active":         bool(pol.lock_is_active),
            "oversight_is_active":      bool(pol.oversight_is_active),
            "oversight_default_level":  pol.oversight_default_level,
            "accepted_by": accepted_by,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def policy_enable_discipline(folder_context: str, manifest: str = "") -> dict[str, Any]:
    """Turn the discipline gate ON for the folder — the third dial beside
    Lock and Oversight (code/text conformance). ``manifest`` optionally names
    a rule file (relative to the folder or absolute); empty uses the built-in
    default. No disclaimer — enabling a quality gate raises rigour."""
    try:
        from .policy import enable_discipline
        pol = enable_discipline(folder_context, manifest=manifest,
                                actor=_default_actor(), log_root=_log_root())
        return {"ok": True, "discipline_enabled": bool(pol.discipline_enabled),
                "discipline_manifest": pol.discipline_manifest}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def policy_disable_discipline(folder_context: str) -> dict[str, Any]:
    """Turn the discipline gate OFF for the folder. No disclaimer — a quality
    gate is not a protection, so disabling it carries no acknowledgement."""
    try:
        from .policy import disable_discipline
        pol = disable_discipline(folder_context, actor=_default_actor(),
                                 log_root=_log_root())
        return {"ok": True, "discipline_enabled": bool(pol.discipline_enabled)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def discipline_audit(
    folder_context: str,
    mode: str = "audit",
    files: list[str] | None = None,
    manifest: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Run the discipline gate over a folder and record the run on the audit
    chain.

    ``mode``: ``audit`` (whole tree), ``diff`` (changed + new vs HEAD), or
    ``check`` (the explicit ``files`` list). ``manifest`` overrides the rule
    file (else the folder's policy manifest, else the built-in default).
    ``strict`` makes warnings count as failures.

    Returns ``{ok, mode, scanned, failures, warnings, clean, findings, audit}``.
    """
    try:
        from .discipline import run_discipline
        res = run_discipline(folder_context, mode=mode, files=files,
                             manifest=manifest, write_audit=True,
                             log_root=_log_root(), strict=strict)
        res["ok"] = "error" not in res
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def _make_full_extractor():
    """Build the production extractor stack:

      FormatAwareExtractor (PDF/DOCX/Pages text reader)
        wrapped in
      RoutingExtractor (classifier + ND fan-out)
        with these NDs registered:
          - gdpr / ai-act / music-rights / contracts (deontic-rule NDs)
          - DefinitionExtractor (X means Y → kind=definition)
          - ArticleReferenceExtractor (Article N → kind=article-reference)
          - DocumentSummaryExtractor (one mental model per doc → kind=doc-summary)

    Together these populate the KG with mental models at multiple
    granularities. The chat-with-folder LLM reads only the KG triples
    (Clean-Team default); the document body is included only when the
    user has explicitly turned the lock off for the folder.
    """
    # Single source of truth: the shared builder in nd_routing. Kept as a thin
    # wrapper so existing call sites (ingest_path, scan_folder) don't change.
    from .nd_routing import make_full_extractor
    return make_full_extractor()

def scan_folder(folder_context: str) -> dict[str, Any]:
    """One-shot scan + ingest of a workspace folder.

    Walks the folder (or its ``Inbox/`` subdir if present), reads each
    new file with the FormatAwareExtractor (PDF/DOCX/Pages/plain-text),
    runs the deontic classifier, fans out to registered domain NDs
    (GDPR / AI-Act / music-rights / contracts) — each producing typed
    Problem/Solution pairs in the folder's L0 memory.

    Idempotent: files already ingested (by SHA-256 file hash) are skipped.

    Returns ``{ok, scan_path, new_pair_ids, count}``.
    """
    try:
        from .inbox_watcher import InboxWatcher
        watcher = InboxWatcher(
            folder_context,
            log_root=_log_root(),
            actor=_default_actor(),
            extractor=_make_full_extractor(),
        )
        new_ids = watcher.run_once()
        return {
            "ok": True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "scan_path": str(watcher.scan_path),
            "new_pair_ids": new_ids,
            "count": len(new_ids),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def fetch_pair_spans(
    folder_context: str,
    pair_ids: list[str],
    mode: str = "lock_default",
) -> dict[str, Any]:
    """Return lock-processed span text for specific pairs.

    The compliance-workflow bridge. When the user is doing single-clause
    compliance evaluation (e.g., "does this clause meet AI Act Art. 5?"),
    they need the OPERATIVE TEXT of specific pairs — not just taxonomy
    triples. This tool returns the spans, gated by the folder's lock
    policy.

    Args:
        folder_context: workspace path.
        pair_ids:       which pairs to fetch spans for.
        mode:           "lock_default" — apply lock per folder's
                        lock_mode policy.
                        "raw" — return raw body (only honoured if the
                        folder's lock_mode is "off"; otherwise treated
                        as "lock_default").

    Returns:
        ``{ok, mode_applied, spans: [{pair_id, body, body_format,
        lock_action, lock_findings, lock_audit}]}``.

    HITL pre-flight in the dashboard surfaces this explicitly to the
    user: "you are about to include the span text of N pairs; review
    each before sending."
    """
    try:
        from .policy import (
            LOCK_MODE_OFF,
        )
        lock_mode = _folder_lock_mode(folder_context)
        mem = WorkspaceMemory(folder_context, log_root=_log_root(), actor=_default_actor())
        spans: list[dict[str, Any]] = []
        for pid in pair_ids:
            pair = mem.by_id(pid)
            if pair is None:
                spans.append({
                    "pair_id": pid,
                    "found": False,
                })
                continue
            solution = pair.get("solution") or {}
            raw_body = solution.get("body") or ""
            body_format = solution.get("body_format", "prose")
            if lock_mode == LOCK_MODE_OFF and mode == "raw":
                # Body crosses raw — user explicitly accepted the risk.
                spans.append({
                    "pair_id": pid, "found": True,
                    "body": raw_body, "body_format": body_format,
                    "lock_action": "off",
                    "lock_findings": 0,
                    "lock_audit": {"notice": "lock off; raw body emitted"},
                })
            else:
                # Default: pass through lock, redact PII.
                r = _lock_string(raw_body, context="")
                spans.append({
                    "pair_id": pid, "found": True,
                    "body": r["text"], "body_format": body_format,
                    "lock_action": r["action"],
                    "lock_findings": r.get("findings", 0),
                    "lock_audit": {
                        "tier": "B",
                        "lock_mode": lock_mode,
                    },
                })
        return _wrap_scanned({
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "lock_mode":    lock_mode,
            "mode_applied":   mode,
            "spans":          spans,
            "count":          len(spans),
        })
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def reextract_folder(folder_context: str) -> dict[str, Any]:
    """Re-run the extractor pipeline over every source file in this folder.

    Use when new extractors land (e.g. Phase M1 mental-model extractors)
    and existing pairs were produced under an older pipeline. The watcher's
    idempotency check (skip-by-file-hash) is bypassed for this run; every
    file gets re-extracted, producing whatever new mental-model pairs the
    current pipeline yields.

    Existing pairs whose ids collide with re-extracted ones get superseded
    via WorkspaceMemory.remember (most-recent-state-wins). Old pairs that no
    longer get re-emitted remain in the log but become stale; the user
    can purge them manually if they want a clean state.

    Returns ``{ok, folder_context, files_reextracted, pair_ids, count}``.
    """
    try:
        from .inbox_watcher import InboxWatcher
        watcher = InboxWatcher(
            folder_context,
            log_root=_log_root(),
            actor=_default_actor(),
            extractor=_make_full_extractor(),
        )
        scan_root = watcher.scan_path
        ext = _make_full_extractor()
        all_pair_ids: list[str] = []
        files_reextracted = 0
        from pathlib import Path
        from .inbox_watcher import ROOT_SKIP_FILES
        for path in sorted(Path(scan_root).rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.name in ROOT_SKIP_FILES:
                continue
            # Skip already-cached / log files
            if any(part.startswith(".") for part in path.parts):
                continue
            try:
                # ingest_file's idempotency check uses the file_hash; for
                # a true re-extract we bypass it by writing pairs directly.
                from .memory import WorkspaceMemory
                mem = WorkspaceMemory(folder_context, log_root=_log_root(),
                               actor=_default_actor())
                extracted = ext.extract(str(path), folder_context)
                for pair in extracted.pairs:
                    pid = mem.remember(pair, channel="document",
                                       source_hash=extracted.file_hash)
                    all_pair_ids.append(pid)
                files_reextracted += 1
            except Exception:
                continue
        return {
            "ok": True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "files_reextracted": files_reextracted,
            "pair_ids": all_pair_ids,
            "count": len(all_pair_ids),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

_LOCK_CLASSIFY_BUCKET: dict[str, list[float]] = {}

_LOCK_CLASSIFY_RATE_LIMIT = int(os.environ.get(
    "LOCK_BETA_CLASSIFY_PER_MINUTE", "100"
))

def lock_classify_text(text: str,
                          folder_context: str = "") -> dict[str, Any]:
    """Run the Tier-B regex PII scan over arbitrary text and return findings.

    Powers the "test what would be redacted" preview in the Privacy Lock
    artifact — the user types a sentence, the tool returns every match (PII
    shape, severity, confidence, regex label) WITHOUT writing to the mutation
    log. No audit event side-effects.

    If ``folder_context`` is supplied, the per-folder confidence threshold
    (``policy.lock_confidence_threshold``) is applied — findings with
    confidence below the threshold are dropped from the response. If empty,
    no threshold filter is applied.

    Args:
        text: free-text to scan. Empty or whitespace-only returns no findings.
        folder_context: optional workspace path to source the threshold from.

    Returns:
        ``{ok, folder_context, threshold, findings: [{type, severity,
            confidence, detail}, ...], findings_count}``.

    0.6.7+: per-folder rate-limited to 100 calls/min (override via
    ``LOCK_BETA_CLASSIFY_PER_MINUTE`` env var). Returns
    ``{ok: false, error: "rate_limited", retry_after_seconds: ...}`` when
    the bucket is full. Defends against pattern-probing enumeration.
    """
    # Rate-limit check (per-folder; calls without folder_context share a bucket).
    bucket_key = folder_context or "__no_folder__"
    now = time.time()
    bucket = _LOCK_CLASSIFY_BUCKET.setdefault(bucket_key, [])
    # Drop timestamps older than 60s
    cutoff = now - 60.0
    bucket[:] = [t for t in bucket if t >= cutoff]
    if len(bucket) >= _LOCK_CLASSIFY_RATE_LIMIT:
        retry_after = max(0, int(60 - (now - bucket[0])))
        return {
            "ok": False,
            "error": "rate_limited",
            "retry_after_seconds": retry_after,
            "limit_per_minute": _LOCK_CLASSIFY_RATE_LIMIT,
        }
    bucket.append(now)
    try:
        if not isinstance(text, str):
            return {"ok": False, "error": "text must be a string"}
        if not text.strip():
            return {
                "ok": True,
                "folder_context": "",
                "threshold": 0.0,
                "findings": [],
                "findings_count": 0,
            }

        # Resolve the per-folder threshold (default 0.0)
        threshold = 0.0
        resolved_folder = ""
        if folder_context:
            try:
                from .policy import load_policy
                resolved_folder = str(Path(folder_context).expanduser().resolve())
                pol = load_policy(resolved_folder)
                threshold = float(pol.lock_confidence_threshold or 0.0)
            except Exception:
                # Folder lookup failures don't fail the scan — just no filter
                threshold = 0.0

        from workspaces.lock import tier_b_scan_text
        raw = tier_b_scan_text(text) or []
        findings: list[dict[str, Any]] = []
        for f in raw:
            conf = float(getattr(f, "confidence", 0.0) or 0.0)
            if conf < threshold:
                continue
            findings.append({
                "type":       getattr(f, "type", ""),
                "severity":   getattr(f, "severity", ""),
                "confidence": conf,
                "detail":     getattr(f, "detail", ""),
                "tier":       getattr(f, "tier", "B"),
            })
        return {
            "ok":             True,
            "folder_context": resolved_folder,
            "threshold":      threshold,
            "findings":       findings,
            "findings_count": len(findings),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def lock_threshold_get(folder_context: str) -> dict[str, Any]:
    """Read the per-folder lock confidence threshold.

    The threshold is a floor — ``lock_classify_text`` drops findings whose
    confidence falls below it. Default 0.0 (no filter).

    Returns ``{ok, folder_context, threshold}``.
    """
    try:
        from .policy import load_policy
        resolved = str(Path(folder_context).expanduser().resolve())
        pol = load_policy(resolved)
        return {
            "ok":             True,
            "folder_context": resolved,
            "threshold":      float(pol.lock_confidence_threshold or 0.0),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def lock_threshold_set(folder_context: str,
                          threshold: float) -> dict[str, Any]:
    """Set the per-folder lock confidence threshold (range [0.0, 1.0]).

    Values outside the range are clamped. 0.0 means "emit all findings"
    (no filter); 1.0 means "only emit findings with perfect confidence".

    Returns ``{ok, folder_context, threshold, previous}``.
    """
    try:
        from .policy import load_policy, save_policy
        resolved = str(Path(folder_context).expanduser().resolve())
        pol = load_policy(resolved)
        try:
            new = float(threshold)
        except (TypeError, ValueError):
            return {"ok": False, "error": "threshold must be a number"}
        new = max(0.0, min(1.0, new))
        previous = float(pol.lock_confidence_threshold or 0.0)
        pol.lock_confidence_threshold = new
        save_policy(resolved, pol)
        return {
            "ok":             True,
            "folder_context": resolved,
            "threshold":      new,
            "previous":       previous,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def lock_reclassify_folder(folder_context: str) -> dict[str, Any]:
    """Re-run lock over every pair in this folder.

    Useful when lock rules / disclaimer version change — existing pairs
    were classified under the old rules and their ``clean`` blocks may be
    stale. This re-runs ``lock_classify_pair`` against current rules
    and writes the updated pair back to the mutation log.

    Idempotent: pairs already at the current disclaimer version are
    skipped (no-op write).

    Returns ``{ok, folder_context, pairs_total, pairs_reclassified,
                pairs_already_current, disclaimer_version}``.
    """
    try:
        from .lock_classify import reclassify_all_pairs
        return reclassify_all_pairs(
            folder_context,
            log_root=_log_root(),
            actor=_default_actor(),
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def pin_skill_to_folder(folder_context: str,
                         skill_id: str,
                         pinned_by: str = "",
                         note: str = "") -> dict[str, Any]:
    """Pin a skill (by fully-qualified id, e.g. ``plugin:skill-name``) to
    a folder. Idempotent — re-pinning the same id updates metadata.

    Returns ``{ok, folder_context, skill_id, total_pinned}``.
    """
    try:
        from .pinned_skills import pin_skill
        store = pin_skill(
            folder_context, skill_id,
            pinned_by=pinned_by or _default_actor(),
            note=note,
            log_root=_log_root(),
        )
        return {
            "ok":             True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "skill_id":       skill_id.strip(),
            "total_pinned":   len(store.skills),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def pin_skills_to_folder(folder_context: str,
                          skill_ids: list,
                          pinned_by: str = "",
                          note: str = "") -> dict[str, Any]:
    """Pin many skills to one folder in a single call.

    Plugin install often needs to pin a curated set of skills at once
    (e.g. legal-first-aid-music's 27-skill family). Looping single-pin
    calls is fine but chatty; this primitive collapses N round-trips to
    one. Idempotent at the per-skill level (same as the single-pin path):
    re-pinning an id updates its metadata.

    Best-effort semantics — an invalid id in the list is reported in
    ``per_skill`` with ``ok: false`` but does not abort the batch.

    Args:
        folder_context: target folder.
        skill_ids: list of fully-qualified skill ids.
        pinned_by: actor identifier; defaults to system.
        note: optional note applied to every pin in this call.

    Returns:
        ``{ok, folder_context, pinned_count, total_pinned, per_skill:
            [{skill_id, ok, error?}, ...]}``.
    """
    try:
        if not isinstance(skill_ids, list) or len(skill_ids) == 0:
            return {"ok": False,
                    "error": "skill_ids must be a non-empty list of strings"}

        from .pinned_skills import pin_skill
        actor = pinned_by or _default_actor()
        per_skill: list[dict[str, Any]] = []
        pinned_count = 0
        store = None
        for raw in skill_ids:
            sid = (str(raw) if raw is not None else "").strip()
            if not sid:
                per_skill.append({"skill_id": "", "ok": False,
                                   "error": "empty skill_id"})
                continue
            try:
                store = pin_skill(
                    folder_context, sid,
                    pinned_by=actor, note=note,
                    log_root=_log_root(),
                )
                per_skill.append({"skill_id": sid, "ok": True})
                pinned_count += 1
            except Exception as e:
                per_skill.append({"skill_id": sid, "ok": False,
                                   "error": f"{type(e).__name__}: {e}"})

        return {
            "ok":             True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "pinned_count":   pinned_count,
            "total_pinned":   len(store.skills) if store is not None else 0,
            "per_skill":      per_skill,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def unpin_skill_from_folder(folder_context: str,
                             skill_id: str) -> dict[str, Any]:
    """Unpin a skill from a folder. Returns ``{ok, removed, total_pinned}``.

    ``removed`` is False if the skill wasn't pinned in the first place
    (still ``ok=True``).
    """
    try:
        from .pinned_skills import unpin_skill
        store, removed = unpin_skill(
            folder_context, skill_id, log_root=_log_root(),
        )
        return {
            "ok":             True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "skill_id":       skill_id.strip(),
            "removed":        removed,
            "total_pinned":   len(store.skills),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def list_pinned_skills(folder_context: str) -> dict[str, Any]:
    """List skills pinned to THIS folder only (no ancestor walk).

    For the orchestrator-time view that includes inherited pins from
    ancestors, call ``resolve_skills_for_query`` instead.

    Returns ``{ok, folder_context, skills: [{id, pinned_at, pinned_by, note}, ...]}``.
    """
    try:
        from .pinned_skills import list_pinned
        skills = list_pinned(folder_context, log_root=_log_root())
        return {
            "ok":             True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "skills":         [s.to_dict() for s in skills],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

class SkillBodyUnreadable(RuntimeError):
    """A skill body exists but could not be read.

    Distinct from ``None`` (no Workspace-internal body — the host resolves it):
    here the declarations exist and we failed to read them, so a caller must not
    proceed as though the skill declared nothing.
    """


def _try_read_workspace_skill_body(skill_id: str) -> Optional[str]:
    """If ``skill_id`` matches a Workspace-internal skill, return the SKILL.md
    body (without YAML frontmatter). Otherwise return None — the host is
    responsible for resolving cross-plugin skill bodies.

    Workspace skills live at ``<plugin>/skills/<skill-name>/SKILL.md`` where
    ``<plugin>`` is this MCP server's package install location. We strip the
    optional ``workspace:`` namespace prefix when matching.
    """
    if not skill_id:
        return None
    name = skill_id.strip()
    if name.startswith("workspace:"):
        name = name.split(":", 1)[1]
    # Heuristic: only resolve names we know map to a Workspace skill directory.
    # The package ships ``skills/`` next to its plugin manifest; in dev that
    # lives at ``<repo>/plugin/skills/<name>/SKILL.md``. We probe a few
    # candidate locations and stop on first hit.
    import importlib.util
    candidates = []
    try:
        spec = importlib.util.find_spec("workspaces")
        if spec and spec.origin:
            pkg_dir = Path(spec.origin).resolve().parent
            # repo layout: runtime/src/workspaces → walk up to repo root
            for up in [pkg_dir.parent.parent.parent, pkg_dir.parent.parent,
                       pkg_dir.parent]:
                candidates.append(up / "plugin" / "skills" / name / "SKILL.md")
    except Exception:
        pass
    # Cowork install layout: plugin lives next to the runtime under a
    # well-known cache root. Best-effort.
    for p in candidates:
        if not p.exists():
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except Exception as exc:
            # The body IS there and we could not read it (permissions, or bytes
            # that are not UTF-8). Swallowing that returned None, which the
            # caller cannot tell from "not a Workspace skill" — so a body
            # DECLARING a grade ceiling dispatched UNCAPPED, while a body that
            # merely failed to PARSE clamped to L0. Same ignorance, opposite
            # outcome. Raise so the caller can fail closed.
            raise SkillBodyUnreadable(f"{p}: {exc}") from exc
        # Strip frontmatter
        if src.startswith("---"):
            parts = src.split("---", 2)
            if len(parts) >= 3:
                return parts[2].lstrip("\n")
        return src
    return None

def dispatch_skill(folder_context: str,
                    skill_id: str,
                    query: str = "",
                    chosen_via: str = "user",
                    grade: str = "L1",
                    footprint: Optional[list] = None) -> dict[str, Any]:
    """Dispatch a pinned skill for a folder.

    Validates that ``skill_id`` is in the folder's resolved pinned-skill set
    (self + ancestors). If yes, records a ``skill-dispatch`` event in the
    folder's mutation log and returns dispatch metadata plus, where
    available, the skill's body so the caller can prepend it to the LLM
    prompt. Cross-plugin skill bodies are NOT readable from the Workspace MCP
    sandbox — for those, ``body`` is ``None`` and the caller falls back to
    using the skill_id as a routing label.

    Returns:
        ``{ok, skill_id, dispatched_at, in_resolved_set, provenance,
            body (or None), body_source}``.
    """
    try:
        from .pinned_skills import (
            record_dispatch,
            resolve_skills_for_query as _resolve,
        )
        # Validate skill_id is in the resolved set.
        #
        # Exception: Workspace's OWN skills (skill_id starts with ``workspace:``)
        # always dispatch, with or without a pin. They are the Workspace plugin's
        # functional surface — the user gets them just by installing the
        # plugin, the same way any normal Claude / Cursor skill is available
        # without per-folder pinning. Pinning them is meaningless because
        # they ARE the workspace system. Provenance for such bypass dispatches
        # is recorded as ``"system"``.
        sid = skill_id.strip()
        is_workspace_internal = sid.startswith("workspace:")
        out = _resolve(folder_context, "",
                        log_root=_log_root(),
                        include_ancestors=True)
        match = next((s for s in out["skills"] if s["id"] == sid), None)
        if match is None and not is_workspace_internal:
            return {
                "ok": False,
                "error": f"skill {skill_id!r} not in resolved pinned set",
                "in_resolved_set": False,
                "resolved_skills": [s["id"] for s in out["skills"]],
            }
        if match is not None:
            provenance = "inherited" if match.get("inherited_from") else "own"
            inherited_from = match.get("inherited_from") or ""
        else:
            # workspace-internal skill that's not explicitly pinned
            provenance = "system"
            inherited_from = ""
        # ── the one oversight chokepoint: every dispatch, any MCP client,
        # resolves to permit/hold/deny (gate × matrix × oversight × privacy)
        # and is recorded on the signed chain. Routine low-reach dispatches
        # resolve to permit; DENY blocks; HOLD flags for human authorisation. ──
        try:
            body = _try_read_workspace_skill_body(skill_id)
        except SkillBodyUnreadable:
            # Same fail-closed rule as an unparseable body below: a ceiling we
            # could not read must clamp, never wave through.
            body, _unreadable = None, True
        else:
            _unreadable = False
        # D9: a skill body may DECLARE its own oversight (a grade ceiling / min
        # level). Compose those facets and feed the ceiling into the chokepoint so
        # a high-reach skill can't be dispatched above the autonomy its own
        # declarations cap it to — the composed ceiling is consumed, not ignored.
        skill_ceiling = "L0" if _unreadable else ""
        if body:
            try:
                from .oversight_extractor import extract_oversight
                from .oversight_compose import compose_facets
                skill_ceiling = compose_facets(extract_oversight(body)).grade_ceiling or ""
            except Exception:
                # Fail-closed: a body that declares oversight but can't be parsed
                # must not dispatch UNCAPPED — clamp to L0 rather than ignore a
                # ceiling we failed to read. (A body with no declarations parses
                # cleanly to "" above; this only fires on a genuine parse error.)
                skill_ceiling = "L0"
        from .governance import decide_action
        gov = decide_action(folder_context, action_class=f"dispatch:{skill_id}",
                            grade=grade, footprint=tuple(footprint or ()),
                            actor=_default_actor(), grade_ceiling=skill_ceiling,
                            log_root=_log_root())
        if gov["verdict"] == "deny":
            return {"ok": False, "in_resolved_set": match is not None,
                    "error": f"blocked by policy: {gov['reason']}", "oversight": gov}
        record = record_dispatch(
            folder_context, skill_id,
            query=query, chosen_via=chosen_via,
            actor=_default_actor(),
            log_root=_log_root(),
        )
        return {
            "ok":               True,
            "oversight":        gov,
            "requires_approval": gov["verdict"] == "hold",
            "skill_id":         record["skill_id"],
            "folder_context":   record["folder_context"],
            "dispatched_at":    record["dispatched_at"],
            "in_resolved_set":  match is not None,
            "provenance":       provenance,
            "inherited_from":   inherited_from,
            "body":             body,
            "body_source":      "workspace-internal" if body else "external (host-managed)",
            # Audit-grade chain-of-evidence: the audit_id is the UUID v4
            # written into the folder's mutation log for THIS dispatch.
            # Use ``get_audit_event(event_id, folder_context)`` to retrieve
            # the full event later (Canonical Output Section [6]).
            "audit_id":         record.get("audit_id", ""),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def dispatch_skills_batch(skill_ids: list,
                           folder_context: str,
                           query: str = "",
                           chosen_via: str = "user") -> dict[str, Any]:
    """Dispatch many skills in one call. Best-effort, per-skill audit events
    linked by a shared ``batch_id``.

    The single-skill ``dispatch_skill`` works for one-shot interactions;
    multi-agent orchestrators batching many skills against the same folder
    paid N round-trips through the MCP before this primitive existed. This
    tool resolves the pinned-skill set ONCE, then loops — each constituent
    dispatch still writes its own ``skill-dispatch`` audit event (with the
    batch_id stamped into ``extra.batch_id``) so the chain-of-evidence
    surface stays unchanged. Compliance plugins can include the batch_id
    in their Canonical Output to group related dispatches.

    Semantics:

    - Best-effort: a failure on one skill (not-in-pinned-set, or an
      exception during dispatch) does NOT abort the batch. The
      caller decides what to do based on per-result ``ok`` values.
    - Order preserved: ``results`` is parallel to ``skill_ids``.
    - Duplicates allowed: each occurrence produces a separate audit event.
    - ``workspace:*`` bypass rule from the single-skill path is honored.
    - Shared ``query`` and ``chosen_via`` are used for every step.

    Returns:
        ``{ok, batch_id, folder_context, ok_count, fail_count,
            results: [{skill_id, ok, audit_id?, body?, in_resolved_set,
                       provenance?, error?}, ...]}``.
        ``ok`` at the batch level is True iff the batch ran (even if every
        constituent dispatch failed). Use per-result ``ok`` to detect the
        compound state.
    """
    try:
        if not isinstance(skill_ids, list) or len(skill_ids) == 0:
            return {"ok": False,
                    "error": "skill_ids must be a non-empty list of strings"}

        import uuid as _uuid
        batch_id = "batch:" + _uuid.uuid4().hex[:16]

        from .pinned_skills import (
            record_dispatch,
            resolve_skills_for_query as _resolve,
        )
        # Resolve the pinned set ONCE — this is the per-batch efficiency win.
        out = _resolve(folder_context, "",
                        log_root=_log_root(),
                        include_ancestors=True)
        pinned = {s["id"]: s for s in out.get("skills", [])}

        results: list[dict[str, Any]] = []
        ok_count = 0
        fail_count = 0

        for raw in skill_ids:
            sid = (str(raw) if raw is not None else "").strip()
            row: dict[str, Any] = {"skill_id": sid, "ok": False}

            if not sid:
                row["error"] = "empty skill_id"
                row["in_resolved_set"] = False
                results.append(row)
                fail_count += 1
                continue

            is_workspace_internal = sid.startswith("workspace:")
            match = pinned.get(sid)
            if match is None and not is_workspace_internal:
                row["error"] = f"skill {sid!r} not in resolved pinned set"
                row["in_resolved_set"] = False
                results.append(row)
                fail_count += 1
                continue

            if match is not None:
                provenance = "inherited" if match.get("inherited_from") else "own"
                inherited_from = match.get("inherited_from") or ""
            else:
                provenance = "system"
                inherited_from = ""

            try:
                body = _try_read_workspace_skill_body(sid)
                record = record_dispatch(
                    folder_context, sid,
                    query=query, chosen_via=chosen_via,
                    actor=_default_actor(),
                    log_root=_log_root(),
                    extra={"batch_id": batch_id},
                )
                row.update({
                    "ok":              True,
                    "in_resolved_set": match is not None,
                    "provenance":      provenance,
                    "inherited_from":  inherited_from,
                    "body":            body,
                    "body_source":     "workspace-internal" if body else
                                       "external (host-managed)",
                    "audit_id":        record.get("audit_id", ""),
                    "dispatched_at":   record.get("dispatched_at", ""),
                })
                ok_count += 1
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"
                row["in_resolved_set"] = match is not None
                fail_count += 1

            results.append(row)

        return {
            "ok":             True,
            "batch_id":       batch_id,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "ok_count":       ok_count,
            "fail_count":     fail_count,
            "results":        results,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def ingest_skill(folder_context: str,
                 source: str,
                 source_format: str = "auto",
                 skill_id: str = "",
                 author: str = "",
                 license: str = "proprietary",
                 monetization_model: str = "none",
                 terms_ref: str = "",
                 version: str = "1.0.0") -> dict[str, Any]:
    """Ingest a user-authored skill into this folder — the universal-skill
    adapter's authoring surface.

    Adapts any source format (auto | anthropic-skill | cursor-rule |
    cline-rule | continue-rule | prose) into a SKILL.md, validates it against
    the five install-floor failure modes, content-addresses it, signs it
    (controller key is the root of trust, operator key the fallback), and
    stores a body-bearing object at ``<folder>/.workspace/skills/<uid>/``. The
    skill is then pinned so the resolver can find it, and a ``skill-ingest``
    event is written to the folder's hash-chained mutation log.

    ``author`` is a display label bound to the signing-key fingerprint, not
    a trust source. ``skill_id`` defaults to ``user:<author>/<name-slug>``.

    Returns ``{ok, uid, skill_id, manifest, signature, pinned, audit_id,
    warnings}`` or ``{ok: False, error, failures}`` on validation reject.
    """
    try:
        from . import ingested_skills as _isk
        from .pinned_skills import pin_skill, record_dispatch

        # Privacy Lock ingress gate: a skill body that carries PII /
        # confidential context must not be stored where it could later be
        # served to a cloud LLM. Refuse blocks the ingest; minimise warns.
        gate = _lock_gate_text(folder_context, source or "",
                                 context="skill-ingest")
        if gate.get("action") == "refuse":
            return {"ok": False, "error": "lock refused skill body",
                    "lock": {"action": "refuse",
                               "findings": gate.get("findings", 0),
                               "reason": gate.get("reason", "")}}

        res = _isk.ingest(
            folder_context, source,
            source_format=source_format, skill_id=skill_id,
            author=author, license=license,
            monetization_model=monetization_model, terms_ref=terms_ref,
            version=version,
        )
        if not res.get("ok"):
            return res  # carries `failures`

        sid = res["skill_id"]
        # Register the routing pin (idempotent), authored-by = the manifest author.
        pin_skill(folder_context, sid,
                  pinned_by=res["manifest"]["ownership"].get("author") or _default_actor(),
                  note=f"ingested:{res['uid']}",
                  log_root=_log_root())
        # Audit stamp: a skill-ingest breadcrumb on the same chain dispatch uses.
        rec = record_dispatch(
            folder_context, sid,
            query="", chosen_via="ingest", actor=_default_actor(),
            log_root=_log_root(),
            extra={"kind": "skill-ingest", "uid": res["uid"],
                   "source_format": res["manifest"]["source_format"],
                   "signed_with": res["signature"].get("signed_with", ""),
                   "fingerprint": res["signature"].get("fingerprint", ""),
                   "license": res["manifest"]["ownership"].get("license", ""),
                   "monetization_model":
                       res["manifest"]["ownership"].get("monetization_model", ""),
                   "lock_action": gate.get("action", "allow"),
                   "lock_findings": gate.get("findings", 0)},
        )
        warnings = list(res.get("warnings", []))
        if gate.get("action") == "minimise":
            warnings.append(
                f"lock flagged {gate.get('findings', 0)} finding(s) in the "
                "body; stored as-authored (a skill body is not minimised)")
        return {
            "ok": True,
            "uid": res["uid"],
            "skill_id": sid,
            "manifest": res["manifest"],
            "signature": res["signature"],
            "pinned": True,
            "audit_id": rec.get("audit_id", ""),
            "lock": {"action": gate.get("action", "allow"),
                       "findings": gate.get("findings", 0),
                       "active": gate.get("lock_active", False)},
            "warnings": warnings,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def dispatch_ingested(folder_context: str,
                      skill_id: str,
                      query: str = "",
                      chosen_via: str = "user") -> dict[str, Any]:
    """Dispatch a user-ingested skill, returning its FULL body.

    This is the path that closes the ``body=None`` gap: where
    ``dispatch_skill`` can only return a body for Workspace-internal catalogue
    skills, ``dispatch_ingested`` reads the body from the folder-resident
    object store, so ``body`` is always populated for an ingested skill.

    Validates that ``skill_id`` is in the resolved pinned set (self +
    ancestors), re-verifies the object's signature + body hash before
    serving, emits a ``skill-dispatch-ingested`` audit event (which doubles
    as the monetization usage ledger entry), and returns the body for the
    caller's own LLM to prepend.

    Returns ``{ok, skill_id, uid, body, body_source, in_resolved_set,
    provenance, verified, license, monetization_model, audit_id}``.
    """
    try:
        from . import ingested_skills as _isk
        from .pinned_skills import record_dispatch, resolve_skills_for_query as _resolve

        sid = skill_id.strip()
        out = _resolve(folder_context, "", log_root=_log_root(),
                       include_ancestors=True)
        match = next((s for s in out["skills"] if s["id"] == sid), None)
        if match is None:
            return {"ok": False,
                    "error": f"skill {skill_id!r} not in resolved pinned set",
                    "in_resolved_set": False,
                    "resolved_skills": [s["id"] for s in out["skills"]]}
        provenance = "inherited" if match.get("inherited_from") else "own"

        # Find the body-bearing object. Self first; then walk ancestors so an
        # inherited (e.g. vault-folder) skill resolves to its stored body.
        obj = _isk.find_by_skill_id(folder_context, sid)
        if obj is None and match.get("inherited_from"):
            obj = _isk.find_by_skill_id(match["inherited_from"], sid)
        if obj is None:
            return {"ok": False,
                    "error": f"no ingested object found for {skill_id!r} "
                             "(pinned but not stored as an ingested skill)"}

        uid = obj["uid"]
        owner_folder = match.get("inherited_from") or folder_context
        v = _isk.verify(owner_folder, uid)

        # Privacy Lock egress gate: the body is about to be handed to the
        # CALLER's own LLM. If it carries confidential context, refuse to
        # serve it (the whole point of routing through Workspaces).
        gate = _lock_gate_text(folder_context, obj["body"],
                                 context="skill-dispatch-egress")
        serve_body = obj["body"]
        if gate.get("action") == "refuse":
            serve_body = None
        elif gate.get("action") == "minimise":
            serve_body = gate.get("text") or None

        own = obj["manifest"].get("ownership", {})
        rec = record_dispatch(
            folder_context, sid,
            query=query, chosen_via=chosen_via, actor=_default_actor(),
            log_root=_log_root(),
            extra={"kind": "skill-dispatch-ingested", "uid": uid,
                   "verified": v["ok"],
                   "lock_action": gate.get("action", "allow"),
                   "lock_findings": gate.get("findings", 0),
                   "license": own.get("license", ""),
                   "monetization_model": own.get("monetization_model", "")},
        )
        return {
            "ok": True,
            "skill_id": sid,
            "uid": uid,
            "body": serve_body,
            "body_source": "folder-resident",
            "in_resolved_set": True,
            "provenance": provenance,
            "verified": v["ok"],
            "lock": {"action": gate.get("action", "allow"),
                       "findings": gate.get("findings", 0),
                       "active": gate.get("lock_active", False)},
            "license": own.get("license", ""),
            "monetization_model": own.get("monetization_model", "none"),
            "audit_id": rec.get("audit_id", ""),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def import_plugin(folder_context: str,
                  source: str,
                  on_conflict: str = "upgrade") -> dict[str, Any]:
    """Import every skill in an Anthropic-format plugin folder into this
    folder via the ingest pipeline.

    ``source`` is a path to a plugin directory, a ``.zip`` of a plugin /
    marketplace, OR a project tree carrying rule files. Discovers and ingests
    every supported source under the root: ``SKILL.md`` (anthropic-skill),
    ``*.mdc`` (cursor-rule), ``.clinerules`` (cline-rule), and
    ``.continue/rules/*.md`` (continue-rule). Each is ingested (adapted,
    validated, signed, stored, pinned). Idempotent: a
    skill whose body is unchanged is skipped; a higher ``version`` upgrades;
    a lower / same-version-different-body is refused unless
    ``on_conflict='fork'``.

    Returns ``{ok, imported, upgraded, skipped, failed, source}`` where each
    list carries ``{skill_id, uid}`` / ``{path, reason}`` rows.
    """
    import tempfile
    import zipfile
    tmpdir = None
    try:
        from . import ingested_skills as _isk
        from .pinned_skills import pin_skill, record_dispatch

        raw = Path(source).expanduser().resolve()
        # Zip source → extract to a temp dir and search inside it.
        if raw.is_file() and raw.suffix.lower() == ".zip":
            tmpdir = tempfile.mkdtemp(prefix="workspace_import_")
            with zipfile.ZipFile(raw) as zf:
                zf.extractall(tmpdir)
            search_root = Path(tmpdir)
        else:
            search_root = raw

        # Discover skill sources of any supported format under the root (a
        # marketplace zip nests one or more plugins; a Cursor/Cline project
        # ships rule files instead of SKILL.md). Each entry is (path, format).
        discovered: list[tuple[Path, str]] = []
        for p in sorted(search_root.rglob("SKILL.md")):
            discovered.append((p, "anthropic-skill"))
        for p in sorted(search_root.rglob("*.mdc")):
            discovered.append((p, "cursor-rule"))
        for p in sorted(search_root.rglob(".clinerules")):
            if p.is_file():
                discovered.append((p, "cline-rule"))
        for p in sorted(search_root.rglob(".continue/rules/*.md")):
            discovered.append((p, "continue-rule"))
        if not discovered:
            return {"ok": False,
                    "error": f"no SKILL.md or rule files found under {source!r}"}

        imported: list[dict[str, Any]] = []
        upgraded: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for sf, fmt in discovered:
            try:
                body = sf.read_text(encoding="utf-8")
            except OSError as e:
                failed.append({"path": str(sf), "reason": str(e)})
                continue
            # Honour a declared version in SKILL.md frontmatter so re-import
            # can upgrade rather than collide at 1.0.0. Rule files carry no
            # version → default 1.0.0. Use the filename as a fallback name.
            fm, _ = _isk.parse_frontmatter(body)
            decl_version = (fm.get("version", "") or "1.0.0").strip("'\"") or "1.0.0"
            # Use the filename as a name hint for rule files, but not for
            # dotfiles like ``.clinerules`` — let the adapter read the heading.
            name_hint = ""
            if fmt != "anthropic-skill" and sf.stem and not sf.stem.startswith("."):
                name_hint = sf.stem
            res = _isk.ingest(folder_context, body,
                              source_format=fmt,
                              name=name_hint,
                              version=decl_version,
                              on_conflict=on_conflict)
            if not res.get("ok"):
                failed.append({"path": str(sf),
                               "reason": "; ".join(res.get("failures", [])) or
                                         res.get("error", "")})
                continue
            if any("idempotent" in w for w in res.get("warnings", [])):
                skipped.append({"skill_id": res["skill_id"], "uid": res["uid"]})
                continue
            pin_skill(folder_context, res["skill_id"],
                      pinned_by=res["manifest"]["ownership"].get("author")
                      or _default_actor(),
                      note=f"imported:{res['uid']}", log_root=_log_root())
            record_dispatch(
                folder_context, res["skill_id"],
                query="", chosen_via="import", actor=_default_actor(),
                log_root=_log_root(),
                extra={"kind": "skill-import", "uid": res["uid"],
                       "action": res.get("action", "create"),
                       "source": str(raw)},
            )
            row = {"skill_id": res["skill_id"], "uid": res["uid"]}
            if res.get("action") == "upgrade":
                upgraded.append(row)
            else:
                imported.append(row)

        return {
            "ok": True,
            "imported": imported,
            "upgraded": upgraded,
            "skipped": skipped,
            "failed": failed,
            "source": str(raw),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

def dispatch_skill_dry_run(folder_context: str,
                            skill_id: str,
                            query: str = "",
                            chosen_via: str = "user") -> dict[str, Any]:
    """Resolve a skill exactly like ``dispatch_skill`` — but do NOT write
    an audit event. The mutation log is unchanged.

    Use for testing, for "what would this do" introspection from an
    orchestrator, or for surfacing a skill body without polluting the
    audit trail. Same workspace:* bypass rule, same resolved-set validation.

    Returns the same shape as ``dispatch_skill`` plus ``dry_run: True``,
    with ``audit_id`` set to the empty string (no event was written).
    """
    try:
        from .pinned_skills import resolve_skills_for_query as _resolve
        sid = (skill_id or "").strip()
        if not sid:
            return {"ok": False, "error": "skill_id is required"}
        is_workspace_internal = sid.startswith("workspace:")
        out = _resolve(folder_context, "",
                        log_root=_log_root(),
                        include_ancestors=True)
        match = next((s for s in out["skills"] if s["id"] == sid), None)
        if match is None and not is_workspace_internal:
            return {
                "ok": False,
                "error": f"skill {skill_id!r} not in resolved pinned set",
                "in_resolved_set": False,
                "resolved_skills": [s["id"] for s in out["skills"]],
                "dry_run": True,
            }
        if match is not None:
            provenance = "inherited" if match.get("inherited_from") else "own"
            inherited_from = match.get("inherited_from") or ""
        else:
            provenance = "system"
            inherited_from = ""
        body = _try_read_workspace_skill_body(sid)
        return {
            "ok":               True,
            "skill_id":         sid,
            "folder_context":   str(Path(folder_context).expanduser().resolve()),
            "in_resolved_set":  match is not None,
            "provenance":       provenance,
            "inherited_from":   inherited_from,
            "body":             body,
            "body_source":      "workspace-internal" if body else "external (host-managed)",
            "audit_id":         "",        # no event written
            "dry_run":          True,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def list_plugin_skills(plugin_id: str) -> dict[str, Any]:
    """List every skill the Workspace plugin catalogue knows under
    ``plugin_id`` (e.g. ``ai-governance-watch``, ``workspace``,
    ``legal-first-aid-music``).

    Use case: an orchestrator inside a plugin wants to introspect its
    own skill set without depending on the folder's pinned-skill state.
    Today ``suggest_companion_skills`` exposes the family for any one
    skill; this primitive surfaces the whole family directly.

    Returns ``{ok, plugin_id, family_label, skills: [id, id, ...]}``
    or ``{ok: False, error}`` if the plugin is unknown to the catalogue.
    """
    try:
        from .pinned_skills import suggest_companions
        pid = (plugin_id or "").strip()
        if not pid:
            return {"ok": False, "error": "plugin_id is required"}
        # suggest_companions returns the family of a skill. The catalogue
        # is keyed by plugin id at the top level — we ask for any
        # plausible skill name in that family and read back the whole
        # family. We use "<plugin>:_" as a probe; the resolver matches
        # by plugin-prefix of the catalogue entry, not by exact skill.
        probe = pid + ":_probe"
        out = suggest_companions(probe, exclude=[])
        if not out.get("family"):
            return {"ok": False,
                    "error": f"plugin {pid!r} not found in skill catalogue"}
        # `companions` excludes the probed id (which doesn't exist), so
        # it IS the full family.
        return {
            "ok":           True,
            "plugin_id":    pid,
            "family_label": out.get("family_label", pid),
            "skills":       list(out.get("companions", [])),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def define_workflow(folder_context: str,
                     name: str,
                     steps: list,
                     description: str = "") -> dict[str, Any]:
    """Persist a workflow definition on this folder.

    ``steps`` is a list of step dicts:
        ``{skill_id: "plugin:skill", query: "...", on_failure: "stop"|"continue"}``

    Overwrites any prior workflow with the same name on this folder.
    Returns ``{ok, name, step_count, path}``.
    """
    try:
        from .workflows import Workflow, WorkflowStep, define_workflow as _define
        wf_steps = [WorkflowStep.from_dict(s) for s in (steps or [])]
        wf = Workflow(name=name, description=description, steps=wf_steps,
                       created_by=_default_actor())
        path = _define(folder_context, wf,
                        created_by=_default_actor(),
                        log_root=_log_root())
        return {
            "ok":         True,
            "name":       wf.name,
            "step_count": len(wf.steps),
            "path":       str(path),
            "folder_context": str(Path(folder_context).expanduser().resolve()),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def list_workflows(folder_context: str,
                    include_ancestors: bool = True) -> dict[str, Any]:
    """List effective workflows for the folder (self + ancestors).

    Returns ``{ok, folder_context, workflows, chain}``.
    """
    try:
        from .workflows import list_workflows as _list
        out = _list(folder_context,
                     include_ancestors=bool(include_ancestors),
                     log_root=_log_root())
        out["ok"] = True
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def delete_workflow(folder_context: str, name: str) -> dict[str, Any]:
    """Delete a workflow definition from THIS folder (ancestors untouched).

    Returns ``{ok, removed}``.
    """
    try:
        from .workflows import delete_workflow as _del
        removed = _del(folder_context, name, log_root=_log_root())
        return {
            "ok":      True,
            "name":    name,
            "removed": removed,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def run_workflow(folder_context: str, name: str) -> dict[str, Any]:
    """Run a workflow sequentially. Records workflow-event entries for
    every state change in the folder's mutation log.

    Each step dispatches via ``dispatch_skill`` (which validates the
    skill is in the resolved pinned set, records the dispatch event, and
    returns body if Workspace-internal). on_failure=stop aborts on the first
    failure; on_failure=continue carries on.

    Returns ``{ok, run_id, workflow, steps, final_state}``.
    """
    try:
        from .workflows import run_workflow as _run
        # Inject dispatch_skill (this same module's MCP wrapper) so steps
        # go through the full audit + body-lookup path.
        def _dispatcher(folder_context, skill_id, query):
            return dispatch_skill(
                folder_context=folder_context,
                skill_id=skill_id,
                query=query,
                chosen_via=f"workflow:{name}",
            )
        out = _run(folder_context, name,
                    dispatcher=_dispatcher,
                    actor=_default_actor(),
                    log_root=_log_root())
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def add_known_workspace(folder_context: str,
                         label: str = "") -> dict[str, Any]:
    """Register a folder as a known workspace. Idempotent.

    Returns ``{ok, path, total}``.
    """
    try:
        from .workspace_registry import add_known_workspace as _add
        out = _add(folder_context, label=label, log_root=_log_root())
        out["ok"] = True
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def remove_known_workspace(folder_context: str) -> dict[str, Any]:
    """Unregister a known workspace. Returns ``{ok, removed}``."""
    try:
        from .workspace_registry import remove_known_workspace as _remove
        removed = _remove(folder_context, log_root=_log_root())
        return {"ok": True, "removed": removed, "path": folder_context}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def bootstrap_default_workspace(target: str = "") -> dict[str, Any]:
    """Create ``~/Documents/Workspaces/`` (or ``target``) if missing, register
    it, and mark it as the default. Safe to call repeatedly.

    Returns ``{ok, path, created, was_default}``.
    """
    try:
        from .workspace_registry import bootstrap_default_workspace as _boot
        out = _boot(target=(target.strip() or None), log_root=_log_root())
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def enqueue_workflow_run(folder_context: str,
                          name: str,
                          enqueued_by: str = "") -> dict[str, Any]:
    """Enqueue a workflow for the background worker to drain.

    Per concurrency-v1 rule, rejects if a non-terminal entry already exists
    for ``(folder, workflow_name)`` — surface the existing run_id instead.

    Returns ``{ok, run_id, state, folder_context, workflow_name}``.
    """
    try:
        from .queue import enqueue_run
        entry = enqueue_run(
            folder_context, name,
            enqueued_by=enqueued_by or _default_actor(),
            log_root=_log_root(),
        )
        return {
            "ok":             True,
            "run_id":         entry.run_id,
            "state":          entry.state,
            "folder_context": entry.folder_path,
            "workflow_name":  entry.workflow_name,
            "enqueued_at":    entry.enqueued_at,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def _run_membership_refusal(run_id: str) -> dict[str, Any] | None:
    """Fail-closed membership gate for run_id-addressed lifecycle operations.

    With a request principal present, the run must exist and live in a
    workspace where the principal is a registered active party; an unknown
    run and a foreign workspace's run refuse identically (the response
    reveals neither the run's existence nor its folder). Without a request
    principal (local single-operator mode) returns None — unchanged."""
    from .principal import get_request_principal, principal_member_filter
    member = principal_member_filter()
    if member is None:
        return None
    from .queue import get_run
    entry = get_run(run_id, log_root=_log_root())
    if entry is not None and member(entry.folder_path):
        return None
    ctx = get_request_principal() or {}
    return {"ok": False,
            "error": f"principal {ctx.get('principal')!r} is not a registered"
                     " party in the run's workspace — the operation is refused"
                     " (fail-closed: run-lifecycle acts are scoped to party"
                     " membership)."}

def list_queue(state_filter: str = "",
                folder_context: str = "") -> dict[str, Any]:
    """List queue entries.

    Args:
        state_filter: optional state filter (pending / leased / done / failed / cancelled).
        folder_context: optional folder filter.

    A folderless listing spans workspaces, so under a request principal the
    rows are filtered by party membership server-side before the response
    leaves (an unmatched principal gets an empty list, never everything).

    Returns ``{ok, entries: [{run_id, folder_path, workflow_name, state, ...}, ...]}``.
    """
    try:
        from .principal import principal_member_filter
        from .queue import list_queue as _list
        entries = _list(
            state_filter=(state_filter.strip() or None),
            folder_path=(folder_context.strip() or None),
            log_root=_log_root(),
        )
        member = principal_member_filter()
        if member is not None:
            entries = [e for e in entries if member(e.folder_path)]
        return {
            "ok":      True,
            "entries": [e.to_dict() for e in entries],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def take_next_run(worker_id: str = "",
                   lease_seconds: int = 60) -> dict[str, Any]:
    """Atomically claim the next pending run. Stale leases auto-revoke.

    Intended for the background worker; not normally called from the
    dashboard. Under a request principal the take is scoped to the caller's
    workspaces (party membership) — a caller leases nothing elsewhere, and
    an unmatched principal always gets ``state: "empty"``.

    Returns ``{ok, run_id?, state, folder_path?, workflow_name?}`` or
    ``{ok: True, state: "empty"}`` when nothing is queued.
    """
    try:
        from .principal import principal_member_filter
        from .queue import take_next_run as _take
        wid = (worker_id or "").strip() or _default_actor()
        entry = _take(wid,
                       lease_seconds=int(lease_seconds),
                       folder_allowed=principal_member_filter(),
                       log_root=_log_root())
        if entry is None:
            return {"ok": True, "state": "empty"}
        return {
            "ok":            True,
            "run_id":        entry.run_id,
            "state":         entry.state,
            "folder_path":   entry.folder_path,
            "workflow_name": entry.workflow_name,
            "leased_to":     entry.leased_to,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def renew_lease(run_id: str,
                 additional_seconds: int = 60) -> dict[str, Any]:
    """Extend a worker's lease on a run. Returns ``{ok, renewed: bool}``."""
    refused = _run_membership_refusal(run_id)
    if refused is not None:
        return refused
    try:
        from .queue import renew_lease as _renew
        renewed = _renew(run_id,
                          additional_seconds=int(additional_seconds),
                          log_root=_log_root())
        return {"ok": True, "renewed": renewed, "run_id": run_id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mark_run_done(run_id: str) -> dict[str, Any]:
    """Mark a leased run as done. Returns ``{ok, marked: bool}``."""
    refused = _run_membership_refusal(run_id)
    if refused is not None:
        return refused
    try:
        from .queue import mark_done as _mark
        return {"ok": True, "marked": _mark(run_id, log_root=_log_root()),
                "run_id": run_id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mark_run_failed(run_id: str, error: str = "") -> dict[str, Any]:
    """Mark a leased run as failed. Returns ``{ok, marked: bool}``."""
    refused = _run_membership_refusal(run_id)
    if refused is not None:
        return refused
    try:
        from .queue import mark_failed as _mark
        return {"ok": True,
                "marked": _mark(run_id, error or "",
                                 log_root=_log_root()),
                "run_id": run_id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def inspect_stuck_runs(stale_pending_seconds: int = 300) -> dict[str, Any]:
    """Return queue entries that look stuck (leased-stale or pending-stale).

    Eager read for the dashboard's crash-resume panel. Does NOT mutate
    state. The user decides via ``resume_run`` or ``mark_run_failed``.
    The scan spans workspaces, so under a request principal the rows are
    filtered by party membership before the response leaves.

    Returns ``{ok, stuck: [{kind, entry, lease?, reason}, ...]}``.
    """
    try:
        from .principal import principal_member_filter
        from .queue import inspect_stuck_runs as _inspect
        stuck = _inspect(stale_pending_seconds=int(stale_pending_seconds),
                          log_root=_log_root())
        member = principal_member_filter()
        if member is not None:
            stuck = [r for r in stuck
                     if member((r.get("entry") or {}).get("folder_path", ""))]
        return {"ok": True, "stuck": stuck}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def resume_run(run_id: str) -> dict[str, Any]:
    """Revoke a stale lease and flip a stuck run back to pending so the
    worker can pick it up again. Returns ``{ok, resumed}``."""
    refused = _run_membership_refusal(run_id)
    if refused is not None:
        return refused
    try:
        from .queue import resume_run as _resume
        return {"ok": True,
                "resumed": _resume(run_id, actor=_default_actor(),
                                    log_root=_log_root()),
                "run_id": run_id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def cancel_run(run_id: str) -> dict[str, Any]:
    """Cancel a queued or in-flight run. Returns ``{ok, cancelled: bool}``."""
    refused = _run_membership_refusal(run_id)
    if refused is not None:
        return refused
    try:
        from .queue import cancel_run as _cancel
        return {"ok": True,
                "cancelled": _cancel(run_id, actor=_default_actor(),
                                      log_root=_log_root()),
                "run_id": run_id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def get_audit_event(event_id: str,
                     folder_context: str = "") -> dict[str, Any]:
    """Retrieve one mutation-log event by its ``audit_id`` (UUID v4).

    The audit-grade chain-of-evidence retrieval primitive for compliance
    plugins: every ``dispatch_skill`` / ``workflow-event`` row writes an
    ``audit_id`` that callers can use to render their Canonical Output
    Section [6] Audit Trail. This tool resolves that id back to the full
    event record so the deliverable can include verifiable IDs instead
    of "it's in the mutation log somewhere."

    Args:
        event_id: the UUID written into the event's ``audit_id`` field.
        folder_context: workspace path that owns the event's mutation
            log. If empty, scans every known workspace (slower but
            useful when the caller has only the id).

    Returns:
        On success: ``{ok: True, event: {...}, folder_context, found_in}``
        where ``event`` carries the full LogEvent record (event kind,
        actor, timestamp, audit_id, extras) and ``found_in`` is the
        folder where the event was located.

        On not-found or error: ``{ok: False, error}``.
    """
    try:
        from .mutation_log import MutationLog
        event_id = (event_id or "").strip()
        if not event_id:
            return {"ok": False, "error": "event_id is required"}

        def _scan(folder_path: str) -> Optional[dict[str, Any]]:
            log = MutationLog(folder_path, log_root=_log_root())
            for e in log.replay():
                if e.audit_id == event_id:
                    return _serialize_log_event(e)
            return None

        # Direct lookup if folder_context given
        if folder_context:
            resolved = str(Path(folder_context).expanduser().resolve())
            evt = _scan(resolved)
            if evt is not None:
                return {"ok": True, "event": evt,
                        "folder_context": resolved,
                        "found_in": resolved}
            return {"ok": False,
                    "error": f"audit_id {event_id!r} not found in folder {resolved!r}",
                    "folder_context": resolved}

        # Discovery scan: walk every known workspace
        from .workspace_registry import list_known_workspaces
        for ws in (list_known_workspaces(log_root=_log_root()) or []):
            wp = ws.get("path") if isinstance(ws, dict) else None
            if not wp:
                continue
            try:
                evt = _scan(wp)
            except Exception:
                continue
            if evt is not None:
                return {"ok": True, "event": evt,
                        "folder_context": "",
                        "found_in": wp}
        return {"ok": False,
                "error": f"audit_id {event_id!r} not found in any known workspace"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def _serialize_log_event(e: Any) -> dict[str, Any]:
    """Render a LogEvent into a transport-friendly dict for get_audit_event."""
    from .workflows import _event_ts_iso
    return {
        "audit_id":        e.audit_id or "",
        "event":           e.event or "",
        "channel":         e.channel or "",
        "pair_id":         e.pair_id or "",
        "lifecycle_state": e.lifecycle_state or "",
        "problem_id":      e.problem_id or "",
        "source_hash":     e.source_hash or "",
        "actor":           e.actor or "",
        "folder_path":     e.folder_path or "",
        "timestamp":       _event_ts_iso(e),
        "ts":              float(getattr(e, "ts", 0) or 0),
        "extra":           dict(e.extra or {}),
    }

def set_pair_layout(pair_id: str,
                     folder_context: str,
                     x: float,
                     y: float) -> dict[str, Any]:
    """Persist the (x, y) position of a pair in the force-directed graph.

    Writes a ``system`` LogEvent carrying ``extra.layout = {x, y, at}``.
    Latest event wins on replay (see ``get_pair_layouts``).

    Args:
        pair_id: the pair whose layout coordinates we're recording.
        folder_context: workspace path that owns this layout (the graph
            stage is scoped to a single folder; cross-folder layouts are
            out of scope).
        x: stage-space x coordinate (float).
        y: stage-space y coordinate (float).

    Returns:
        ``{ok, audit_id, x, y, folder_context, pair_id}`` on success;
        ``{ok: False, error}`` on failure.
    """
    try:
        from datetime import datetime, timezone
        from .mutation_log import LogEvent, MutationLog
        pair_id = (pair_id or "").strip()
        if not pair_id:
            return {"ok": False, "error": "pair_id is required"}
        if not folder_context:
            return {"ok": False, "error": "folder_context is required"}
        resolved = str(Path(folder_context).expanduser().resolve())
        try:
            fx = float(x); fy = float(y)
        except (TypeError, ValueError):
            return {"ok": False, "error": "x and y must be numeric"}
        log = MutationLog(resolved, log_root=_log_root())
        at_iso = datetime.now(timezone.utc).isoformat()
        evt = LogEvent(
            event="system",
            folder_path=resolved,
            pair_id=pair_id,
            actor=_default_actor(),
            extra={"layout": {"x": fx, "y": fy, "at": at_iso}},
        )
        aid = log.append(evt)
        return {
            "ok":             True,
            "audit_id":       aid,
            "x":              fx,
            "y":              fy,
            "folder_context": resolved,
            "pair_id":        pair_id,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def _replay_pair_layouts(folder_path: str) -> dict[str, dict[str, Any]]:
    """Walk the folder's mutation log and return latest-wins layout coords.

    Returns ``{pair_id: {x, y, at, audit_id}}``. Only events with a
    ``extra.layout`` dict carrying numeric ``x`` and ``y`` are considered.
    """
    from .mutation_log import MutationLog
    out: dict[str, dict[str, Any]] = {}
    log = MutationLog(folder_path, log_root=_log_root())
    for e in log.replay():
        layout = (e.extra or {}).get("layout") if e.extra else None
        if not isinstance(layout, dict):
            continue
        try:
            lx = float(layout.get("x"))
            ly = float(layout.get("y"))
        except (TypeError, ValueError):
            continue
        # Latest-wins by ts (events are appended in order so iteration
        # order is already chronological; explicit ts check guards
        # against out-of-order recovery).
        prior = out.get(e.pair_id)
        if prior is None or float(getattr(e, "ts", 0) or 0) >= prior.get("_ts", 0):
            out[e.pair_id] = {
                "x":        lx,
                "y":        ly,
                "at":       layout.get("at") or "",
                "audit_id": e.audit_id or "",
                "_ts":      float(getattr(e, "ts", 0) or 0),
            }
    # Strip private ``_ts`` from the public payload
    for v in out.values():
        v.pop("_ts", None)
    return out

def get_pair_layouts(folder_context: str) -> dict[str, Any]:
    """Return the latest persisted (x, y) layout per pair for this folder.

    The Workspace dashboard hydrates the force-directed graph from this
    so user-curated positions survive a session restart.

    Args:
        folder_context: workspace path.

    Returns:
        ``{ok, folder_context, count, layouts: {pair_id: {x, y, at, audit_id}}}``
        on success; ``{ok: False, error}`` on failure.
    """
    try:
        if not folder_context:
            return {"ok": False, "error": "folder_context is required"}
        resolved = str(Path(folder_context).expanduser().resolve())
        layouts = _replay_pair_layouts(resolved)
        return {
            "ok":             True,
            "folder_context": resolved,
            "count":          len(layouts),
            "layouts":        layouts,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def active_workflows(folder_context: str) -> dict[str, Any]:
    """Return workflow runs that started but never reached a terminal
    state. Useful for surfacing in-flight or crashed workflows.

    Returns ``{ok, folder_context, active}``.
    """
    try:
        from .workflows import active_workflows as _active
        active = _active(folder_context, log_root=_log_root())
        return {
            "ok":             True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "active":         active,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def suggest_companion_skills(skill_id: str,
                              folder_context: str = "") -> dict[str, Any]:
    """Suggest companion skills for ``skill_id`` based on the Workspace plugin's
    skill-family catalogue. If ``folder_context`` is provided, already-pinned
    companions on this folder (or any ancestor) are filtered out of the
    returned list so the UI only surfaces *new* suggestions.

    Returns:
        ``{ok, skill_id, family, family_label, companions: [id, ...],
            already_pinned: [id, ...]}``.
    """
    try:
        from .pinned_skills import (
            suggest_companions,
            resolve_skills_for_query as _resolve,
        )
        already_pinned: list[str] = []
        if folder_context:
            out = _resolve(folder_context, "",
                            log_root=_log_root(),
                            include_ancestors=True)
            already_pinned = [s["id"] for s in out["skills"]]
        result = suggest_companions(skill_id, exclude=already_pinned)
        result["ok"] = True
        result["already_pinned"] = already_pinned
        return result
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def resolve_skills_for_query(folder_context: str,
                              query: str = "",
                              include_ancestors: bool = True) -> dict[str, Any]:
    """Resolve the effective pinned-skill set for ``folder_context``
    walking the asymmetric hierarchy UPWARD (self + ancestors).

    Use at orchestration time. If ``query`` is non-empty, results are
    filtered by case-insensitive substring match on skill id.

    Returns ``{ok, folder_context, query, skills, chain}``.
    """
    try:
        from .pinned_skills import resolve_skills_for_query as _resolve
        out = _resolve(
            folder_context, query,
            log_root=_log_root(),
            include_ancestors=bool(include_ancestors),
        )
        out["ok"] = True
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def local_llm_complete(
    prompt: str,
    folder_context: str,
    model: str = "",
    temperature: float = 0.0,
    max_tokens: int = 512,
    capture: bool = True,
) -> dict[str, Any]:
    """Route a completion request to the configured local-LLM endpoint.

    Returns ``{ok, response, model_used, latency_ms, endpoint_host, captured}``
    on success, ``{ok: false, error, endpoint_host?}`` on failure.

    If ``capture=True`` (default), the exchange is recorded in the
    folder's mutation log with ``model_provider="local"`` — the same
    audit floor as cloud calls. Local routing keeps data off the wire;
    capture proves it.
    """
    from .local_llm import complete
    result = complete(prompt, model=model or None,
                       temperature=temperature, max_tokens=max_tokens)
    if result.get("ok") and capture and folder_context:
        # Record the exchange to the folder's mutation log.
        try:
            # NB: capture_llm's params are prompt_context / cited_sources, and its
            # result dict reports success under "captured" (not "ok"). The prior
            # call used prompt=/citations=/.get("ok") — every call raised TypeError
            # and was swallowed, so local completions were NEVER audited (N2). The
            # outgoing text is redacted downstream in the capture pipeline (D1).
            captured = capture_llm(
                folder_context=folder_context,
                prompt_context=prompt,
                response=result["response"],
                model=f"local:{result.get('model_used', 'unknown')}",
                cited_sources=[result.get("endpoint_host", "local")],
            )
            result["captured"] = bool(captured.get("captured"))
            # Surface the pair id (correlation) + why a capture was skipped, so a
            # captured:false isn't an opaque dead end for the caller.
            if captured.get("pair_id"):
                result["pair_id"] = captured["pair_id"]
            if captured.get("skipped_reason"):
                result["capture_skipped_reason"] = captured["skipped_reason"]
        except Exception as e:
            result["captured"] = False
            result["capture_error"] = f"{type(e).__name__}: {e}"
    else:
        result["captured"] = False
    return result

def local_llm_classify(
    text: str,
    categories: list[str],
    folder_context: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Specialised classification helper: route a categorisation request.

    Picks one of ``categories`` for ``text`` using the local LLM. Returns
    ``{ok, category, raw_response, model_used, latency_ms, endpoint_host}``.

    Used downstream by tier_c semantic check once the local model is wired.
    Doesn't audit by default — classification is short and high-frequency;
    callers who want audit should use ``local_llm_complete`` with capture=True.
    """
    from .local_llm import classify
    return classify(text, categories, model=model or None)

def local_llm_list_available(folder_context: str = "") -> dict[str, Any]:
    """Probe the configured local-LLM endpoint for available models.

    Returns ``{ok, endpoint, models: [...], reachable}``. Useful for
    discovery in clients that want to populate a model picker.
    """
    from .local_llm import list_available
    return list_available()

def model_runtime_status(probe_endpoint: bool = True) -> dict[str, Any]:
    """Read-only: the operator's answer to "which model work runs locally right
    now, and what degrades" — per-task readiness with each task's degrade
    action, the Tier C scan backend and its availability, and (optionally)
    endpoint reachability. Declares recorded/probed state; runs no model."""
    from .lock import describe_tier_c, is_tier_c_available, tier_c_requires_real_backend
    from .model_capability import readiness
    out: dict[str, Any] = {
        "ok": True,
        "readiness": readiness(),
        "tier_c": {"backend": describe_tier_c(),
                   "available": bool(is_tier_c_available()),
                   # fail-closed mode: a real backend is configured, so a broken
                   # backend refuses egress rather than waving it through
                   "fail_closed": bool(tier_c_requires_real_backend())},
    }
    if probe_endpoint:
        try:
            out["endpoint"] = local_llm_list_available()
        except Exception as e:
            out["endpoint"] = {"ok": False, "reachable": False, "error": str(e)}
    return out

# Task scopes that mean "this egress feeds AI training / corpus building".
# Consulted against the folder's TDM opt-out (Art. 4 DSM shape) when the
# caller provides a folder_context.
TDM_TRAINING_SCOPES = frozenset({
    "training", "fine-tuning", "finetuning", "dataset", "dataset-export",
    "bulk-corpus", "corpus", "embedding-corpus",
})


def lock_egress_check(
    tool: str,
    arguments: dict,
    task_scope: list[str],
    mode: str | None = None,
    capability_token: dict | None = None,
    folder_context: str | None = None,
) -> dict:
    """Pre-call middleware. Check whether a tool invocation is safe to send.

    Merged from workspace-lock-mcp in 0.6.6. Returns:
    ``{action: "allow"|"strip"|"refuse", findings: [...], modified_call?, stripped_fields?, reason}``.

    0.6.7: delegates to the reference implementation in ``workspaces.lock.mcp_server``
    instead of re-assembling the egress() call here. The previous copy drifted
    (passed ``capability_token`` to ``egress()``, which takes it on the
    ``ToolCall``) and crashed on every invocation through this path.
    Delegation makes signature drift structurally impossible.

    TDM opt-out (2026-06-11, design § 3): when ``folder_context`` is given
    and that folder's policy asserts ``ai_training_optout``, any egress
    whose ``task_scope`` names an AI-training/corpus use is refused before
    the PII delegation runs. No folder_context → no TDM check (the PII
    gate below is folder-agnostic and unchanged).
    """
    if folder_context:
        scopes = {str(s).strip().lower() for s in (task_scope or [])}
        tdm_hit = scopes & TDM_TRAINING_SCOPES
        if tdm_hit:
            from .policy import resolve_ai_training_optout
            if resolve_ai_training_optout(folder_context):
                return {
                    "action": "refuse",
                    "findings": [],
                    "reason": (
                        "TDM opt-out: this folder's policy asserts "
                        "ai_training_optout; egress with task_scope "
                        f"{sorted(tdm_hit)} is refused (Art. 4 DSM "
                        "reservation). Withdraw via policy "
                        "set_ai_training_optout(folder, False)."
                    ),
                }
    from workspaces.lock.mcp_server import egress_check as _lb_egress_check
    return _lb_egress_check(
        tool=tool,
        arguments=arguments,
        task_scope=task_scope,
        mode=mode,
        capability_token=capability_token,
    )

def lock_ingress_check(
    payload: dict,
    task_scope: list[str],
    mode: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Post-call middleware. Check a tool response before it enters the agent's context.

    Merged from workspace-lock-mcp in 0.6.6. Returns:
    ``{action: "allow"|"redact", findings: [...], redacted_payload?, reason}``.

    0.6.7: delegates to the reference implementation in ``workspaces.lock.mcp_server``
    (same rationale as ``lock_egress_check`` — the local copy serialised
    decisions with ``__dict__``, leaving dataclass findings non-JSON-safe).

    Type coercion (gateway path, G4): workflow engines routinely hand the
    gate a plain string (webhook body, ticket text, email). The reference
    impl iterates ``payload.keys()``; a bare string crashed the flagship
    ingress gate. Coerce ``str`` → ``{"text": payload}`` here — pure
    normalisation, no logic fork from the reference implementation.
    """
    from workspaces.lock.mcp_server import ingress_check as _lb_ingress_check
    if isinstance(payload, str):
        payload = {"text": payload}
        task_scope = sorted({*task_scope, "text"})
    return _lb_ingress_check(
        payload=payload,
        task_scope=task_scope,
        mode=mode,
        task_id=task_id,
    )

def lock_audit_query(
    reason_for_query: str,
    limit: int = 50,
) -> dict:
    """Read the most recent entries from the lock audit log.

    Merged from workspace-lock-mcp in 0.6.6. Reads from path set by env var
    ``AGENT_TOOL_LOCK_AUDIT_LOG``. Returns:
    ``{entries: [...], total_lines_in_log, audit_log_path}``.

    0.6.7+: ``reason_for_query`` is required (non-empty). Each access is
    self-logged so passive enumeration of the lock's decision history
    becomes visible in the audit chain it queries. Use cases: "monthly
    compliance review", "incident response for ticket XYZ", "DPIA refresh
    for Acme deployment". Empty or generic-only reason is refused.
    """
    if not reason_for_query or not reason_for_query.strip():
        return {
            "ok": False,
            "error": "reason_for_query is required (non-empty). Audit-log "
                     "access is itself audited; provide a reason that "
                     "would survive a regulator's read.",
        }
    # Self-log this access (best-effort; never blocks the read).
    try:
        from .mutation_log import LogEvent, MutationLog
        cf = os.environ.get("WORKSPACE_FOLDER_CONTEXT")
        if cf:
            log = MutationLog(cf)
            log.append(LogEvent(
                event="system", folder_path=cf,
                pair_id=f"lock-audit-access:{int(time.time())}",
                actor=_default_actor(),
                extra={"action": "lock_audit_query",
                       "reason": reason_for_query, "limit": limit},
            ))
    except Exception:
        pass  # logging failure must never block legitimate audit access
    path = os.environ.get("AGENT_TOOL_LOCK_AUDIT_LOG")
    if not path:
        return {
            "entries": [],
            "total_lines_in_log": 0,
            "audit_log_path": None,
            "note": "AGENT_TOOL_LOCK_AUDIT_LOG env var not set; no audit log to read.",
        }
    limit = max(1, min(limit, 500))
    log_path = Path(path)
    if not log_path.exists():
        return {
            "entries": [],
            "total_lines_in_log": 0,
            "audit_log_path": str(log_path),
            "note": "Audit log path configured but file does not exist yet.",
        }
    lines = log_path.read_text().splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {
        "entries": entries,
        "total_lines_in_log": len(lines),
        "audit_log_path": str(log_path),
    }

def pairs_recent(folder_context: str, limit: int = 20) -> dict[str, Any]:
    """Recent pairs in the folder.

    Preferred name (0.6.6+). Alias of ``recent``, which remains for back-compat.
    """
    return recent(folder_context=folder_context, limit=limit)

def folder_list(path: str) -> dict[str, Any]:
    """List contents of a folder.

    Preferred name (0.6.6+). Alias of ``list_folder``, which remains for back-compat.
    """
    return list_folder(path=path)

def folder_create(path: str) -> dict[str, Any]:
    """Create a folder.

    Preferred name (0.6.6+). Alias of ``create_folder``, which remains for back-compat.
    """
    return create_folder(path=path)

def folder_scan(folder_context: str) -> dict[str, Any]:
    """Scan an Inbox subfolder once.

    Preferred name (0.6.6+). Alias of ``scan_folder``, which remains for back-compat.
    """
    return scan_folder(folder_context=folder_context)

def folder_reextract(folder_context: str) -> dict[str, Any]:
    """Re-run extractor pipeline on existing pairs in folder.

    Preferred name (0.6.6+). Alias of ``reextract_folder``, which remains for back-compat.
    """
    return reextract_folder(folder_context=folder_context)

def folder_ingest(path: str,
                   folder_context: str = "",
                   actor: str = "") -> dict[str, Any]:
    """Ingest a file into the workspace.

    Forwards to ``ingest_path(folder_context, file_path)``. ``actor`` is accepted
    for call-site compatibility; ``ingest_path`` performs its own audit.

    Previously forwarded ``path=/actor=`` — neither is a parameter of
    ``ingest_path`` (it takes ``file_path``) — so every call raised TypeError (N3).
    """
    return ingest_path(folder_context=folder_context, file_path=path)

def pair_spans(folder_context: str,
                pair_id: str,
                span_count: int = 5) -> dict[str, Any]:
    """Return lock-processed spans for a single pair.

    Forwards to ``fetch_pair_spans(folder_context, pair_ids=[pair_id])``.
    ``span_count`` is accepted for call-site compatibility, but fetch_pair_spans
    returns all lock-processed spans for the requested pair(s) and has no
    per-pair count limit, so it is not forwarded.

    Previously forwarded ``pair_id=`` (the target takes a ``pair_ids`` LIST) and
    ``span_count=`` (not a parameter) — so every call raised TypeError (N3).
    """
    return fetch_pair_spans(folder_context=folder_context,
                             pair_ids=[pair_id])

def mirror_generate(folder_context: str, source_path: str,
                     actor: str = "") -> dict[str, Any]:
    """Run Lock over ``source_path`` and write a cleaned mirror.

    Creates ``<folder>/mirrors/lock/<basename>.cleaned.md`` plus a
    spans sidecar. Emits a ``system`` audit event with
    ``extra.kind="mirror_lock"``.

    Args:
        folder_context: workspace path.
        source_path: absolute path to the source file inside the folder.
        actor: actor identifier (defaults to ``system:mirror``).

    Returns ``{ok, mirror_path, spans_path, span_count, audit_id}``.
    """
    try:
        from .mirrors import generate_lock_mirror
        rec = generate_lock_mirror(
            folder_context, source_path,
            log_root=_log_root(),
            actor=actor or "system:mirror",
        )
        return {
            "ok":          True,
            "mirror_path": rec.mirror_path,
            "spans_path":  rec.spans_path,
            "span_count":  rec.span_count,
            "audit_id":    rec.audit_id,
            "source_path": rec.source_path,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mirror_approve(folder_context: str, mirror_path: str,
                    approver: str) -> dict[str, Any]:
    """Promote a Lock mirror into the Oversight surface.

    Copies the cleaned file to ``<folder>/mirrors/oversight/<basename>
    .approved.md``, copies the spans sidecar, and emits a ``system`` audit
    event with ``extra.kind="mirror_oversight"`` and the approver identity.

    Returns ``{ok, mirror_path, spans_path, audit_id}``.
    """
    try:
        from .mirrors import approve_lock_mirror
        rec = approve_lock_mirror(
            folder_context, mirror_path, approver,
            log_root=_log_root(),
        )
        return {
            "ok":          True,
            "mirror_path": rec.mirror_path,
            "spans_path":  rec.spans_path,
            "span_count":  rec.span_count,
            "audit_id":    rec.audit_id,
            "approver":    approver,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mirror_edit(folder_context: str, mirror_path: str,
                 span_id: str, operation: str,
                 actor: str = "system:editor", reason: str = "",
                 **kwargs: Any) -> dict[str, Any]:
    """Apply one per-span edit operation to a draft (B9).

    Operation is one of: redact / un_redact / change_replacement / split /
    merge / add_note / new_redact. The ``**kwargs`` carry op-specific
    arguments (``replacement``, ``new_replacement``, ``at_offset``,
    ``other_span_id``, ``note``, ``start``, ``end``, ``kind``,
    ``controller_key``, ``recheck``).

    Returns ``{ok, draft_path, spans_path, revision, audit_id}``.
    """
    try:
        from . import mirror_editor
        rd = mirror_editor.edit_span(
            folder_context, mirror_path, span_id, operation,
            actor=actor, reason=reason, log_root=_log_root(),
            **kwargs,
        )
        return {
            "ok":         True,
            "draft_path": rd.draft_path,
            "spans_path": rd.spans_path,
            "revision":   rd.revision,
            "audit_id":   rd.audit_id,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mirror_un_redact(folder_context: str, mirror_path: str,
                       span_id: str, controller_key: str,
                       actor: str = "system:editor", reason: str = "",
                       original_text: str = "", recheck: bool = True,
                       ) -> dict[str, Any]:
    """Privileged restore of a redacted region (B9.1).

    Requires a controller-key fingerprint or signed token. When
    ``recheck=True`` (default) re-runs Lock Tier B+ over the restored
    region and records the resulting ``lock_recheck_id``. When
    ``recheck=False`` emits ``mirror_edit_lock_skipped`` so the bypass
    is visible on-chain.
    """
    try:
        from . import mirror_editor
        rd = mirror_editor.un_redact(
            folder_context, mirror_path, span_id,
            actor=actor, reason=reason, controller_key=controller_key,
            original_text=original_text, recheck=recheck,
            log_root=_log_root(),
        )
        return {
            "ok":         True,
            "draft_path": rd.draft_path,
            "spans_path": rd.spans_path,
            "revision":   rd.revision,
            "audit_id":   rd.audit_id,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mirror_history(folder_context: str, mirror_path: str) -> dict[str, Any]:
    """Chronological revisions of a draft (B9.1)."""
    try:
        from . import mirror_editor
        revs = mirror_editor.revisions_list(
            folder_context, mirror_path, log_root=_log_root(),
        )
        return {"ok": True, "count": len(revs),
                "revisions": [r.to_dict() for r in revs]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mirror_diff(folder_context: str, mirror_path: str,
                  from_rev: int, to_rev: int | None = None) -> dict[str, Any]:
    """Unified diff between two revisions of a draft (B9.1)."""
    try:
        from . import mirror_editor
        diff = mirror_editor.revisions_diff(
            folder_context, mirror_path, int(from_rev),
            None if to_rev is None else int(to_rev),
            log_root=_log_root(),
        )
        return {"ok": True, "diff": diff}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mirror_discard(folder_context: str, mirror_path: str,
                     actor: str = "system:editor", reason: str = ""
                     ) -> dict[str, Any]:
    """Discard the current draft and release the lock (B9.1)."""
    try:
        from . import mirror_editor
        audit_id = mirror_editor.discard_revision(
            folder_context, mirror_path,
            actor=actor, reason=reason, log_root=_log_root(),
        )
        return {"ok": True, "audit_id": audit_id}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mirror_lock_acquire(folder_context: str, mirror_path: str,
                          actor: str,
                          ttl_seconds: int = 900) -> dict[str, Any]:
    """Acquire the per-draft editing lock (B9.2)."""
    try:
        from . import mirror_editor
        # mirror_path here may be the lock mirror; the editor resolves
        # the draft path internally.
        from pathlib import Path as _P
        folder_p = _P(folder_context).expanduser().resolve()
        stem = mirror_editor._stem_for(mirror_path)
        draft_p = mirror_editor._draft_path_for(folder_p, stem)
        lock = mirror_editor.acquire_lock(
            folder_p, draft_p, actor=actor, ttl_seconds=int(ttl_seconds),
        )
        return {"ok": True, "lock": lock.to_dict()}
    except mirror_editor.LockHeldError as e:
        return {"ok": False, "error": f"LockHeldError: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mirror_lock_release(folder_context: str, mirror_path: str,
                          actor: str = "") -> dict[str, Any]:
    """Release the per-draft editing lock (B9.2)."""
    try:
        from . import mirror_editor
        from pathlib import Path as _P
        folder_p = _P(folder_context).expanduser().resolve()
        stem = mirror_editor._stem_for(mirror_path)
        draft_p = mirror_editor._draft_path_for(folder_p, stem)
        mirror_editor.release_lock(folder_p, draft_p, actor=actor)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def mirror_list(folder_context: str, kind: str = "") -> dict[str, Any]:
    """List every mirror present under ``<folder>/mirrors/``.

    Args:
        folder_context: workspace path.
        kind: optional filter — ``"lock"`` or ``"oversight"``.

    Returns ``{ok, count, mirrors: [{kind, mirror_path, spans_path,
    source_path, span_count, created_at}]}``.
    """
    try:
        from .mirrors import list_mirrors
        records = list_mirrors(folder_context, kind=kind or "")
        return {
            "ok":      True,
            "count":   len(records),
            "mirrors": [r.to_dict() for r in records],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def erase_sweep(folder_context: str,
                subject: str,
                cascade: bool = False) -> dict[str, Any]:
    """Preview what an erasure would touch. No writes — dry-run only.

    Walks the folder (+ descendants if ``cascade=True``) for events that
    reference ``subject`` in any pair body, capture_llm prompt/response,
    or capture_web payload, and for draft and saved card files carrying
    the subject. Returns the shape of the would-be composite tombstone so
    the controller can confirm scope before authorising.

    Args:
        folder_context: workspace path the sweep operates against.
        subject:        the subject text to look for.
        cascade:        when True, also sweep descendant folders.

    Returns:
        ``{ok, sweep: {subject, folder_context, cascade, hits_by_kind,
        hits_by_folder, estimated_tombstone, total_hits}}``.
    """
    try:
        from . import erasure
        report = erasure.sweep(folder_context, subject,
                                cascade=bool(cascade),
                                log_root=_log_root())
        return {"ok": True, "sweep": report.to_dict()}
    except ValueError as e:
        return {"ok": False, "error": f"ValueError: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def erase_subject(folder_context: str,
                   subject: str,
                   legal_basis: str,
                   requester_ref: str,
                   reason: str,
                   cascade: bool = False,
                   dry_run: bool = False,
                   actor: str = "") -> dict[str, Any]:
    """Run the erasure workflow: sweep → per-pair purge → composite
    tombstone → forgotten_subjects ledger.

    Controller-facing. Requires:

    - ``legal_basis`` — GDPR Art. 17(1) ground (art_17_1_a..art_17_1_f).
    - ``requester_ref`` — opaque reference to the requesting subject.
    - ``reason`` — free-text reason, recorded on the composite tombstone.

    Status: beta, experimental, no legal advice, ongoing.

    Args:
        folder_context: workspace path.
        subject:        subject text to erase.
        legal_basis:    Art. 17(1) ground.
        requester_ref:  intake reference.
        reason:         free-text reason.
        cascade:        when True, also purge descendants.
        dry_run:        when True, returns the sweep without writing.
        actor:          actor identifier (defaults to env default).

    Returns:
        ``{ok, report: {...full ExecutionReport...}}`` or
        ``{ok: false, error: "..."}`` on validation failure.
    """
    try:
        from . import erasure
        report = erasure.execute(
            folder_context, subject,
            legal_basis=legal_basis,
            requester_ref=requester_ref,
            reason=reason,
            cascade=bool(cascade),
            dry_run=bool(dry_run),
            log_root=_log_root(),
            actor=actor or _default_actor(),
        )
        return {"ok": True, "report": report.to_dict()}
    except ValueError as e:
        return {"ok": False, "error": f"ValueError: {e}"}
    except RuntimeError as e:
        return {"ok": False, "error": f"RuntimeError: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def erase_request(folder_context: str,
                   subject: str,
                   requester_ref: str,
                   reason: str,
                   actor: str = "") -> dict[str, Any]:
    """Two-phase intake (D4): write an ERASURE_REQUESTED audit event.

    Does NOT sweep or purge. Use this when an intake form fires and a
    human review must come before the sweep. The returned ``request_id``
    can be passed to ``erase_subject(..., request_id=...)`` later — and
    to ``erase_status`` at any time to read back the cascade manifest.

    Returns ``{ok, request_id, audit_id, folder}``.
    """
    try:
        from . import erasure
        res = erasure.request(
            folder_context, subject,
            requester_ref=requester_ref, reason=reason,
            log_root=_log_root(),
            actor=actor or _default_actor(),
        )
        res["ok"] = True
        return res
    except ValueError as e:
        return {"ok": False, "error": f"ValueError: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def erase_status(folder_context: str, request_id: str) -> dict[str, Any]:
    """Return the cascade manifest for a previously-issued erase request.

    Walks the folder's mutation log for the matching ERASURE_REQUESTED
    event, every per-pair purge that referenced this request_id, the
    composite tombstone, and the forgotten_subjects breadcrumb.

    Returns ``{ok, manifest: {request_id, folder, requested, executed,
    purges, forgotten}}``.
    """
    try:
        from . import erasure
        manifest = erasure.status(folder_context, request_id,
                                   log_root=_log_root())
        return {"ok": True, "manifest": manifest}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def record_contract_review(folder_context: str,
                            contract_id: str,
                            decision: str,
                            findings_json: Optional[dict[str, Any]] = None,
                            audience_side: str = "",
                            contract_type: str = "",
                            jurisdiction_anchors: Optional[list[str]] = None,
                            total_value_eur: Optional[float] = None,
                            actor: str = "",
                            run_id: str = "") -> dict[str, Any]:
    """Persist a contract review's Composer output to the workspace's
    mutation log. Used by the Contract Governance Workbench to surface
    traffic-light status across many documents without re-running
    review on every dashboard load.

    Args:
        folder_context: workspace path.
        contract_id: stable document identifier (e.g. ENTITY-YEAR-TYPE-SEQUENCE).
        decision: Composer decision — "Approve" / "Approve with Conditions" / "Block".
        findings_json: full Composer Hand-Off JSON or the section-3 findings array.
        audience_side: audience_side from orchestrator intake (artist / label / etc.).
        contract_type: contract_type from orchestrator classification.
        jurisdiction_anchors: jurisdictions touched (EU / UK / US / DE / FR / INTL / OTHER:<ISO>).
        total_value_eur: contract value if known.
        actor: identifier of caller (defaults to ``system``).
        run_id: orchestrator run id for cross-reference.

    Returns ``{ok, audit_id, contract_id, traffic_light, spill_path}``.
    """
    try:
        from .contracts.reviews import record_contract_review as _rec
        res = _rec(
            folder_context,
            contract_id=contract_id,
            decision=decision,
            findings_json=findings_json,
            actor=actor or _default_actor(),
            audience_side=audience_side,
            contract_type=contract_type,
            jurisdiction_anchors=jurisdiction_anchors,
            total_value_eur=total_value_eur,
            run_id=run_id,
            log_root=_log_root(),
        )
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def list_contract_reviews(folder_context: str,
                           filters: Optional[dict[str, Any]] = None,
                           include_findings: bool = False) -> dict[str, Any]:
    """Return contract reviews recorded in the workspace, latest-per-contract,
    newest first.

    Args:
        folder_context: workspace path.
        filters: optional dict — accepts ``decision``, ``traffic_light``,
                 ``jurisdiction``, ``audience_side``, ``min_severity``,
                 ``since``, ``contract_id``.
        include_findings: when True, embeds the full findings JSON
                          (incurs spill-file reads for large reviews).

    Returns ``{ok, folder_context, count, reviews}``.
    """
    try:
        from .contracts.reviews import list_contract_reviews as _list
        rows = _list(
            folder_context,
            filters=filters or {},
            include_findings=bool(include_findings),
            log_root=_log_root(),
        )
        return {
            "ok":             True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "count":          len(rows),
            "reviews":        rows,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def request_contract_approval(folder_context: str,
                                contract_id: str,
                                signers: list[str],
                                deadline: str = "",
                                requested_by: str = "",
                                reason: str = "",
                                action_summary: str = "",
                                idempotency_key: str = "") -> dict[str, Any]:
    """Open an approval request against a contract.

    Args:
        folder_context: workspace path.
        contract_id: stable document identifier.
        signers: list of signer identifiers (emails / names / role labels).
        deadline: optional ISO timestamp after which approval auto-expires.
        requested_by: actor identifier.
        reason: free-text reason / context the signers will see.
        action_summary: one-liner describing what is being approved
            (required context for requests arriving via the gateway).
        idempotency_key: when set, a retry with the same
            (contract_id, requested_by, idempotency_key) returns the
            existing approval instead of filing a duplicate — workflow
            engines deliver at-least-once.

    Returns ``{ok, approval_id, contract_id, state, signers, deduplicated}``.
    """
    try:
        from .contracts.reviews import request_contract_approval as _req
        res = _req(
            folder_context,
            contract_id=contract_id,
            signers=signers,
            deadline=deadline,
            requested_by=requested_by or _default_actor(),
            reason=reason,
            action_summary=action_summary,
            idempotency_key=idempotency_key,
            log_root=_log_root(),
        )
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def record_contract_approval(folder_context: str,
                              approval_id: str,
                              signer: str,
                              decision: str,
                              comment: str = "",
                              actor: str = "") -> dict[str, Any]:
    """Record one signer's decision on an existing approval request.

    Args:
        folder_context: workspace path.
        approval_id: id returned by ``request_contract_approval``.
        signer: which signer is recording (must match one of the request's signers).
        decision: ``"approved" | "rejected" | "expired"``.
        comment: optional free-text reason.
        actor: identifier of caller (defaults to ``signer``).

    Returns ``{ok, approval_id, contract_id, overall_state, signer_decisions}``.
    """
    try:
        from .contracts.reviews import record_contract_approval as _rec
        res = _rec(
            folder_context,
            approval_id=approval_id,
            signer=signer,
            decision=decision,
            comment=comment,
            actor=actor or signer or _default_actor(),
            log_root=_log_root(),
        )
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def list_contract_approvals(folder_context: str,
                             state: str = "",
                             contract_id: str = "") -> dict[str, Any]:
    """Return approval requests in the workspace, newest first.

    Args:
        folder_context: workspace path.
        state: optional filter — ``pending | approved | rejected | expired``.
        contract_id: optional filter — return only this contract's approvals.

    Returns ``{ok, folder_context, count, approvals}``.
    """
    try:
        from .contracts.reviews import list_contract_approvals as _list
        rows = _list(
            folder_context,
            state=state or None,
            contract_id=contract_id or None,
            log_root=_log_root(),
        )
        return {
            "ok":             True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "count":          len(rows),
            "approvals":      rows,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def _op_call(op: str, table: dict, params: dict) -> dict[str, Any]:
    """Generic facade dispatcher: route op→function, validate required params via
    signature introspection, forward only accepted keys. Self-documents on help."""
    import inspect
    if op in ("help", "ops", "catalogue"):
        out = []
        for k, fn in table.items():
            sig = inspect.signature(fn)
            req = [n for n, pp in sig.parameters.items()
                   if pp.default is inspect.Parameter.empty
                   and pp.kind in (pp.POSITIONAL_OR_KEYWORD, pp.KEYWORD_ONLY)]
            out.append({"op": k, "required": req})
        return {"ops": out}
    fn = table.get(op)
    if fn is None:
        return {"error": f"unknown op {op!r}", "valid_ops": sorted(table)}
    from .principal import apply_principal_to_params
    refused = apply_principal_to_params(fn, params)
    if refused is not None:
        return refused
    sig = inspect.signature(fn)
    accepted = {n: v for n, v in params.items() if n in sig.parameters}
    missing = [n for n, pp in sig.parameters.items()
               if pp.default is inspect.Parameter.empty
               and pp.kind in (pp.POSITIONAL_OR_KEYWORD, pp.KEYWORD_ONLY) and n not in accepted]
    if missing:
        return {"error": f"op {op!r} missing params: {missing}"}
    return fn(**accepted)

def contract_ingest(folder_context: str, text: str, contract_id: str = "",
                    language: str = "en", source_document: str = "",
                    actor: str = "ingest") -> dict[str, Any]:
    """Ingest one contract end-to-end: ContractInstance (typed fields or honest
    "not extracted"), defined terms, versioned span-norms, obligations.
    Idempotent on identical bytes."""
    try:
        from .contracts.extractor import ingest_contract
        res = ingest_contract(folder_context, text, contract_id=contract_id,
                              language=language, source_document=source_document,
                              actor=actor, log_root=_log_root())
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def contract_state(folder_context: str) -> dict[str, Any]:
    """The workbench state: contracts, clauses, obligations (with deadline
    derivations), the decision queue, and the audit view — one JSON."""
    try:
        from .workbench_io import export_state
        res = export_state(folder_context, log_root=_log_root())
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def contract_obligations(folder_context: str, state: str = "",
                         contract_ref: str = "",
                         obligation_id: str = "") -> dict[str, Any]:
    """Read-only obligations board. With no filter: the open buckets in
    severity order (pending / due_soon / due / breached_candidate / escalated),
    terminal counts, the decision-surface candidates, and the obligations whose
    deadline cannot be resolved. ``state`` or ``contract_ref`` narrow the list;
    ``obligation_id`` returns one obligation with its recorded history.
    Never advances a state — the clock moves only through the tick op."""
    try:
        from .contracts.instance import ContractRegistry
        from .obligation_runtime import (ObligationRegistry, OPEN_STATES,
                                         TERMINAL_STATES)
        reg = ObligationRegistry(folder_context, log_root=_log_root())
        contracts = ContractRegistry(folder_context, log_root=_log_root())

        def contract_for(ob):
            cid, _, ver = ob.contract_ref.partition("@")
            try:
                return contracts.get(cid, int(ver)) if ver else contracts.get(cid)
            except Exception:                                   # noqa: BLE001
                return None

        def row(ob) -> dict[str, Any]:
            deadline = ob.resolved_deadline(contract_for(ob))
            out = {"obligation_id": ob.obligation_id, "contract_ref": ob.contract_ref,
                   "summary": ob.summary, "obligor_role": ob.obligor_role,
                   "obligee_role": ob.obligee_role, "state": ob.state,
                   "deadline": deadline.iso if deadline else None}
            if deadline is None and ob.deadline_rel is not None:
                out["deadline_rel"] = ob.deadline_rel.to_dict()
            return out

        if obligation_id:
            ob = reg.get(obligation_id)
            if ob is None:
                return {"ok": False, "error": f"unknown obligation {obligation_id!r}"}
            return {"ok": True, "obligation": row(ob),
                    "history": reg.history(obligation_id)}
        if state:
            if state not in OPEN_STATES + TERMINAL_STATES:
                return {"ok": False, "error": f"unknown state {state!r}",
                        "valid_states": list(OPEN_STATES + TERMINAL_STATES)}
            obs = reg.in_state(state)
            if contract_ref:
                obs = [o for o in obs if o.contract_ref == contract_ref]
            return {"ok": True, "state": state,
                    "obligations": [row(o) for o in obs]}
        if contract_ref:
            return {"ok": True, "contract_ref": contract_ref,
                    "obligations": [row(o) for o in reg.for_contract(contract_ref)]}

        buckets = {s: [row(o) for o in reg.in_state(s)] for s in OPEN_STATES}
        return {"ok": True,
                "buckets": buckets,
                "counts": {s: len(rows) for s, rows in buckets.items()},
                "closed_counts": {s: len(reg.in_state(s)) for s in TERMINAL_STATES},
                "candidates": [o.obligation_id for o in reg.candidates()],
                "unresolved_deadlines": [r["obligation_id"]
                                         for rows in buckets.values()
                                         for r in rows if r["deadline"] is None]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def model_attest_baseline(model_id: str, probes: list,
                          folder_context: str,
                          actor: str = "app-user") -> dict[str, Any]:
    """Capture a model's gold probe set (governed write, recorded): each
    {id, input} probe runs against the model now; the signatures become the
    baseline later runs are held against."""
    try:
        from .attestation.runtime import baseline
        return baseline(str(model_id), list(probes or []), folder_context,
                        actor, log_root=_log_root())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def model_attest_run(model_id: str, folder_context: str,
                     actor: str = "app-user",
                     tolerance: int = 0) -> dict[str, Any]:
    """Run the probe battery and record the reconciled verdict
    (PASS / EXPLAINED_DRIFT / UNLOGGED_LEARNING) on the workspace's chain.
    A probe the model cannot answer is unobserved — a coverage gap, never
    drift; a battery that cannot run at all refuses without recording."""
    try:
        from .attestation.runtime import run_battery
        return run_battery(str(model_id), folder_context, actor,
                           tolerance=int(tolerance), log_root=_log_root())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def model_attest_admit(model_id: str, folder_context: str, note: str,
                       actor: str = "app-user") -> dict[str, Any]:
    """Declare a deliberate model change (recorded) so the next attestation
    reconciles drift as explained instead of alarming."""
    try:
        from .attestation.runtime import admit
        return admit(str(model_id), folder_context, actor, str(note or ""),
                     log_root=_log_root())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def model_attest_status(folder_context: str, model_id: str = "") -> dict[str, Any]:
    """Read-only: the recorded attestation state — latest run per model with
    its verdict and lists, baseline and admission counts. Never runs a probe."""
    try:
        from .attestation.runtime import status
        return status(folder_context, str(model_id or ""), log_root=_log_root())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_build(query: str, candidates: list, esc_reason: str = "",
                   context: str = "") -> dict[str, Any]:
    """Assemble a decision surface from candidate readings — pure, no write.
    Refuses an empty candidate list; a single candidate passes through with its
    warning set. Grounding is banded here and only the band leaves the server —
    thin (< 0.6: no supporting norm), moderate (< 0.9), firm (0.9 and above) —
    with the supporting-norm count; the raw score never reaches a client."""
    try:
        from .decisions.surface import build_surface
        cands = list(candidates or [])
        if not cands:
            return {"ok": False, "error": "no candidates — a decision surface"
                                          " needs at least one defensible reading"}
        surface = build_surface(str(query or ""), cands,
                                esc_reason=str(esc_reason or ""),
                                context=str(context or ""))
        d = surface.to_dict()
        for o in d.get("options", []):
            g = o.pop("grounding", 0.0)
            o["grounding_band"] = ("thin" if g < 0.6 else
                                   "moderate" if g < 0.9 else "firm")
            o["supporting_count"] = len(o.get("supporting") or [])
        d["ok"] = True
        return d
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_open(folder_context: str, surface: dict, raised_by: str,
                  competence: str = "",
                  claim_ttl_s: int = 14400,
                  escalate_to: str = "", escalate_after_s: int = 0,
                  write_reconfirm: bool = False,
                  auto_notify: bool = True,
                  idempotency_key: str = "", priority: str = "",
                  decide_by: str = "", panel: dict | None = None) -> dict[str, Any]:
    """Persist an escalation as a pending decision so routing can find its
    human: the competence (from the escalation, never an org chart) and the
    raising actor are recorded with it. ``escalate_to``/``escalate_after_s``
    declare the widening ladder (no claim within the window → the competence
    widens, recorded); ``write_reconfirm`` guards the eventual write with a
    fresh channel code. Holders with registered channels are notified
    (minimised payload + personal action link) unless ``auto_notify`` is off;
    delivery results ride the response and the entry."""
    try:
        from .decisions.queue import DecisionQueue
        out = DecisionQueue(folder_context, log_root=_log_root()).open(
            dict(surface or {}), raised_by=str(raised_by or ""),
            competence=str(competence or ""), claim_ttl_s=int(claim_ttl_s),
            escalate_to=str(escalate_to or ""),
            escalate_after_s=int(escalate_after_s),
            write_reconfirm=bool(write_reconfirm),
            idempotency_key=str(idempotency_key or ""),
            priority=str(priority or ""), decide_by=str(decide_by or ""),
            panel=dict(panel) if panel else None)
        if out.get("ok") and auto_notify and not out.get("deduplicated"):
            try:
                from .decisions.outbox import notify
                sent = notify(folder_context, out["decision_id"],
                              log_root=_log_root(), actor="system")
                out["notified"] = {"holders": sent.get("holders", 0),
                                   "sent_ok": sum(1 for r in sent.get("sent", [])
                                                  if r.get("ok"))}
            except Exception as e:              # noqa: BLE001 — the open stands
                out["notified"] = {"error": f"{type(e).__name__}: {e}"}
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_notify(folder_context: str, decision_id: str,
                    actor: str = "system") -> dict[str, Any]:
    """Deliver (or re-deliver, e.g. after an escalation widened the holders)
    the decision's minimised notification + personal action links to every
    holder's channels through the Lock-gated outbox. Every per-channel result
    is recorded, failures included."""
    try:
        from .decisions.outbox import notify
        return notify(folder_context, str(decision_id or ""),
                      log_root=_log_root(), actor=str(actor or "system"))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_reconfirm_request(folder_context: str,
                               link_token: str) -> dict[str, Any]:
    """Mint the write-confirmation code for a link-authenticated reviewer and
    send it to THEIR registered channels only — a forwarded link can request a
    code it will never see. The code is short-lived and single-use, and it
    leaves through the Lock's egress gate like any outbound message."""
    try:
        from .decisions.outbox import SENDERS, _deep_link  # noqa: F401
        from .decisions.queue import DecisionQueue
        q = DecisionQueue(folder_context, log_root=_log_root())
        v = q.verify_link(str(link_token or ""))
        if not v.get("ok"):
            return v
        minted = q.mint_reconfirm(v["decision_id"], v["party_id"])
        if not minted.get("ok"):
            return minted
        msg = {"title": "Your Rvnd confirmation code",
               "deep_link": f"code: {minted['code']}"}
        gate = lock_egress_check(tool="decision-reconfirm", arguments=msg,
                                 task_scope=["title", "deep_link"],
                                 folder_context=str(folder_context))
        if gate.get("action") != "allow":
            return {"ok": False, "error": "the Lock refused the code egress —"
                                          " nothing was sent: "
                                          + str(gate.get("reason", ""))}
        from .parties import list_parties
        me = next((p for p in list_parties(
            folder_context, log_root=str(_log_root()) if _log_root() else None
        ).get("parties", []) if p.get("party_id") == v["party_id"]), None)
        results = []
        for channel in (me or {}).get("channels", []):
            kind, _, address = str(channel).partition(":")
            sender = SENDERS.get(kind)
            res = (sender(address, msg) if sender
                   else {"ok": False, "detail": f"no sender for {kind!r}"})
            results.append({"channel": kind, "ok": bool(res.get("ok")),
                            "detail": str(res.get("detail", ""))})
        return {"ok": True, "decision_id": v["decision_id"], "sent": results}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_pending(folder_context: str, for_party: str = "") -> dict[str, Any]:
    """Read-only: the open decisions with their routing state (claimed by
    whom, until when, on what assignment basis). ``for_party`` narrows to what
    that party may claim — competence held via the resolver's roster, and
    never their own escalation."""
    try:
        from .decisions.queue import DecisionQueue
        return DecisionQueue(folder_context, log_root=_log_root()).pending(
            for_party=str(for_party or ""))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_dossier(folder_context: str, decision_id: str) -> dict[str, Any]:
    """Read-only: one pending decision's local context — the stored surface
    with grounding banded, the raiser's attributed runs and standing, and the
    recourse ladder. Same access posture as decision_pending; panel seats stay
    sealed (counts and commitments only) and no raw grounding score leaves."""
    try:
        from .decisions.dossier import decision_dossier as _dossier
        return _dossier(folder_context, decision_id=str(decision_id or ""),
                        log_root=_log_root())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_claim(folder_context: str, decision_id: str = "",
                   actor: str = "", link_token: str = "") -> dict[str, Any]:
    """Claim (lease) a pending decision: the first claim locks it for the TTL
    so two reviewers cannot decide the same card; expiry releases it back to
    every holder. The raiser cannot claim their own escalation. Recorded.
    With ``link_token`` the claimant is the token's bound party (the
    registered channel is the credential) and the claim records its rung."""
    try:
        from .decisions.queue import DecisionQueue
        q = DecisionQueue(folder_context, log_root=_log_root())
        rung = ""
        if link_token:
            v = q.verify_link(str(link_token))
            if not v.get("ok"):
                return v
            actor, decision_id, rung = v["party_id"], v["decision_id"], v["auth_rung"]
        else:
            from .principal import get_request_principal
            ctx = get_request_principal()
            if ctx and ctx.get("party") == (actor or "").strip():
                rung = ctx.get("rung", "")
        return q.claim(str(decision_id or ""), str(actor or ""), auth_rung=rung)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_release(folder_context: str, decision_id: str,
                     actor: str) -> dict[str, Any]:
    """Release a claim you hold — the decision widens back to every holder.
    Recorded."""
    try:
        from .decisions.queue import DecisionQueue
        return DecisionQueue(folder_context, log_root=_log_root()).release(
            str(decision_id or ""), str(actor or ""))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_link_mint(folder_context: str, decision_id: str, party_id: str,
                       ttl_s: int = 86400,
                       actor: str = "system") -> dict[str, Any]:
    """Mint a signed, single-use action link token binding one party to one
    open decision — the registered channel becomes the credential. The token
    is returned exactly once (only its hash is stored); it dies on use, on
    expiry, or when a competing claim takes the card. No link is minted for
    the escalation's raiser."""
    try:
        from .decisions.queue import DecisionQueue
        return DecisionQueue(folder_context, log_root=_log_root()).mint_link(
            str(decision_id or ""), str(party_id or ""),
            ttl_s=int(ttl_s), actor=str(actor or "system"))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def decision_record(folder_context: str, surface: dict | None = None,
                    chosen_option_id: str = "",
                    rationale: str = "", actor: str = "",
                    considered: list | None = None,
                    asked: list | None = None,
                    evidence_refs: list | None = None,
                    decision_id: str = "", link_token: str = "",
                    reconfirm_code: str = "") -> dict[str, Any]:
    """The one governed write of a decision surface: records the originated
    choice on the workspace's signed chain. The module's gate holds — a real
    option, a non-empty rationale, a named actor. ``considered`` carries only
    what the decider actually opened (empty records empty, never all);
    ``asked`` the recorded assistance exchanges; ``evidence_refs`` the works
    attached as grounds. With ``decision_id`` the choice resolves a PENDING
    decision: the stored surface is used, the raiser is refused as decider
    (separation of duties), a live foreign claim is honoured, and the entry
    closes with a pointer to the choice's audit event."""
    try:
        from .decisions.surface import DecisionSurface, Option, record_choice
        queue = None
        entry = None
        auth_rung = ""
        if not link_token:
            from .principal import get_request_principal
            ctx = get_request_principal()
            if ctx and ctx.get("party") == (actor or "").strip():
                auth_rung = ctx.get("rung", "")
        if link_token:
            from .decisions.queue import DecisionQueue
            queue = DecisionQueue(folder_context, log_root=_log_root())
            v = queue.verify_link(str(link_token))     # consumed only on success
            if not v.get("ok"):
                return v
            actor, decision_id, auth_rung = v["party_id"], v["decision_id"], v["auth_rung"]
        if decision_id:
            from .decisions.queue import DecisionQueue
            queue = queue or DecisionQueue(folder_context, log_root=_log_root())
            entry = queue.get(str(decision_id))
            if entry is None or entry.get("state") != "open":
                return {"ok": False, "error": f"no open decision {decision_id!r}"}
            if (actor or "").strip() == entry.get("raised_by"):
                return {"ok": False, "error": "separation of duties: the actor"
                                              " who raised this escalation"
                                              " cannot be its decider"}
            if entry.get("claimed_by") and entry["claimed_by"] != (actor or "").strip():
                return {"ok": False, "error": f"claimed by"
                                              f" {entry['claimed_by']!r} — the"
                                              f" lease holds until"
                                              f" {entry['claim_expires_at']}"}
            if link_token and entry.get("write_reconfirm"):
                ok = queue.verify_reconfirm(str(decision_id), str(actor),
                                            str(reconfirm_code or ""))
                if not ok.get("ok"):
                    return {"ok": False, "error": "this decision requires the"
                                                  " channel confirmation code"
                                                  " for its write: "
                                                  + ok.get("error", "")}
            if entry.get("panel"):
                seat = queue.record_seat(str(decision_id), str(actor or ""),
                                         str(chosen_option_id),
                                         str(rationale or ""),
                                         auth_rung=auth_rung)
                if not seat.get("ok") or not seat.get("resolved"):
                    if seat.get("ok") and link_token:
                        queue.verify_link(str(link_token), consume=True)
                    return seat
                # the panel met its rule: one closing record, jointly held
                chosen_option_id = seat["chosen_option_id"]
                rationale = (f"panel rule {entry['panel']['rule']} met"
                             f" ({seat['panel']['recorded']} of"
                             f" {seat['panel']['seats']} seats) — each seat's"
                             " choice and rationale is on the chain,"
                             " referenced by the closing event")
                actor = "panel(" + ",".join(seat["panel"]["recorded_by"]) + ")"
                evidence_refs = list(evidence_refs or []) + seat["seat_audit_ids"]
                auth_rung = "panel"
            surface = entry["surface"]
        opts = []
        for o in (surface or {}).get("options", []):
            opts.append(Option(
                id=str(o.get("id", "")), label=str(o.get("label", "")),
                conclusion=str(o.get("conclusion", "")),
                supporting=list(o.get("supporting") or []),
                reasons=str(o.get("reasons", "")),
                consequences=list(o.get("consequences") or [])))
        if not opts:
            return {"ok": False, "error": "surface carries no options"}
        s = DecisionSurface(
            query=str((surface or {}).get("query", "")), options=opts,
            esc_reason=str((surface or {}).get("esc_reason", "")),
            context=str((surface or {}).get("context", "")),
            single_reading_warning=bool((surface or {}).get("single_reading_warning")),
            options_may_be_incomplete=bool((surface or {}).get("options_may_be_incomplete", True)),
            note=str((surface or {}).get("note", "")))
        res = record_choice(
            s, chosen_option_id=str(chosen_option_id or ""),
            rationale=str(rationale or ""), actor=str(actor or ""),
            folder=folder_context, log_root=_log_root(),
            considered=list(considered) if considered is not None else [],
            asked=list(asked) if asked else None,
            evidence_refs=list(evidence_refs) if evidence_refs else None,
            auth_rung=auth_rung,
            corpus=folder_context)
        if "error" in res:
            return {"ok": False, **res}
        if queue is not None and entry is not None:
            if link_token:
                queue.verify_link(str(link_token), consume=True)   # single-use: spent by the successful write
            queue.close(str(decision_id), str(actor or "").strip(),
                        choice_audit_id=str(res.get("audit_id", "")))
            res["decision_id"] = str(decision_id)
        return {"ok": True, **res}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def contract_tick(folder_context: str, as_of: str = "",
                  autonomy_grade: str = "L2") -> dict[str, Any]:
    """Deterministic scheduler sweep. Advances obligation states by date
    arithmetic only; the machine stops at breached_candidate — breach is a
    human judgment on the decision surface. Replay-safe."""
    try:
        from .obligation_scheduler import ObligationScheduler
        from workspaces.adapters.solver.temporal import Date
        sched = ObligationScheduler(folder_context, log_root=_log_root(),
                                    autonomy_grade=autonomy_grade)
        report = sched.tick(Date(as_of) if as_of else None)
        return {"ok": True, **report.to_dict()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def contract_apply(folder_context: str, actions: list) -> dict[str, Any]:
    """Apply queued workbench actions (resolve_obligation | bind_term |
    record_correction) through the gated registries — named actor + rationale
    enforced; failures reported per action, never skipped."""
    try:
        from .workbench_io import apply_actions
        res = apply_actions(folder_context, list(actions or []),
                            log_root=_log_root())
        res["ok"] = res.get("ok", False)
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def contract_resolve(folder_context: str, obligation_id: str, choice: str,
                     actor: str, rationale: str) -> dict[str, Any]:
    """Record a human resolution (satisfied | waived) of one obligation.
    Anonymous or rationale-free resolutions are refused by the registry."""
    try:
        from .obligation_runtime import ObligationRegistry
        reg = ObligationRegistry(folder_context, log_root=_log_root())
        rec = reg.resolve(obligation_id, choice, actor=actor, reason=rationale)
        return {"ok": True, "obligation": rec}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def contract_demo(folder_context: str) -> dict[str, Any]:
    """Build the validation demo state: ingest the 5-template corpus and run
    the two protocol ticks. Regenerable, synthetic parties only."""
    try:
        from .workbench_io import build_demo
        res = build_demo(folder_context, log_root=_log_root())
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_ground(folder_context: str, claim: str, works: list,
                    style: str = "apa", method: str = "researcher",
                    agent: str = "", confidence: float = 0.0,
                    locator: str = "") -> dict[str, Any]:
    """One-shot: register works, ground the claim, return formatted citations.

    ``works``: list of dicts — ``title`` required; ``creators`` / ``container``
    / ``publisher`` / ``date`` / ``url`` / ``doi`` / ``type`` as known.
    Refuses (and audits) when no work is supplied: no citation, no claim.
    """
    try:
        from .workspace_grounder import ground as _ground
        res = _ground(folder_context, claim, works, style=style, method=method,
                      agent=agent, confidence=confidence, locator=locator,
                      log_root=str(_log_root()) if _log_root() else None)
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_register_work(folder_context: str, title: str, type: str = "web",
                           creators: list | None = None, container: str = "",
                           publisher: str = "", date: str = "", url: str = "",
                           accessed: str = "", doi: str = "",
                           identifiers: dict | None = None,
                           language: str = "",
                           retrieved_by: str = "researcher") -> dict[str, Any]:
    """Register one cited work in the folder's grounding ledger (idempotent)."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.register_work(
            title=title, type=type, creators=creators, container=container,
            publisher=publisher, date=date, url=url, accessed=accessed,
            doi=doi, identifiers=identifiers, language=language,
            retrieved_by=retrieved_by)
        res["ok"] = True
        return res
    except Exception as e:
        # ``type`` is the public work-type parameter above, so calling
        # ``type(e)`` here attempts to call a string precisely when the
        # operation fails.  Use the exception class directly and preserve the
        # API-compatible parameter name.
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}

def grounder_claim_status(folder_context: str, claim_id: str, status: str,
                          by: str = "", note: str = "") -> dict[str, Any]:
    """Set a claim's status (verified | disputed | retracted). Disputed claims
    are residuals — surface them to the human, never resolve them."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.set_claim_status(claim_id, status, by=by, note=note)
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_add_provenance(folder_context: str, from_work: str,
                            relation: str, to_work: str, evidence: str = "",
                            basis: str = "") -> dict[str, Any]:
    """Record one followed citation: from_work --relation--> to_work
    (relations: cites | quotes | derives_from | republishes | translates |
    summarizes | responds_to). The swarm calls this for every link it walks."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.add_provenance(from_work, relation, to_work,
                                    evidence=evidence, basis=basis)
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_trace(folder_context: str, work_id: str,
                   max_depth: int = 8) -> dict[str, Any]:
    """Trace a work's provenance upstream to root works and the entities
    behind them (creators, publishers, corpus refs). Cycle-safe."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.trace(work_id, max_depth=max_depth)
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_frontier(folder_context: str) -> dict[str, Any]:
    """Works whose citations are not yet traced — the research swarm's next
    targets."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.frontier()
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_bibliography(folder_context: str, style: str = "apa",
                          work_ids: list | None = None) -> dict[str, Any]:
    """Formatted bibliography in the chosen style (apa | mla | chicago |
    harvard | ieee | vancouver) for the ledger or a subset of works."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.bibliography(style=style, work_ids=work_ids)
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_oversight_feed(folder_context: str, limit: int = 50) -> dict[str, Any]:
    """The output-review feed: grounding-gate decisions (grounded / flagged /
    stopped) off the signed chain — how the grounder responded to oversight."""
    try:
        from .governance import grounding_feed
        res = grounding_feed(folder_context, log_root=_log_root(), limit=int(limit))
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def grounder_coverage(folder_context: str) -> dict[str, Any]:
    """The honor-creators report: attribution completeness, claims by status,
    works missing creators/links/dates, disputed residuals."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.coverage()
        res["ok"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_forget_subject(folder_context: str, name: str) -> dict[str, Any]:
    """Remove a creator from the grounding ledger and entity corpus.

    The removal is audited as a purge. Claims mentioning the subject are
    returned for human review, never auto-edited.
    """
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.forget_subject(name)
        res["ok"] = res.get("status") == "ok"
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_check_claim(folder_context: str, claim_id: str,
                         model: str = "") -> dict[str, Any]:
    """Semantic claim-support check via the local-LLM route (scaffold;
    production-gated on the gold-set). Verdict supports | does_not_support |
    insufficient; anything but supports escalates, never auto-retracts."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.check_claim_support(claim_id, model=model)
        res["ok"] = res.get("status") == "ok"
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_ingest_source(folder_context: str, content: str, url: str = "",
                           title: str = "", use_model: bool = False,
                           model: str = "",
                           follow_references: bool = True) -> dict[str, Any]:
    """Swarm per-page step: register a fetched page as a work (deterministic
    meta tags first; local-LLM fallback only if use_model and only for
    fields the tags missed — model values must occur verbatim in the source
    or are dropped), hash the content for fixity, register every DOI/arXiv/
    URL reference found, and record cites provenance edges."""
    try:
        from .grounder_extract import ingest_source
        model_fn = None
        if use_model:
            from .local_llm import complete

            def model_fn(prompt: str) -> str:           # noqa: F811
                res = complete(prompt, model=model or None, max_tokens=512)
                if not res.get("ok"):
                    raise RuntimeError(res.get("error", "local-LLM failed"))
                return res.get("response", "")
        res = ingest_source(folder_context, content, url=url, title=title,
                            model_fn=model_fn,
                            follow_references=follow_references,
                            log_root=str(_log_root()) if _log_root() else None)
        res["ok"] = res.get("status") == "ok"
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_classify_creators(folder_context: str,
                               model: str = "") -> dict[str, Any]:
    """Fill missing person/org roles on creators via the local-LLM route
    (drives citation formatting; org names are never split). Proposed +
    recorded, never overwrites an existing role."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.classify_creator_roles(model=model)
        res["ok"] = res.get("status") == "ok"
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def grounder_link_entities(folder_context: str) -> dict[str, Any]:
    """Ingest every creator into the folder's entity corpus so provenance of
    ideas joins the workspace's entity map."""
    try:
        from .workspace_grounder import GroundingLedger
        ledger = GroundingLedger(folder_context, log_root=_log_root())
        res = ledger.link_creators_to_corpus()
        res["ok"] = res.get("status") == "ok"
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
