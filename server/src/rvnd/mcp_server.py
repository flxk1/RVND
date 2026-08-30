# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""MCP-server wrapper for workspace-l0-memory.

Exposes the L0 surface as MCP tools so any MCP-compatible host (Claude
Desktop, Cowork, Cursor, an agent framework) can call them — and so
cross-plugin integration with ``agent-tool-lock`` is transport-mediated
rather than requiring both plugins to share a Python process.

Tools exposed:

- ``capture_llm``     — record an LLM exchange (AGENTIC audit floor + INTERACTIVE opt-in)
- ``capture_web``     — record a web-search exchange
- ``load_policy``     — read a folder's policy snapshot (lock + oversight state)
- ``l0_search``       — keyword similarity search across folder + descendants
- ``l0_by_id``        — direct pair lookup
- ``l0_recent``       — list recent pairs in scope (diagnostic)

Configuration via environment variables:

- ``WORKSPACE_L0_LOG_ROOT`` — override the default log root (per-machine).
- ``WORKSPACE_L0_DEFAULT_ACTOR`` — actor identifier recorded in audit when
  a tool call doesn't pass one (default: ``"mcp:l0"``).

Run::

    python -m rvnd.mcp_server

or, with the installed entry point::

    workspace-l0-mcp
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from .memory import WorkspaceMemory
from . import session_mcp  # S12 — session save/load facade


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

# The front door. An agent connecting here is IDENTIFIED (its principal → party →
# competences) and GOVERNED: its actions are sorted through the boundaries this
# workspace has set. The handshake hands it the governance LANGUAGE up front
# (llms.txt, consumed from loomground-governance) so it grounds every action in
# that language before it acts — and every tool call is planned + gated; RVND
# plans, the host executes and reports back; an ungoverned action is refused.
_FRONT_DOOR = (
    "RVND — a local-first governance server. When you connect you are IDENTIFIED "
    "and GOVERNED: your actions are sorted through the boundaries (gates) this "
    "workspace has set for the agents it admits. Every tool call is planned and "
    "gated (GO / CONDITIONAL / NO-GO): RVND plans the call, you execute it and "
    "report the outcome back — an unplanned or refused action must not proceed, "
    "and refusal (NO-GO) is a valid, expected outcome. Before acting, read the "
    "governance LANGUAGE this server speaks — the `governance://llms.txt` resource "
    "(the vocabulary, the gates, how citations and refusals work) — and ground "
    "every governance action in it."
)
mcp = FastMCP("workspace", instructions=_FRONT_DOOR)

# FastMCP leaves the low-level server's version unset; mcp >= 1.28 rejects a
# None server_version when the streamable-http host initializes, so the host and
# gateway cannot start without it. Declare the installed package version (a
# placeholder when the dist is unresolved, e.g. a source run) on the low-level
# server so initialization succeeds and the client sees a real version.
try:
    from importlib.metadata import version as _srv_pkg_version
    _server_version = _srv_pkg_version("rvnd")
except Exception:
    _server_version = "0+source"
try:
    mcp._mcp_server.version = _server_version
except AttributeError:
    pass


@mcp.resource("governance://llms.txt")
def governance_language() -> str:
    """The governance LANGUAGE the agent is handed at the front door — the
    vocabulary, the gates, how citations and refusals work. CONSUMED from
    loomground-governance (``artifact_path``), never a copy, so it cannot drift
    from the canonical language. This is what makes governance effective: the
    agent grounds every action in the language before it can act, and every
    action is gated at this handshake. Consumed through the assets seam, so this
    module never imports the upstream package directly."""
    from .loomground_assets import llms_txt
    return llms_txt()

# Implementation handlers (split to mcp_impl.py; this file is the surface).
from .mcp_impl import (
    _default_actor,
    capture_llm,
    capture_web,
    capture_read,
    policy_snapshot,
    _workspace_lock_guard,
    _rank_served,
    workspace_lock_unlock,
    workspace_lock_lock,
    lock_setup_status,
    lock_setup_run,
    model_runtime_status,
    model_attest_baseline,
    model_attest_run,
    model_attest_admit,
    model_attest_status,
    recent,
    pair_safe_context,
    pairs_safe_context_for_query,
    workspace_remember,
    workspace_query,
    _sanitise_filename,
    ingest_path,
    ingest_url,
    list_urls,
    policy_set_lock_mode,
    policy_enable_lock,
    policy_disable_lock,
    policy_enable_oversight,
    policy_disable_oversight,
    policy_enable_discipline,
    policy_disable_discipline,
    discipline_audit,
    fetch_pair_spans,
    lock_classify_text,
    lock_threshold_get,
    lock_threshold_set,
    lock_reclassify_folder,
    pin_skill_to_folder,
    pin_skills_to_folder,
    unpin_skill_from_folder,
    list_pinned_skills,
    dispatch_skill,
    dispatch_skills_batch,
    ingest_skill,
    dispatch_ingested,
    import_plugin,
    dispatch_skill_dry_run,
    list_plugin_skills,
    define_workflow,
    list_workflows,
    delete_workflow,
    run_workflow,
    add_known_workspace,
    remove_known_workspace,
    bootstrap_default_workspace,
    enqueue_workflow_run,
    list_queue,
    take_next_run,
    renew_lease,
    mark_run_done,
    mark_run_failed,
    inspect_stuck_runs,
    resume_run,
    cancel_run,
    get_audit_event,
    set_pair_layout,
    get_pair_layouts,
    active_workflows,
    suggest_companion_skills,
    resolve_skills_for_query,
    local_llm_complete,
    local_llm_classify,
    local_llm_list_available,
    lock_egress_check,
    lock_ingress_check,
    lock_audit_query,
    pairs_recent,
    folder_list,
    folder_create,
    folder_scan,
    folder_reextract,
    folder_ingest,
    pair_spans,
    mirror_generate,
    mirror_approve,
    mirror_edit,
    mirror_un_redact,
    mirror_history,
    mirror_diff,
    mirror_discard,
    mirror_lock_acquire,
    mirror_lock_release,
    mirror_list,
    erase_sweep,
    erase_subject,
    erase_request,
    erase_status,
    record_contract_review,
    list_contract_reviews,
    request_contract_approval,
    record_contract_approval,
    list_contract_approvals,
    _op_call,
    contract_ingest,
    contract_state,
    contract_obligations,
    contract_tick,
    decision_build,
    decision_record,
    decision_open,
    decision_pending,
    decision_dossier,
    decision_claim,
    decision_release,
    decision_link_mint,
    decision_notify,
    decision_reconfirm_request,
    contract_apply,
    contract_resolve,
    contract_demo,
    grounder_ground,
    grounder_register_work,
    grounder_claim_status,
    grounder_add_provenance,
    grounder_trace,
    grounder_frontier,
    grounder_bibliography,
    grounder_coverage,
    grounder_oversight_feed,
    grounder_forget_subject,
    grounder_check_claim,
    grounder_ingest_source,
    grounder_classify_creators,
    grounder_link_entities,
    _LOCK_CLASSIFY_BUCKET,  # noqa: F401  -- re-export: reached as a module attribute, not an import
    )

# Serving helpers (extracted to keep this file the assembler, not the impl).
from .mcp_serving import (
    _rerank_by_dimension,  # noqa: F401  -- re-export: reached as a module attribute, not an import
    )

# Dynamic seam: resolve _log_root through mcp_serving so tests that patch
# rvnd.mcp_serving._log_root take effect in every module (split-safe).
from . import mcp_serving as _mcp_serving
def _log_root():
    return _mcp_serving._log_root()












# ---------------------------------------------------------------------------
# Capture tools
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Policy tool
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Workspace Lock — at-rest seal session (unlock / lock)
# ---------------------------------------------------------------------------












# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def route_to_workspace(query: str, limit: int = 5) -> dict[str, Any]:
    """Suggest which registered workspace(s) should handle a query or a dropped file.

    Deterministic concept routing ("cells-lite"): scores the query's tokens
    against each workspace's concept signature (its label + served pairs). No model,
    no network. Sealed+locked workspaces are scored on their label only and flagged
    ``label_only``. Returns candidates ranked best-first."""
    from . import router, registry
    lr = _log_root()
    try:
        ws = registry.list_known_workspaces(log_root=lr)
    except Exception as e:
        return {"ok": False, "error": str(e), "query": query, "candidates": []}
    folders = [w.get("path") for w in ws if w.get("path")]
    labels = {w["path"]: w.get("label", "") for w in ws if w.get("path")}
    ranked = router.route(query, folders, log_root=lr, labels=labels, limit=limit)
    return {"ok": True, "query": query, "count": len(ranked), "candidates": ranked}


# ---------------------------------------------------------------------------
# Read-side tools
# ---------------------------------------------------------------------------


# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def search(
    folder_context: str,
    query: str,
    k: int = 5,
) -> dict[str, Any]:
    """Keyword-similarity search across folder + descendants + ancestor-distributed.

    Honours the asymmetric hierarchical rule: sibling folders are out of scope.

    Args:
        folder_context: absolute path of the folder to search from.
        query: free-text query (Phase-1 uses Jaccard over tokens).
        k: maximum number of results.

    Returns:
        Dict with ``results`` (list of pair bodies) and ``folder_context``.
    """
    k = max(1, min(int(k), 50))
    _resolved = str(Path(folder_context).expanduser().resolve())
    _state, _payload = _workspace_lock_guard(folder_context)
    if _state == "locked":
        return {**_payload, "query": query, "count": 0, "results": []}
    if _state == "served":
        ranked = _rank_served(_payload, query, k)
        return {"folder_context": _resolved, "query": query, "k": k,
                "served_sealed": True, "results": ranked}
    mem = WorkspaceMemory(folder_context, log_root=_log_root(), actor=_default_actor())
    hits = mem.search(query, k=k)
    return {
        "folder_context": str(Path(folder_context).expanduser().resolve()),
        "query": query,
        "k": k,
        "results": hits,
    }


# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def by_id(folder_context: str, pair_id: str) -> dict[str, Any]:
    """Direct pair lookup.

    Returns ``{"found": True, "pair": ...}`` or ``{"found": False}``.
    Honours the asymmetric rule (own scope) plus ancestor-distributed pairs.
    Deleted / purged / rejected pairs return not-found.
    """
    _state, _payload = _workspace_lock_guard(folder_context)
    if _state == "locked":
        return {"found": False, "pair_id": pair_id, **_payload}
    if _state == "served":
        p = _payload.get(pair_id)
        if p is None:
            return {"found": False, "pair_id": pair_id, "served_sealed": True}
        return {"found": True, "pair": p, "served_sealed": True}
    mem = WorkspaceMemory(folder_context, log_root=_log_root(), actor=_default_actor())
    pair = mem.by_id(pair_id)
    if pair is None:
        return {"found": False, "pair_id": pair_id}
    return {"found": True, "pair": pair}




# ---------------------------------------------------------------------------
# Audit chain verification (B2, 0.6.8)
# ---------------------------------------------------------------------------


# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def audit_verify_chain(folder_context: str,
                        actor: str = "") -> dict[str, Any]:
    """Walk the folder's mutation-log chain and report integrity.

    Surface parity with ``workspaces status --folder ...`` — non-Python MCP
    clients can now probe a folder's tamper-evidence state. Combines the
    SHA-256 hash chain check with Ed25519 signature verification, and
    distinguishes authorised purge-tombstone re-links from raw tampering.

    Audit-of-audit (D8): each call self-logs a ``system`` event with
    ``extra.kind="verify_chain_read"`` to the same mutation log so the
    invocation itself is part of the audit trail. The chain count visible
    on the NEXT call therefore includes this one's breadcrumb.

    Args:
        folder_context: workspace path to verify.
        actor:          actor identifier for the self-log breadcrumb
                        (defaults to the env default / ``"mcp:l0"``).

    Returns:
        Dict with:
        - ``ok``: True iff no broken links and no signature failures
        - ``total_events``: well-formed events walked
        - ``legacy_events``: events with no ``prev_hash`` (pre-0.6.5)
        - ``unsigned_events``: events with no ``signature`` (pre-0.6.6)
        - ``broken_links``: list of hash-chain mismatches
        - ``signature_failures``: list of Ed25519 verify failures
        - ``malformed_lines``: count of unparseable JSONL lines
        - ``purged_with_tombstone``: count of authorised re-links (B1)
        - ``public_key_fingerprint``: 16-hex fingerprint of operator pubkey
        - ``controller_key_fingerprint``: same for controller pubkey, or
          ``None`` if controller key not initialised
        - ``host_id``: 12-char host fingerprint (B4)
        - ``folder_context``: the resolved folder path
    """
    from .mutation_log import MutationLog, LogEvent
    from . import signing

    resolved = str(Path(folder_context).expanduser().resolve())
    log = MutationLog(resolved, log_root=_log_root())
    result = log.verify_chain()

    # Best-effort key fingerprints — surface unavailability without
    # killing the call.
    try:
        op_fp = signing.public_key_fingerprint()
    except Exception:
        op_fp = ""
    try:
        ctrl_fp = signing.public_controller_key_fingerprint()
    except Exception:
        ctrl_fp = None
    try:
        host_id = signing._host_id()
    except Exception:
        host_id = ""

    out: dict[str, Any] = {
        "ok":                       bool(result.ok),
        "total_events":             int(result.total_events),
        "legacy_events":            int(result.legacy_events),
        "unsigned_events":          int(result.unsigned_events),
        "broken_links":             list(result.broken_links),
        "signature_failures":       list(result.signature_failures),
        "malformed_lines":          int(result.malformed_lines),
        "purged_with_tombstone":    int(getattr(result, "purged_with_tombstone", 0)),
        "public_key_fingerprint":   op_fp,
        "controller_key_fingerprint": ctrl_fp,
        "host_id":                  host_id,
        "folder_context":           resolved,
    }

    # D8: self-log this call so the audit chain records who/when verified.
    # Best-effort — if the self-log itself fails (e.g. fully read-only fs),
    # the verification result still returns.
    try:
        log.append(LogEvent(
            event="system",
            folder_path=resolved,
            pair_id=f"audit:verify_chain:{int(time.time() * 1000)}",
            channel="system",
            actor=actor or _default_actor(),
            extra={
                "kind":  "verify_chain_read",
                "actor": actor or _default_actor(),
                "ok":    bool(result.ok),
                "total_events": int(result.total_events),
            },
        ))
    except Exception:
        # Audit-of-audit is best-effort. Verification was the user's ask.
        pass

    return out


# ---------------------------------------------------------------------------
# Filesystem helpers (for the dashboard's file explorer)
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Safe-context tools — boundary-respecting view of a pair for cloud LLMs
# ---------------------------------------------------------------------------
#
# Designed for the "chat with this folder" surface in the dashboard. Three
# layers ranked from least-leaky to most-leaky:
#
#   1. fingerprint   — taxonomy + structured facets only (modal, subject,
#                      has_condition, …). No content.
#   2. triples       — subject-predicate-object derived from scope/type/facets
#                      + opaque doc-token. Joins work, content stays local.
#   3. anonymised    — summary + rendered rule body passed through
#      spans         lock_text(). Regex-PII redacted; KG-confidential terms
#                      redacted if Tier-C backend is available.
#
# Folder policy is honoured: lock-ON folders default to fingerprint+triples
# (`safe_minimal`); lock-OFF folders may include anonymised spans
# (`safe_full`).  Caller can override with the ``mode`` argument.
#
# Pairs whose summary or body refuses locking (Mode.STANDARD refuse) are
# returned with action="refuse" — the artifact must drop them from the prompt.


































# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def reason(
    folder_context: str,
    start: str = "",
    max_depth: int = 3,
    min_confidence: float = 0.0,
    max_results: int = 50,
    record: bool = True,
) -> dict[str, Any]:
    """Reason over the folder's five-dimensional edge graph.

    Composes the dimensioned edges in this folder's memory into multi-hop
    inferences — causal chains, structural decompositions, and so on — each
    with its composed dimension, a confidence (the product of the edges'
    weights), and full provenance: the ordered source edges and the pairs they
    came from. Reasoning runs over grounded facts only; previously recorded
    inferences are excluded so it cannot feed on itself.

    When ``record`` is true (default) each inference is written to the signed
    mutation log (channel ``reasoning``) with its provenance, so the derivation
    is auditable — an auditor can read the log and reconstruct which source
    pairs and edges produced a conclusion. Returns the inferences and the ids
    of any recorded.
    """
    import hashlib
    mem = WorkspaceMemory(folder_context, log_root=_log_root(), actor=_default_actor())
    # Versum is the canonical knowledge plane. During the bounded data-migration
    # window an unindexed legacy workspace remains readable, but the response names
    # that compatibility source explicitly; there is no invisible engine fallback.
    from .adapters.versum import VersumKnowledgeStore, VersumSolverSource
    # Versum is the ONLY knowledge plane (Language -> Ingest -> Versum -> Solver).
    # Reasoning is fail-closed: an unindexed workspace is refused ("index the
    # folder first"), never served from a non-Versum overlay. The legacy pair
    # overlay was retired so nothing reasons off a source other than Versum.
    knowledge = VersumKnowledgeStore(folder_context)
    if not knowledge.has_records:
        return {
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "knowledge_backend": None,
            "error": "versum index required — index the folder with "
                     "loomground-versum before reasoning",
            "count": 0, "recorded": 0, "recorded_ids": [], "inferences": [],
        }
    knowledge_backend = "loomground-versum"
    inferences = VersumSolverSource(knowledge).paths(
        start=(start or None),
        max_depth=max(2, min(int(max_depth), 6)),
        min_confidence=max(0.0, min(float(min_confidence), 1.0)),
        max_results=max(1, min(int(max_results), 500)),
    )

    recorded: list[str] = []
    if record:
        for inf in inferences:
            h = hashlib.sha256(
                "|".join(
                    [inf.subject, inf.object, inf.dimension.value]
                    + [f"{hop['source_pair']}:{hop['dimension']}" for hop in inf.path]
                ).encode("utf-8")
            ).hexdigest()[:32]
            pid = f"sha256:reason-{h}"
            pair = {
                "id": pid,
                "problem": {
                    "id": f"{pid}-p", "scope": "reasoning", "type": "inference",
                    "summary": f"{inf.subject} -> {inf.object} [{inf.dimension.value}]",
                    "facets": {
                        "start": inf.subject, "end": inf.object,
                        "dimension": inf.dimension.value, "hops": inf.hops,
                        "dimension_chain": inf.dimension_chain, "via": inf.path,
                    },
                },
                "solution": {
                    "id": pid, "problem_id": f"{pid}-p",
                    "body": " -> ".join([inf.subject] + [hop["object"] for hop in inf.path]),
                    "body_format": "inference-path",
                    "authority_tier": 4, "confidence": round(inf.confidence, 4),
                },
                "edges": [{
                    "subject": inf.subject, "predicate": "derives",
                    "object": inf.object, "dimension": inf.dimension.value,
                }],
            }
            recorded.append(mem.remember(pair, channel="reasoning"))
            # Knowledge plane: record the inference into Versum as a node/relation
            # chain so it is reachable by query/reason. The signed mutation-log
            # event above stays the audit record; best-effort on the Versum side.
            try:
                from .adapters.versum import append_inference as _append_inference
                store = Path(folder_context).expanduser().resolve() / ".versum"
                store.mkdir(parents=True, exist_ok=True)
                _append_inference(
                    store,
                    path=[{"subject": h["subject"], "predicate": h["predicate"],
                           "object": h["object"]} for h in inf.path],
                    dimension=inf.dimension.value, actor=_default_actor())
            except Exception:
                pass

    return {
        "folder_context": str(Path(folder_context).expanduser().resolve()),
        "knowledge_backend": knowledge_backend,
        "count": len(inferences),
        "recorded": len(recorded),
        "recorded_ids": recorded,
        "inferences": [i.as_dict() for i in inferences],
    }






# ---------------------------------------------------------------------------
# Drag-and-drop ingest — write file, then ingest into the folder's memory.
# ---------------------------------------------------------------------------
#
# Two granular tools so the artifact (and any other caller) can compose them
# however needed:
#
#   - write_file_to_folder(folder_context, filename, content_b64)
#       Writes a file (base64-decoded) to the folder; sanitises the filename;
#       enforces a size cap; returns the resolved path.
#
#   - ingest_path(folder_context, file_path)
#       Runs the default extractor on a single file, writes any resulting
#       pairs into the folder's L0 memory. Idempotent. Returns pair_ids.
#
# The artifact's drag-drop UX calls them in sequence: write → ingest. Both are
# safe to call alone (e.g. ingesting a file the user dropped via Finder).


import base64 as _base64

_MAX_DROP_BYTES = 25 * 1024 * 1024   # 25 MiB cap on a single drop




# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def write_file_to_folder(
    folder_context: str,
    filename: str,
    content_b64: str,
) -> dict[str, Any]:
    """Write a base64-encoded file into a workspace folder.

    Used by the dashboard's drag-and-drop ingest flow: artifact reads the
    dropped file via FileReader, base64-encodes it, calls this tool. The
    file lands on disk in the user's actual folder — it is then picked up
    by ``ingest_path`` (or by a running InboxWatcher).

    Args:
        folder_context: target workspace path. File will be written here.
        filename:       requested filename (basename only — directory parts
                        are stripped and unsafe chars sanitised).
        content_b64:    base64-encoded bytes. 25 MiB cap; larger → error.

    Returns:
        ``{path, bytes, sanitised_filename, written: True}`` on success;
        ``{error}`` on failure.
    """
    try:
        # Decode + size check.
        try:
            raw = _base64.b64decode(content_b64, validate=False)
        except Exception as e:
            return {"error": f"base64 decode failed: {e}"}
        if len(raw) > _MAX_DROP_BYTES:
            return {"error": f"file too large: {len(raw)} bytes "
                             f"(cap {_MAX_DROP_BYTES})"}
        # Sanitise + resolve.
        safe_name = _sanitise_filename(filename)
        folder = Path(folder_context).expanduser().resolve()
        if not folder.exists() or not folder.is_dir():
            return {"error": f"folder not found or not a directory: {folder}"}
        target = folder / safe_name
        # Atomic write via tmp + rename, so the InboxWatcher doesn't race
        # against a partial file. Tmp goes in the same dir to avoid
        # cross-device rename failures.
        tmp = folder / (f".{safe_name}.partial")
        try:
            with open(tmp, "wb") as fp:
                fp.write(raw)
            tmp.replace(target)
        finally:
            if tmp.exists():
                try: tmp.unlink()
                except Exception: pass
        return {
            "path": str(target),
            "bytes": len(raw),
            "sanitised_filename": safe_name,
            "written": True,
        }
    except PermissionError as e:
        return {"error": f"permission denied: {e}"}
    except OSError as e:
        return {"error": f"os error: {e}"}








# ---------------------------------------------------------------------------
# Policy-mutation tools — direct, reliable replacements for ``workspace policy …``
# ---------------------------------------------------------------------------
#
# The dashboard previously asked askClaude to "run this exact bash command",
# which routed through Haiku — and Haiku has no bash access. So toggles never
# actually fired. These tools call the policy module directly and are the
# only correct path for an artifact-side caller.


















# ---------------------------------------------------------------------------
# scan_folder — replacement for ``workspace watch --once`` over MCP
# ---------------------------------------------------------------------------










# Per-folder rate-limit state for lock_classify_text (0.6.7+).
# In-process only — survives MCP server lifetime, not restarts. For
# stronger rate limiting, deploy behind a real gateway.










# ---------------------------------------------------------------------------
# Folder-scoped skill pinning (task #145)
# ---------------------------------------------------------------------------
# A folder may pin a subset of skills it cares about. The orchestrator
# resolves the effective pinning set at orchestration time by walking the
# asymmetric hierarchy UPWARD (folder + ancestors), matching how pairs
# already flow.


























# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# Workspace registry (#134, #154)
# ---------------------------------------------------------------------------


# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def list_known_workspaces() -> dict[str, Any]:
    """Return the persisted list of known workspaces.

    Source of truth for the dashboard's workspace list (replaces localStorage).
    Returns ``{ok, default, workspaces: [{path, label, added_at, exists}, ...]}``.

    Each row carries ``exists`` — whether its folder is present right now — so
    the console can hide workspaces whose folder is gone (deleted temp dirs,
    unmounted drives) without deleting anything from the registry. It is a
    live, non-destructive signal: an unmounted drive's workspace reappears the
    next time it is mounted. The registry itself is never mutated here.

    The registry list is principal-scoped (see
    ``workspace_registry.list_known_workspaces``); the ``default`` pointer
    follows the same scope — it names no workspace the caller cannot see.
    """
    try:
        from .mcp_serving import get_request_principal
        from .registry import load_registry, list_known_workspaces as _list
        data = load_registry(log_root=_log_root())
        rows = _list(log_root=_log_root())
        for w in rows:
            try:
                w["exists"] = bool(w.get("path")) and Path(w["path"]).is_dir()
            except Exception:  # noqa: BLE001 — a bad path is simply "not present"
                w["exists"] = False
        default = data.get("default", "")
        if (get_request_principal() is not None
                and default not in {w.get("path") for w in rows}):
            default = ""
        return {
            "ok":         True,
            "default":    default,
            "workspaces": rows,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}








# ---------------------------------------------------------------------------
# Background-runner queue + lease
# ---------------------------------------------------------------------------




















# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def recent_dispatches(folder_context: str,
                       limit: int = 50,
                       include_workflows: bool = True,
                       scope: str = "self") -> dict[str, Any]:
    """Return recent skill-dispatch and workflow-event entries
    (chronological DESC, newest first).

    Args:
        folder_context: workspace path.
        limit: cap on returned events.
        include_workflows: include workflow-event rows alongside dispatches.
        scope: ``"self"`` (this folder only) or ``"recursive"`` (this folder
               plus descendant workspaces with logs).

    Returns ``{ok, folder_context, scope, events}``.
    """
    try:
        from .workflows import recent_dispatches as _recent
        events = _recent(folder_context,
                          limit=int(limit),
                          include_workflows=bool(include_workflows),
                          scope=scope,
                          log_root=_log_root())
        return {
            "ok":             True,
            "folder_context": str(Path(folder_context).expanduser().resolve()),
            "scope":          scope,
            "events":         events,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}






# ---------------------------------------------------------------------------
# KG-viz layout persistence (2026-05-22)
#
# The Workspace dashboard's force-directed pair graph lets the user drag
# nodes into hand-curated positions. Those positions are persisted to the
# folder's mutation log as ``system`` events so the layout survives a
# session restart. Latest-wins per ``pair_id``.
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# Local-LLM routes (0.6.7+) — route to user-provided local LLM endpoint
# ---------------------------------------------------------------------------
#
# Workspace exposes the route, not the model. Users bring their own
# OpenAI-compatible HTTP endpoint (llama.cpp server / vllm / LM Studio /
# Ollama). Every call goes through capture_llm for the audit floor.
# Configure via env var WORKSPACE_LOCAL_LLM_URL.








# ---------------------------------------------------------------------------
# Lock tools merged from workspace-lock-mcp (0.6.6+)
# ---------------------------------------------------------------------------
#
# Previously these lived in a separate stdio server (workspace-lock-mcp). The
# split was historical (mirrored the package boundary between `workspaces` and
# `rvnd.lock`) rather than user-driven — users wanted all tools from
# one server. In 0.6.6 they're merged into workspaces-mcp. The workspace-lock-mcp
# binary remains as a thin alias for back-compat.








# ---------------------------------------------------------------------------
# Prefixed-name aliases (0.6.6+)
# ---------------------------------------------------------------------------
#
# Option 2 naming convention: every tool's name carries a domain prefix so
# the 60+ tool surface sorts into logical groups in alphabetised UIs.
# Originally-unprefixed tools (search, by_id, recent, folder operations, etc.)
# keep their original names as back-compat aliases; the prefixed name is the
# new canonical. Users on existing 0.6.5 installs see no breakage; users on
# 0.6.6+ should prefer the prefixed names.
#
# When you add or remove an alias, update _DECLARED_TOOLS too.


def pairs_search(folder_context: str,
                  query: str | dict[str, Any],
                  k: int = 5) -> dict[str, Any]:
    """Search pairs across folder + descendants. Asymmetric: siblings out of scope.

    Preferred name (0.6.6+). Alias of ``search``, which remains for back-compat.
    """
    return search(folder_context=folder_context, query=query, k=k)


def pair_by_id(folder_context: str, pair_id: str) -> dict[str, Any]:
    """Fetch a specific pair by id.

    Preferred name (0.6.6+). Alias of ``by_id``, which remains for back-compat.
    """
    return by_id(folder_context=folder_context, pair_id=pair_id)








def folder_write_file(folder_context: str,
                       relative_path: str,
                       content: str,
                       actor: str = "") -> dict[str, Any]:
    """Write a text file into a folder.

    Forwards to ``write_file_to_folder``, whose contract is a base64 payload +
    *basename* filename — so the plain ``content`` is UTF-8 + base64 encoded here.
    ``actor`` is accepted for call-site compatibility; the writer records no actor
    (the file is picked up by ingest_path / the InboxWatcher, which audit
    separately).

    The underlying writer flattens any directory parts to a basename. Rather than
    silently relocate a caller's ``subdir/file.txt`` to ``file.txt`` (data lands
    somewhere unexpected), a ``relative_path`` containing a path separator is
    REFUSED with a clear error — the writer is basename-only by contract.

    Previously forwarded ``relative_path=/content=/actor=`` — none of which are
    parameters of ``write_file_to_folder`` — so every call raised TypeError (N3).
    """
    if "/" in relative_path or "\\" in relative_path:
        return {"error": ("relative_path must be a filename only (no directory "
                          "parts) — the folder writer is basename-only and would "
                          "otherwise silently flatten the path. Pass e.g. "
                          "'note.txt', not 'sub/note.txt'.")}
    content_b64 = _base64.b64encode(content.encode("utf-8")).decode("ascii")
    return write_file_to_folder(folder_context=folder_context,
                                 filename=relative_path,
                                 content_b64=content_b64)










# ---------------------------------------------------------------------------
# Folder mirrors (F1, 0.6.8)
# ---------------------------------------------------------------------------






















# ---------------------------------------------------------------------------
# Erasure (B5, 0.6.8) — first-class GDPR Art. 17 verb
# ---------------------------------------------------------------------------










@mcp.tool()
def cross_workspace_read(folder_context: str,
                    sources: list[str],
                    role: str = "source",
                    autonomy_grade: str = "L2") -> dict[str, Any]:
    """Govern a lateral read from source workspaces into a target workspace.

    ``folder_context`` is the target. Each crossing is ruled by the action gate
    (GO / CONDITIONAL / NO-GO) and, when allowed, recorded on the target's
    signed chain with provenance to the source pairs. ``role`` is "source" (the
    workspace feeds a companion) or "companion" (the companion is applied to the
    workspace) — the two drag directions. Does not run the companion's skill or copy
    raw source content; the Lock governs content, this records references.
    """
    from .cross_workspace import cross_workspace_read as _xc
    return _xc(folder_context, list(sources), role=role,
               autonomy_grade=autonomy_grade)


# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def workspace_cascade(folder_context: str,
                 prompt: str,
                 max_tokens: int = 512,
                 temperature: float = 0.0,
                 capability_token: str = "",
                 track_id: str = "") -> dict[str, Any]:
    """Run the governed local-first cascade for a workspace: try a local tier first,
    escalate to cloud only if needed and only with the Shield approving the
    egress, and record the exchange on the workspace's signed chain. Returns the
    result plus a ledger of tokens the cloud did not spend when served locally.
    With no tier configured it returns a loud error with the exact env to set,
    never a silent no-op. Lets any MCP client (Claude in Cowork, Cline, the app)
    use the workspace's cascade for tasks the workspace is set up for.
    """
    from .cascade_binding import cascade_for_workspace
    return cascade_for_workspace(folder_context, prompt,
                            max_tokens=max_tokens, temperature=temperature,
                            capability_token=capability_token,
                            track_id=track_id)


@mcp.tool()
def workspace_orchestrate(folder_context: str,
                     query: str,
                     autonomy_grade: str = "L2") -> dict[str, Any]:
    """Route a query across the companion workspaces in a workspace tree (folder +
    descendants), gating each dispatch (GO/CONDITIONAL/NO-GO) and recording the
    plan on the root workspace's signed chain. The shared routing core for the app
    sidebar and the /Workspaces chat. Returns the gated dispatch plan; running the
    chosen skills is a separate, gated step.
    """
    from .orchestrate import orchestrate
    return orchestrate(query, folder_context, autonomy_grade=autonomy_grade)


@mcp.tool()
def workspace_ask(folder_context: str,
             query: str,
             max_tokens: int = 512) -> dict[str, Any]:
    """One governed chat turn over a workspace: retrieve its works, generate
    local-first via the cascade (Lock gates any cloud rung), and apply grounding
    only when the turn rests on works (so creators get credit) — then record the
    turn. The identical loop the app sidebar and /Workspaces run. The chat orchestrates
    which governance tools the turn needs; not every turn is grounded.
    """
    from .orchestrate import ask_workspace
    return ask_workspace(query, folder_context, max_tokens=max_tokens)


# 2026-06-12 fold: no longer a registered tool - reachable as a
# facade op (see _DECLARED_TOOLS note); the function stays.
def workspace_shadow_scan(folder_context: str, high_fan_in: int = 3) -> dict[str, Any]:
    """Classify the workspace's recorded cross-workspace crossings into shadow vs declared
    flow. Detective + read-only: reads the signed chain and surfaces emergent
    lateral data flows that no declared workflow covers (shadow), crossings that
    need human sign-off (CONDITIONAL), blocked attempts (NO-GO), and high fan-in.
    It never blocks — the per-crossing gate already did — it makes the shape of
    the flow visible. Does not assert "sanctioned": confirmation is a human act.
    """
    from .shadow_workflow import classify_shadow_workflows
    return classify_shadow_workflows(folder_context, high_fan_in=high_fan_in)


# ---------------------------------------------------------------------------
# Declared tool list (kept hand-maintained)
# ---------------------------------------------------------------------------


_DECLARED_TOOLS = sorted([
    # 2026-06-12 fold (33 -> 23): standalone duplicates left the surface,
    # the capability stayed as facade ops — audit_verify_chain ->
    # workspace_audit(verify_chain), workspace_shadow_scan -> workspace_audit(shadow_scan),
    # list_known_workspaces -> workspace_workspace(list), route_to_workspace ->
    # workspace_workspace(route), write_file_to_folder -> workspace_folder(write_file),
    # search -> workspace_memory(search), by_id -> workspace_memory(pair), reason ->
    # workspace_memory(reason), recent_dispatches -> workspace_dispatch(recent),
    # workspace_cascade -> workspace_model(cascade). test_mcp_facades pins both halves.
    "cross_workspace_read",        # standalone tool — governed cross-workspace read
    "workspace_orchestrate",       # standalone tool — route across companions, gated
    "workspace_ask",               # standalone tool — one governed chat turn
    "workspace_legal",
    "workspace_folder",
    "workspace_mirror",
    "workspace_policy",
    "workspace_workflow",          # 2026-06-02 — facade: workflows + run lifecycle (14 ops)
    "workspace_conformity",        # 2026-06-05 — facade: conformity evidence projections (6 ops, read-only)
    "workspace_lens",              # 2026-06-08 — facade: in-vivo Lens (classify/select_precedent/budget/log)
    "workspace_matrix",            # 2026-06-08 — facade: policy matrix grid (show/set/set_row/set_col/reset/explain)
    "workspace_lock",              # 2026-06-02 — facade: Privacy Lock gate + seal (11 ops)
    "workspace_grounder",          # 2026-06-04 — facade: attribution boundary (13 ops)
    "workspace_memory",            # 2026-06-02 — facade: pair/triple memory (12 ops)
    "workspace_dispatch",
    "workspace_ingest",
    "workspace_contract",
    "workspace_erase",
    "workspace_workspace",
    "workspace_audit",
    "workspace_model",
    "workspace_capture",          # 2026-06-02 — facade: dispatch/resolve/pin (11 ops)            # 2026-06-02 — facade: pair/triple memory (12 ops)              # 2026-06-02 — facade: Privacy Lock gate + seal (9 ops)          # 2026-06-02 — facade: workflows + run lifecycle (14 ops)            # 2026-06-02 — facade: mirror_* (10 ops)            # 2026-06-02 — facade: list/create/scan/reextract/write_file/ingest                # 2026-06-02 — facade: card/facts/select/pipeline/validate (8 ops, 1 tool)
    "workspace_session",          # 2026-07-02 — facade: save/verify/restore/export/import a governance session (S12)
    "server_info",
])












@mcp.tool()
def workspace_session(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """workspace_session facade (S12): save / verify / restore / export / import a
    governance session — the whole environment (all workspaces) as one portable,
    signed .rvnd file. Loads are fail-closed (three integrity checks + referential
    integrity, not overridable); writes are local files (air-gap). The draft_*
    ops persist a workspace's authoring drafts (unsigned working state beside
    the chain). workspace_session(op="help") lists the ops."""
    return session_mcp.workspace_session(op, params)


@mcp.tool()
def server_info(request: dict[str, Any] | None = None) -> dict[str, Any]:
    """Diagnostic surface — returns this server's name, version, and the
    full list of tools it exposes.

    Uses a hand-maintained declaration list (``_DECLARED_TOOLS``) rather
    than introspecting FastMCP's internals: those internals change
    between FastMCP versions, and the previous introspection-based
    implementation silently returned an empty list, which made the
    dashboard's diagnostic banner falsely report every tool as missing.

    If you add or remove an ``@mcp.tool()`` in this file, add/remove
    it from ``_DECLARED_TOOLS`` at the top of this section too.
    """
    if request is not None:
        return {"ok": False, "error": "server_info accepts no request parameters"}

    # Version from package metadata, never a constant: the hardcoded
    # "0.6.6" here survived three releases and made a live server
    # mis-identify against an 0.6.8.1 tree (found 2026-06-12).
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    def _version(distribution: str) -> str:
        try:
            return _pkg_version(distribution)
        except PackageNotFoundError:
            return "source-tree"

    ver = _version("rvnd")
    dependency_versions = {
        "loomground-governance": _version("loomground-governance"),
        "loomground-deontic": _version("loomground-deontic"),
        "loomground-ingest": _version("loomground-ingest"),
        "loomground-solver": _version("loomground-solver"),
        "loomground-versum": _version("loomground-versum"),
    }
    language_runtime: dict[str, Any]
    try:
        from rvnd.adapters.solver.loomground import LANGUAGE_VERSION as solver_language
        from .adapters.versum import versum_language_runtime
        from .loomground_assets import governance_language_version

        direct_language = governance_language_version()
        versum_language = versum_language_runtime()["language_version"]
        language_runtime = {
            "name": "loomground",
            "rvnd": direct_language,
            "solver": solver_language,
            "versum": versum_language,
            "aligned": len({direct_language, solver_language,
                            versum_language}) == 1,
        }
    except Exception as exc:
        language_runtime = {
            "name": "loomground",
            "aligned": False,
            "error": type(exc).__name__,
        }
    try:
        import deontic
        from .ingest import default_registry

        deontic_runtime = {
            "name": "deontic",
            "version": deontic.language_version(),
            "direct": True,
            "ingest": "deontic" in default_registry().ids(),
        }
    except Exception as exc:
        deontic_runtime = {
            "name": "deontic",
            "direct": False,
            "ingest": False,
            "error": type(exc).__name__,
        }
    return {
        # Product/brand layer (Rvnd) over the OS/namespace layer (Workspaces + nD).
        # server_name stays "workspaces": it is the OS identifier the namespace,
        # entrypoints, gateway and tests pin — do not rename it.
        "product": "Loomground Rvnd",
        "os": "Workspaces + nD",
        "tagline": "scaling responsibility in agentic governance",
        "server_name": "workspaces",
        "server_version": ver,
        "dependency_versions": dependency_versions,
        "language_runtime": language_runtime,
        "deontic_runtime": deontic_runtime,
        "tool_count": len(_DECLARED_TOOLS),
        "tools": _DECLARED_TOOLS,
    }


# ---------------------------------------------------------------------------
# Legal workflow facade — one tool, eight operations
# ---------------------------------------------------------------------------


@mcp.tool()
def workspace_legal(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Legal-workflow facade: one tool, many operations (keeps the MCP surface small).

    Rather than eight separate tools, this bundles the legal workflow capabilities
    behind an ``op`` enum. Call ``workspace_legal(op="help")`` for the self-describing
    catalogue of operations and their required params.

    Args:
        op: one of ``card.save`` / ``card.load`` / ``card.list`` / ``facts.form`` /
            ``facts.record`` / ``select.context`` / ``subsumption.validate`` /
            ``pipeline.run_class_c`` (or ``help`` to list them).
        params: the operation's parameters.

    Returns:
        The operation's result dict, or ``{"error": ...}`` for an unknown op or a
        missing required param (never raises across the MCP boundary).
    """
    from .legal_facade import workspace_legal_op, ops_catalogue
    if op in ("help", "catalogue", "ops"):
        return {"ops": ops_catalogue()}
    return workspace_legal_op(op, params or {})


# Declared required params per workspace_folder op — single source of truth for
# both the help catalogue and the pre-dispatch validation gate below (no
# divergent second copy: editing this dict changes both at once).
_WORKSPACE_FOLDER_REQUIRED: dict[str, list[str]] = {
    "list": ["path"],
    "create": ["path"],
    "scan": ["folder_context"],
    "reextract": ["folder_context"],
    "write_file": ["folder_context", "relative_path", "content"],
    "ingest": ["path"],
}


@mcp.tool()
def workspace_folder(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Folder/workspace facade: one tool, six ops (replaces 10 folder tools incl.
    the 0.6.6 back-compat aliases). Call workspace_folder(op="help") for the catalogue.

    op: list | create | scan | reextract | write_file | ingest (or "help").
    """
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        return {"ops": [{"op": k, "required": v} for k, v in _WORKSPACE_FOLDER_REQUIRED.items()]}
    _missing = _require_op_params(_WORKSPACE_FOLDER_REQUIRED, op, p, facade="workspace_folder")
    if _missing is not None:
        return _missing
    try:
        if op == "list":       return folder_list(p["path"])
        if op == "create":     return folder_create(p["path"])
        if op == "scan":       return folder_scan(p["folder_context"])
        if op == "reextract":  return folder_reextract(p["folder_context"])
        if op == "write_file": return folder_write_file(p["folder_context"], p["relative_path"], p["content"], p.get("actor", ""))
        if op == "ingest":     return folder_ingest(p["path"], p.get("folder_context", ""), p.get("actor", ""))
    except KeyError as e:
        return {"ok": False, "error": f"op {op!r} missing param {e}"}
    return {"error": f"unknown op {op!r}", "valid_ops": ["list", "create", "scan", "reextract", "write_file", "ingest"]}


@mcp.tool()
def workspace_mirror(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mirror (human-review redaction editor) facade: one tool, ten ops
    (replaces the mirror_* tools). workspace_mirror(op="help") lists them.

    op: generate | approve | edit | un_redact | history | diff | discard |
        lock_acquire | lock_release | list
    """
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        return {"ops": [
            {"op": "generate", "required": ["folder_context", "source_path"]},
            {"op": "approve", "required": ["folder_context", "mirror_path", "approver"]},
            {"op": "edit", "required": ["folder_context", "mirror_path", "span_id", "operation"]},
            {"op": "un_redact", "required": ["folder_context", "mirror_path", "span_id", "controller_key"]},
            {"op": "history", "required": ["folder_context", "mirror_path"]},
            {"op": "diff", "required": ["folder_context", "mirror_path", "from_rev"]},
            {"op": "discard", "required": ["folder_context", "mirror_path"]},
            {"op": "lock_acquire", "required": ["folder_context", "mirror_path", "actor"]},
            {"op": "lock_release", "required": ["folder_context", "mirror_path"]},
            {"op": "list", "required": ["folder_context"]},
        ]}
    try:
        if op == "generate":     return mirror_generate(p["folder_context"], p["source_path"], p.get("actor", ""))
        if op == "approve":      return mirror_approve(p["folder_context"], p["mirror_path"], p["approver"])
        if op == "edit":         return mirror_edit(p["folder_context"], p["mirror_path"], p["span_id"], p["operation"], actor=p.get("actor", "system:editor"), reason=p.get("reason", ""), **(p.get("kwargs") or {}))
        if op == "un_redact":    return mirror_un_redact(p["folder_context"], p["mirror_path"], p["span_id"], p["controller_key"], actor=p.get("actor", "system:editor"), reason=p.get("reason", ""), original_text=p.get("original_text", ""), recheck=p.get("recheck", True))
        if op == "history":      return mirror_history(p["folder_context"], p["mirror_path"])
        if op == "diff":         return mirror_diff(p["folder_context"], p["mirror_path"], p["from_rev"], p.get("to_rev"))
        if op == "discard":      return mirror_discard(p["folder_context"], p["mirror_path"], p.get("actor", "system:editor"), p.get("reason", ""))
        if op == "lock_acquire": return mirror_lock_acquire(p["folder_context"], p["mirror_path"], p["actor"], p.get("ttl_seconds", 900))
        if op == "lock_release": return mirror_lock_release(p["folder_context"], p["mirror_path"], p.get("actor", ""))
        if op == "list":         return mirror_list(p["folder_context"], p.get("kind", ""))
    except KeyError as e:
        return {"error": f"op {op!r} missing param {e}"}
    return {"error": f"unknown op {op!r}"}


def _require_op_params(required_map: dict[str, list[str]], op: str,
                       params: dict[str, Any], facade: str = "") -> dict[str, Any] | None:
    """Pre-dispatch MCP input validation: reject a KNOWN op that is missing a
    declared-required param before the handler body runs (no partial execution),
    returning a clean, declared error instead of relying on a KeyError raised
    mid-handler (or, worse, a silent no-op). Unknown ops fall through to the
    facade's own unknown-op handling.

    All three ``(op, params)`` facades share FastMCP's tool signature
    ``(op: str, params: dict|None = None)``: required fields like ``path`` /
    ``folder_context`` must be nested INSIDE ``params``. A caller that passes
    them top-level has them dropped as extra fields before the handler runs —
    so this also names the facade and shows a concrete correctly-nested
    example, not just "missing param", to steer the caller to the fix.
    ``facade`` is optional (defaults to "") so existing positional callers
    (``_require_op_params(required_map, op, params)``) keep working unchanged.
    """
    if not isinstance(op, str):
        # An unhashable (dict/list) or non-string op would raise on the
        # required_map.get(op) lookup below. Treat any non-string op as
        # unknown — the caller sent garbage; fall through to the facade's
        # own unknown-op handling rather than crashing the validator.
        return None
    req = required_map.get(op)
    if req is None:
        return None
    try:
        missing = [k for k in req if params.get(k) in (None, "")]
    except AttributeError:
        # params itself is not a dict (e.g. a bare string/list sent where an
        # object was expected) — every declared-required key is, by
        # definition, missing.
        missing = list(req)
    if not missing:
        return None
    name = facade or "this tool"
    example = ", ".join(f'"{k}": ...' for k in req)
    hint = (f"parameters must be nested inside `params`, not passed top-level "
            f"(FastMCP drops unrecognised top-level fields silently) — e.g. "
            f'{name}(op={op!r}, params={{{example}}})')
    return {"ok": False,
            "error": f"op {op!r} missing required param(s): {', '.join(missing)} — {hint}",
            "op": op, "facade": facade, "missing": missing, "required": req}


def _facade_required_from_table(table: dict) -> dict[str, list[str]]:
    """Op->required-param map for an ``_op_call`` dispatch table, sourced from
    ``_op_call``'s OWN introspection (its ``help`` branch) rather than a
    hand-maintained copy — so it can never drift from the real function
    signatures ``_op_call`` will call. Used to pre-validate before dispatch
    for facades (e.g. ``workspace_workspace``) that hand their op table
    straight to ``_op_call`` instead of declaring their own help catalogue."""
    return {row["op"]: row["required"] for row in _op_call("help", table, {}).get("ops", [])}


# Declared required params per workspace_policy op — the enforced input schema. Mirrors
# the help catalogue's "required" lists; test_mcp_input_schema guards against drift.
_WORKSPACE_POLICY_REQUIRED: dict[str, list[str]] = {
    "snapshot": ["folder_context"],
    "enable": ["folder_context", "dial"],
    "disable": ["folder_context", "dial", "accepted_by", "reason"],   # disabling a protection requires attribution + a recorded reason (no silent disable over MCP)
    "set_lock_mode": ["folder_context", "mode"],
    "set_oversight_level": ["folder_context", "level"],
    "set_access_control": ["folder_context", "enabled"],
    "delegate_signing": ["folder_context", "from_party", "to_party"],
    "tdm_optout": ["folder_context"],
    "tdm_declare": ["folder_context"],
    "juris_packs": ["folder_context"],
    "party_register": ["folder_context", "party_id", "kind"],
    "party_status": ["folder_context", "party_id", "status"],
    "party_list": ["folder_context"],
    "party_route": ["folder_context", "competence"],
    "actor_stamps": ["folder_context"],
}


@mcp.tool()
def workspace_policy(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Policy facade: one tool for the per-folder dials (replaces 8 policy_* tools,
    collapsing the enable/disable toggle explosion). workspace_policy(op="help") lists ops.

    op: snapshot | enable | disable | set_lock_mode
    dial (for enable/disable): lock | oversight | discipline
    Disabling lock/oversight takes accepted_by + reason (disclaimer acknowledgement).
    """
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        return {"ops": [
            {"op": "snapshot", "required": ["folder_context"]},
            {"op": "enable", "required": ["folder_context", "dial"], "dials": ["lock", "oversight", "discipline"]},
            {"op": "disable", "required": ["folder_context", "dial", "accepted_by", "reason"], "note": "disabling a protection requires attribution + a recorded reason (no silent disable)"},
            {"op": "set_lock_mode", "required": ["folder_context", "mode"]},
            {"op": "set_oversight_level", "required": ["folder_context", "level"],
             "note": "the live oversight row (autonomous..manual); set from the matrix"},
            {"op": "set_access_control", "required": ["folder_context", "enabled"],
             "optional": ["actor"],
             "note": "opt this workspace in/out of access control (#58); ON makes "
                     "sign-off require a registered authorised party, fail-closed"},
            {"op": "delegate_signing", "required": ["folder_context", "from_party", "to_party"],
             "optional": ["actor", "now"],
             "note": "grant signing authority from one active human to another (#58); "
                     "the delegate may then record sign-offs on the signer's behalf"},
            {"op": "tdm_optout", "required": ["folder_context"],
             "note": "assert/withdraw the AI-training opt-out; enabled defaults true"},
            {"op": "tdm_declare", "required": ["folder_context"],
             "note": "write the machine-readable reservation file + assert + audit"},
            {"op": "juris_packs", "required": ["folder_context"],
             "optional": ["packs", "as_of"],
             "note": "with 'packs': declare this folder's jurisdiction-pack "
                     "stack (audited); without: show own + resolved stack "
                     "(ancestors cascade, descendants only add)"},
            {"op": "party_register", "required": ["folder_context", "party_id", "kind"],
             "optional": ["name", "role", "competences", "channels", "owner",
                          "purpose", "grade", "agent_uid", "actor"],
             "note": "register/update a human or agent on the chain (1.5)"},
            {"op": "party_status", "required": ["folder_context", "party_id", "status"],
             "optional": ["reason", "actor"],
             "note": "active | suspended | killed - killed is the kill switch"},
            {"op": "party_list", "required": ["folder_context"],
             "optional": ["kind", "competence"]},
            {"op": "party_route", "required": ["folder_context", "competence"],
             "note": "active humans matching the competence (approver routing)"},
            {"op": "actor_stamps", "required": ["folder_context"],
             "note": "classify chain actors: registered / builtin / unknown"},
        ]}
    # MCP input schema: reject a known op missing a declared param up front.
    _missing = _require_op_params(_WORKSPACE_POLICY_REQUIRED, op, p, facade="workspace_policy")
    if _missing is not None:
        return _missing
    try:
        fc = p["folder_context"]
        if op == "snapshot":
            return policy_snapshot(fc)
        if op == "set_lock_mode":
            return policy_set_lock_mode(fc, p["mode"], p.get("accepted_by", ""), p.get("reason", ""))
        if op == "set_oversight_level":
            from .policy import set_oversight_level as _sol
            pol = _sol(fc, p["level"], actor=p.get("actor", "user"))
            return {"ok": True, "folder": fc,
                    "oversight_default_level": pol.oversight_default_level}
        if op == "set_access_control":
            from .policy import set_access_control as _sac
            pol = _sac(fc, bool(p["enabled"]), actor=p.get("actor", "user"))
            return {"ok": True, "folder": fc,
                    "access_control_enabled": pol.access_control_enabled}
        if op == "delegate_signing":
            from .approvals import delegate_signing as _dsg
            import time as _t
            try:
                return _dsg(fc, from_party=p["from_party"], to_party=p["to_party"],
                            actor=p.get("actor", "user"), now=float(p.get("now", _t.time())),
                            log_root=_log_root())
            except ValueError as e:
                return {"error": str(e)}
        if op == "tdm_optout":
            from .policy import set_ai_training_optout as _sto
            pol = _sto(fc, bool(p.get("enabled", True)),
                       actor=p.get("actor", "user"))
            return {"ok": True, "folder": fc,
                    "ai_training_optout": pol.ai_training_optout}
        if op == "tdm_declare":
            from .policy import tdm_declare as _td
            return _td(fc, actor=p.get("actor", "user"))
        if op in ("party_register", "party_status", "party_list",
                  "party_route", "actor_stamps"):
            from . import parties as _pt
            lr = _log_root()
            try:
                return _party_op(op, fc, p, _pt, lr)
            except ValueError as e:
                return {"error": str(e)}
        if op == "juris_packs":
            from .juris_packs import (active_packs, resolve_folder_packs,
                                      set_folder_packs)
            if "packs" in p:
                return set_folder_packs(fc, list(p["packs"]),
                                        actor=p.get("actor", "user"))
            resolved = active_packs(resolve_folder_packs(fc),
                                    as_of=p.get("as_of", ""))
            from .policy import load_policy as _lp
            return {"ok": True, "folder": fc,
                    "own": list(_lp(fc).juris_packs),
                    "resolved": resolved}
        if op == "enable":
            dial = p["dial"]
            if dial == "lock":       return policy_enable_lock(fc)
            if dial == "oversight":  return policy_enable_oversight(fc)
            if dial == "discipline": return policy_enable_discipline(fc, p.get("manifest", ""))
            return {"error": f"unknown dial {dial!r}", "dials": ["lock", "oversight", "discipline"]}
        if op == "disable":
            dial = p["dial"]
            if dial == "lock":       return policy_disable_lock(fc, p.get("accepted_by", ""), p.get("reason", ""))
            if dial == "oversight":  return policy_disable_oversight(fc, p.get("accepted_by", ""), p.get("reason", ""))
            if dial == "discipline": return policy_disable_discipline(fc)
            return {"error": f"unknown dial {dial!r}", "dials": ["lock", "oversight", "discipline"]}
    except KeyError as e:
        # Safety net only: every known op's required params are already
        # rejected up front by _require_op_params above, so this should be
        # unreachable for a declared op — kept in case a new op is added
        # here without a matching _WORKSPACE_POLICY_REQUIRED entry.
        return {"ok": False, "error": f"op {op!r} missing param {e}"}
    return {"error": f"unknown op {op!r}", "valid_ops": ["snapshot", "enable", "disable", "set_lock_mode", "set_oversight_level"]}


def _party_op(op, fc, p, _pt, lr):
    """workspace_policy's party dispatch (1.5 registry ops on the policy facade)."""
    if op == "party_register":
        return _pt.register_party(
            fc, p["party_id"], p["kind"], name=p.get("name", ""),
            role=p.get("role", ""), competences=p.get("competences"),
            channels=p.get("channels"), owner=p.get("owner", ""),
            purpose=p.get("purpose", ""), grade=p.get("grade", ""),
            agent_uid=p.get("agent_uid", ""),
            actor=p.get("actor", "user"), log_root=lr)
    if op == "party_status":
        return _pt.set_party_status(
            fc, p["party_id"], p["status"],
            reason=p.get("reason", ""), actor=p.get("actor", "user"),
            log_root=lr)
    if op == "party_list":
        return _pt.list_parties(fc, kind=p.get("kind", ""),
                                competence=p.get("competence", ""),
                                log_root=lr)
    if op == "party_route":
        return _pt.route_approvers(fc, p["competence"], log_root=lr)
    return _pt.actor_stamp_report(fc, log_root=lr)


@mcp.tool()
def workspace_conformity(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Conformity facade: one read-only tool — evidence as API (C1). Six projections over the signed log, each keyed to the
    standard clause it evidences. Produces the evidence the articles require;
    never claims "compliant". workspace_conformity(op="help") lists ops.

    op: evidence_pack | oversight_attestation | trigger_map | drift_report |
        risk_register | threat_model
    """
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        return {"ops": [
            {"op": "evidence_pack", "required": ["folder_context"],
             "optional": ["since", "until"], "hook": "Art. 12; prEN ISO/IEC 24970"},
            {"op": "oversight_attestation", "required": ["folder_context"],
             "optional": ["since", "until"], "hook": "Art. 14; action-level oversight record"},
            {"op": "trigger_map", "required": ["folder_context"],
             "hook": "external-action inventory → activated instruments"},
            {"op": "drift_report", "required": ["folder_context"],
             "optional": ["catalogue_fingerprint", "as_of"],
             "hook": "Art. 3(23)/72; prEN 18286 cl. 9.4"},
            {"op": "risk_register", "required": ["folder_context"],
             "optional": ["posture"], "hook": "Art. 9; prEN 18228"},
            {"op": "threat_model", "required": [],
             "hook": "Art. 15(4); prEN 18282; OWASP agentic taxonomy"},
        ], "note": "all ops are read-only projections of the signed mutation log. "
                   "Jurisdiction-neutral by default (no statute cited); pass "
                   "params.regime='eu-ai-act' to attach the EU reference regime's "
                   "legal labels (instruments, article bases). Other regimes are "
                   "pack data under data/packs/<id>-conformity.json."}
    from . import conformity as _conf
    try:
        lr = _log_root()
        # Jurisdiction neutrality: no regime by default (engine output carries
        # no statute). A caller opts into a legal regime by name; "eu-ai-act"
        # loads the shipped reference pack. Unknown name → neutral.
        reg = None
        rid = p.get("regime")
        if rid == "eu-ai-act":
            reg = _conf.load_regime()
        elif isinstance(rid, str) and rid:
            cand = _conf.REGIME_PACKS_DIR / f"{rid}-conformity.json"
            reg = _conf.load_regime(cand) if cand.exists() else None
        if op == "evidence_pack":
            return _conf.evidence_pack(p["folder_context"], log_root=lr,
                                       since=p.get("since"), until=p.get("until"), regime=reg)
        if op == "oversight_attestation":
            return _conf.oversight_attestation(p["folder_context"], log_root=lr,
                                               since=p.get("since"), until=p.get("until"), regime=reg)
        if op == "trigger_map":
            return _conf.trigger_map(p["folder_context"], log_root=lr, regime=reg)
        if op == "drift_report":
            return _conf.drift_report(p["folder_context"], log_root=lr,
                                      catalogue_fingerprint=p.get("catalogue_fingerprint", ""),
                                      as_of=p.get("as_of"), regime=reg)
        if op == "risk_register":
            return _conf.risk_register(p["folder_context"], log_root=lr,
                                       posture=p.get("posture", "balanced"), regime=reg)
        if op == "threat_model":
            return _conf.threat_model(tests_dir=p.get("tests_dir"), regime=reg)
        if op == "authorship_evidence":
            # § 1.7 stem provenance: per-work production-process evidence
            # (read-only chain projection, like every op on this facade).
            # Explicit log_root override accepted: harmless for a read-only
            # projection, required for hermetic tests.
            from .stem_provenance import authorship_evidence as _ae
            return _ae(p["folder_context"], p["work_id"],
                       log_root=p.get("log_root", lr))
    except KeyError as e:
        return {"error": f"op {op!r} missing param {e}"}
    return {"error": f"unknown op {op!r}", "valid_ops": list(_conf.OPS) + ["authorship_evidence"]}


@mcp.tool()
def workspace_lens(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """In-vivo Lens facade (USP-2): govern what an agent LEARNS, not just what it
    does. Guard, not teacher — it admits / holds / rejects (default-deny),
    transports human judgment as revocable precedent, and bounds learning by an
    update budget. workspace_lens(op="help") lists ops.

    op: classify | select_precedent | budget | log | precedent_declare |
        precedent_revoke | precedent_list | budget_cap_get | budget_cap_set
    """
    from . import lens_service as _ls
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        return {"ops": [
            {"op": "classify", "required": ["cls", "content_hash"],
             "optional": ["scope", "source_actor", "signature", "confidence",
                          "confidence_floor", "known_teachers", "magnitude",
                          "record", "folder_context", "actor"],
             "hook": "default-deny admit/hold/reject; GDPR purpose-limitation / "
                     "AI Act Art. 10 data governance compile into scope"},
            {"op": "select_precedent", "required": ["candidates"],
             "optional": ["features", "now"],
             "hook": "stare decisis for agents — revocable, TTL'd, threshold (§4.3)"},
            {"op": "precedent_declare", "required": ["folder_context", "id"],
             "optional": ["query_features", "chosen_option", "rationale", "actor",
                          "learnable", "similarity_threshold", "expires_at"],
             "hook": "declare a human origination learnable — written to the signed chain"},
            {"op": "precedent_revoke", "required": ["folder_context", "id"],
             "optional": ["actor", "reason"], "hook": "revoke a precedent (recorded)"},
            {"op": "precedent_list", "required": ["folder_context"],
             "optional": ["include_inactive", "now"],
             "hook": "the live precedent shelf, replayed from the signed chain"},
            {"op": "budget", "required": [], "optional": ["cap", "admitted", "folder_context"],
             "hook": "learning = deliberate drift; over cap forces re-gate (Art. 3(23))"},
            {"op": "budget_cap_get", "required": ["folder_context"],
             "hook": "the per-folder update-budget cap (or null)"},
            {"op": "budget_cap_set", "required": ["folder_context", "cap"],
             "hook": "set the per-folder update-budget cap (> 0)"},
            {"op": "log", "required": ["folder_context"], "optional": ["limit"],
             "hook": "admission feed (+ spent/cap) behind the queue + budget meter"},
        ], "note": "Workspace is a guard, not a teacher: it decides what is allowed to "
                   "stick, it computes no gradients. Forbidden classes "
                   "(protected-attribute, escalated-residual, lock-refused, "
                   "special-category-data) never auto-admit, whatever the scope says."}
    lr = _log_root()
    try:
        if op == "classify":          return _ls.classify(p, log_root=lr)
        if op == "select_precedent":  return _ls.select(p)
        if op == "precedent_declare": return _ls.precedent_declare(p, log_root=lr)
        if op == "precedent_revoke":  return _ls.precedent_revoke(p, log_root=lr)
        if op == "precedent_list":
            return _ls.precedent_list(p["folder_context"], log_root=lr,
                                      include_inactive=bool(p.get("include_inactive")),
                                      now=p.get("now"))
        if op == "budget":            return _ls.budget(p, log_root=lr)
        if op == "budget_cap_get":
            return {"folder": p["folder_context"],
                    "cap": _ls.budget_cap_get(p["folder_context"], log_root=lr)}
        if op == "budget_cap_set":
            return _ls.budget_cap_set(p["folder_context"], float(p["cap"]), log_root=lr)
        if op == "log":
            return _ls.admission_log(p["folder_context"], log_root=lr,
                                     limit=int(p.get("limit", 50)))
    except KeyError as e:
        return {"error": f"op {op!r} missing param {e}"}
    return {"error": f"unknown op {op!r}", "valid_ops": list(_ls.OPS)}


@mcp.tool()
def workspace_matrix(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Policy matrix facade: the autonomy-grade × oversight-level traffic-light
    grid a folder's steps inherit. POLICY layer — it *declares* what is allowed;
    the gate/runner/Lens follow it. workspace_matrix(op="help") lists ops.

    op: show | set | set_row | set_col | reset | explain
    """
    from . import policy_matrix as _pm
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        return {"ops": [
            {"op": "show", "required": ["folder_context"],
             "hook": "the grid {grade:{oversight:light}} + axes"},
            {"op": "set", "required": ["folder_context", "grade", "oversight", "light"],
             "hook": "one cell (go|ask|block) — paint can only tighten at runtime"},
            {"op": "set_row", "required": ["folder_context", "oversight", "light"],
             "hook": "bulk: a whole oversight row"},
            {"op": "set_col", "required": ["folder_context", "grade", "light"],
             "hook": "bulk: a whole grade column"},
            {"op": "set_all", "required": ["folder_context", "matrix"],
             "hook": "save a full grid as this workspace's override in one call"},
            {"op": "reset", "required": ["folder_context"],
             "hook": "drop this workspace's override → inherit global/ancestor again"},
            {"op": "explain", "required": ["folder_context", "grade", "oversight"],
             "optional": ["privacy", "verdict", "forms", "footprint", "as_of"],
             "hook": "effective light + WHY + control form (1.5 algebra; "
                     "'forms' composes required forms strictest-wins; "
                     "'footprint' pulls the folder's jurisdiction-pack stack)"},
        ], "grades": list(_pm.GRADES), "oversight": list(_pm.OVERSIGHT),
           "lights": list(_pm.LIGHTS),
           "note": "POLICY declares; OVERSIGHT WORKFLOWS follow. A painted cell can "
                   "only tighten the gate verdict + privacy floor, never loosen them."}
    lr = _log_root()
    try:
        fc = p["folder_context"]
        if op == "show":
            own = _pm.own_matrix(fc, log_root=lr)
            return {"folder": fc,
                    "matrix": _pm.resolve_matrix(fc, log_root=lr),        # EFFECTIVE
                    "own": own,                                           # None = inherits
                    "inherits": own is None,
                    "inherited": _pm.resolve_inherited(fc, log_root=lr),  # from root/ancestors
                    "grades": list(_pm.GRADES), "oversight": list(_pm.OVERSIGHT),
                    "lights": list(_pm.LIGHTS)}
        if op == "set_all":        # persist a whole grid as this workspace's override
            saved = _pm.save_own_matrix(fc, _pm._backfill(p["matrix"]),
                                        actor=p.get("actor", "user"), log_root=lr)
            return {"ok": True, "inherits": False, "matrix": saved}
        if op == "reset":          # drop this workspace's override → inherit again
            _pm.clear_own_matrix(fc, actor=p.get("actor", "user"), log_root=lr)
            return {"ok": True, "inherits": True,
                    "matrix": _pm.resolve_matrix(fc, log_root=lr)}
        if op == "explain":
            forms = list(p.get("forms") or ())
            if p.get("footprint"):
                from .juris_packs import folder_required_forms
                forms += folder_required_forms(fc, p["footprint"],
                                               as_of=p.get("as_of", ""))
            return _pm.effective_control_form(
                _pm.resolve_matrix(fc, log_root=lr),
                grade=p["grade"], oversight=p["oversight"],
                privacy_class=p.get("privacy"),
                gate_verdict=p.get("verdict"),
                required_forms=forms)
        if op in ("set", "set_row", "set_col"):
            # start from the current effective grid so the override captures the
            # full intended policy (inherited cells + the change)
            m = _pm.resolve_matrix(fc, log_root=lr)
            if op == "set":
                _pm.set_cell(m, p["grade"], p["oversight"], p["light"])
            elif op == "set_row":
                _pm.set_row(m, p["oversight"], p["light"])
            else:
                _pm.set_col(m, p["grade"], p["light"])
            _pm.save_own_matrix(fc, m, actor=p.get("actor", "user"), log_root=lr)
            return {"ok": True, "inherits": False, "matrix": m}
    except KeyError as e:
        return {"error": f"op {op!r} missing param {e}"}
    except ValueError as e:
        return {"error": str(e)}
    return {"error": f"unknown op {op!r}",
            "valid_ops": ["show", "set", "set_row", "set_col", "reset", "explain"]}


_STATUTE_RE = re.compile(
    r"(?:"
    r"(?:GDPR|DSGVO|AI Act|AIA|BetrVG|CCPA|HIPAA|DSA|Data Act)"
    r"(?:[^.;:\n]{0,40}?(?:Art(?:icle|\.)?\s*\d+\w*|§\s*\d+[\w()]*))?"
    r"|(?:Art(?:icle|\.)?\s*\d+\w*|§\s*\d+[\w()]*)[^.;:\n]{0,20}?(?:GDPR|AI Act|BetrVG|Data Act)"
    r"|Reg(?:ulation)?\.?\s*\(?(?:EU)?\)?\s*\d{4}/\d+"
    r")", re.I)


def _read_policy_file(path: str, folder_context: str) -> tuple[str, Optional[str], Optional[str]]:
    """Read + extract text from a file to ingest as a policy (txt/pdf/docx via
    format_extractors), then route it through the genre router (detect genre, drop the genre's
    non-normative preamble, line-clean). SANDBOXED: the resolved file must live inside the
    workspace folder — a governance tool does not read arbitrary paths off the machine.
    Returns (text, genre, error)."""
    from pathlib import Path
    from . import format_extractors as _fx
    if not folder_context:
        return "", None, "no folder_context for sandbox"
    try:
        base = Path(folder_context).resolve()
        fp = Path(path)
        fp = (base / fp if not fp.is_absolute() else fp).resolve()
    except Exception as e:
        return "", None, f"bad path: {type(e).__name__}: {e}"
    try:
        fp.relative_to(base)                      # reject ../ escape / outside-workspace paths
    except ValueError:
        return "", None, f"path is outside the workspace folder: {fp}"
    if not fp.is_file():
        return "", None, f"not a file: {fp}"
    res = _fx._extract_text(fp)
    if res.error and not res.text:
        return "", None, res.error
    if not res.text.strip():
        return "", None, f"no extractable text in {fp.name} (scanned/encrypted?)"
    # GENRE ROUTER: detect the document genre, drop its non-normative preamble (recitals /
    # foreword), and line-clean — so extraction sees the normative body, not PDF fragments.
    from .adapters.ingest.governance import genre_router as _gr
    genre, cleaned = _gr.ingest_prepare(res.text)
    return cleaned, genre, None


def _attach_statute_sources(twin: dict[str, Any], policy_text: str) -> dict[str, Any]:
    """RVND-LAYER enrichment (not Loomground): if the ingested policy NAMES a statute,
    attach it to the matching reservation as its ``source`` — attributed to the user's
    policy, never asserted by Rvnd. policy_ingest (Loomground) extracts the governance
    STRUCTURE; this jurisdiction-specific citation parsing stays in Rvnd. A reservation
    is linked to a statute by a role/kind word shared with the naming sentence; if
    nothing names a statute, the source is left unset (authored = nothing cited)."""
    if not isinstance(twin, dict) or not policy_text:
        return twin
    reservations = (twin.get("patch") or {}).get("reservations") or []
    if not reservations:
        return twin
    # Each sentence → the DISTINCT statutes it names. Normalize hyphens to spaces so a
    # role/kind like "data-protection-officer" matches "data protection officer" in the
    # text (loop fix B1/B2). Capture all statutes per sentence, not just the first (B4).
    sent_statutes: list[tuple[str, list[str]]] = []
    for sent in re.split(r"(?<=[.;\n])\s+", policy_text):
        statutes: list[str] = []
        for m in _STATUTE_RE.finditer(sent):
            s = m.group(0).strip(" ,.")
            if s and s not in statutes:
                statutes.append(s)
        if statutes:
            sent_statutes.append((sent.replace("-", " ").lower(), statutes))
    if not sent_statutes:
        return twin
    for r in reservations:
        if not isinstance(r, dict) or r.get("source"):
            continue
        by = (r.get("by") or "").replace("-", " ").lower()
        kind_words = [w for w in (r.get("kind") or "").replace("-", " ").replace("_", " ").split()
                      if len(w) > 3]
        hits = [statutes for sent, statutes in sent_statutes
                if (by and by in sent) or any(w in sent for w in kind_words)]
        # Attach only when unambiguous: exactly one matched sentence naming exactly one
        # statute. Otherwise leave the source unset — UNDER-attribute, never misattribute
        # (loop fix B3/B4). Attributed to the policy, never guessed by Rvnd.
        if len(hits) == 1 and len(hits[0]) == 1:
            r["source"] = hits[0][0]
    return twin


def _loom_apply(folder_context: str, patch: dict[str, Any], *, actor: str,
                log_root: Optional[str] = None) -> dict[str, Any]:
    """Write a validated Loomground v0.5 patch to the signed chain via the
    bijection: actor->party kind=agent, human->party kind=human,
    gate->use_case, authority cord + grant -> the gate's allowed_agents,
    gate risk_floor -> use_case risk. Caller has already validated (fail-closed).
    Returns the resulting governance_graph (text -> chain -> graph round-trip)."""
    if not (actor or "").strip():
        return {"ok": False, "errors": ["apply needs a named actor"]}
    from .parties import register_party
    from .use_case import register_use_case, get_use_case
    from .governance_graph import governance_graph

    # Structural graph writes cover parties/use_cases/authority. Governance
    # DECLARATIONS are written to the chain too: reserve + prohibit (in the gate
    # loop below) and obligation + redress (mapped just below). `pending` therefore
    # stays empty — nothing is deferred; a declaration is either written, or
    # surfaced as a 'not applied' warning when its gate isn't in the patch (no
    # silent drop; doctrine). Grant narrowing/delegation remain projection-only.
    pending: dict[str, Any] = {}
    warnings: list[str] = []
    # Obligations + redress are now WRITTEN to the chain (declared duties / remedies
    # that ride with the gate). Map them to the gate they attach to, so they ride
    # into register_use_case(obligations=, redress=) like reservations do.
    # redress mirrors reservations grammatically (`redress <kind> by <role>` ==
    # `reserve <kind> by <role>`): `kind` is the gate kind, so both key by it.
    # isinstance guards: a hand-built patch could carry a non-dict item — skip it
    # rather than crash (the netlist parser only ever emits dicts).
    oblig_by_gate: dict[str, list[dict[str, Any]]] = {}
    for o in patch.get("obligations", []):
        g = o.get("on") if isinstance(o, dict) else None
        if g:
            oblig_by_gate.setdefault(g, []).append({"obligation": o.get("obligation", ""), "on": g})
    redress_by_gate: dict[str, list[dict[str, Any]]] = {}
    for r in patch.get("redress", []):
        g = r.get("kind") if isinstance(r, dict) else None
        if g:
            redress_by_gate.setdefault(g, []).append(dict(r))
    # gate kind -> the company reservation owed there (twin: reserve <kind> by <role>).
    # Keyed by the gate kind so it rides into register_use_case(policy_reservations=).
    policy_res: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for r in patch.get("reservations", []):
        kind, by = r.get("kind"), r.get("by")
        if kind:
            # Append, never overwrite: two policy sentences reserving the same gate
            # by different roles both owe their act (no silent drop).
            # G3: carry the reservation's OWN source if it has one (a statute the
            # ingested policy NAMED, attached by _attach_statute_sources — attributed
            # to the user's policy, not asserted by Rvnd). A reserve authored directly
            # in the patch cites nothing, so it says so plainly (no fake "by law").
            _entry = {
                "reserved_to": by or "designated-approver",
                "act_type": "review",
                "source": r.get("source") or "authored in this patch (no source cited)"}
            # Option 2: carry the reservation's `when` GUARD onto the chain so the
            # run-path can honour it (a conditional reserve only reserves when the
            # guard holds — incl. `tags contains`). Was dropped here before.
            if r.get("when"):
                _entry["when"] = r["when"]
            policy_res.setdefault(kind, {}).setdefault(kind, []).append(_entry)
    prohibited_kinds = {pr.get("kind") for pr in patch.get("prohibitions", []) if pr.get("kind")}
    preserved_reservations: list[str] = []   # gates whose chain reservation we carried forward

    nodes = patch.get("nodes", [])
    by_id = {n["id"]: n for n in nodes}
    allow: dict[str, set] = {}
    for g in patch.get("grants", []):
        allow.setdefault(g["gate"], set()).add(g["actor"])
    for c in patch.get("cords", []):
        src, dst = c.get("from"), c.get("to")
        if (dst != "master" and by_id.get(src, {}).get("class") == "actor"
                and by_id.get(dst, {}).get("class") == "gate"):
            allow.setdefault(dst, set()).add(src)

    n_actor = n_human = n_gate = 0
    for n in nodes:
        cls = n["class"]
        if cls == "actor":
            register_party(folder_context, n["id"], "agent",
                           name=n.get("name", ""), actor=actor, log_root=log_root)
            n_actor += 1
        elif cls == "human":
            register_party(folder_context, n["id"], "human",
                           name=n.get("name", ""), role=n.get("role", ""),
                           actor=actor, log_root=log_root)
            n_human += 1
    for n in nodes:
        if n["class"] != "gate":
            continue
        # Map the netlist gate id back to the bare use_case_id. governance_graph
        # bares the "uc:" namespace prefix, but KEEPS it on an id collision
        # (a party and a use case sharing a bare id) — so a colliding gate
        # arrives as "uc:foo". Strip it, else get_use_case("uc:foo") misses the
        # stored "foo" record, the gate looks new, and the reservation is dropped.
        gid = n["id"]
        uc_id = gid[3:] if gid.startswith("uc:") else gid
        desired_agents = sorted(allow.get(gid, set()))
        desired_risk = n.get("risk_floor", "low")
        desired_name = n.get("name") or uc_id
        # A gate carrying a reservation/prohibition is a governed act. Apply its
        # company reservation + prohibition flag, and (downgrade doctrine) floor its
        # risk to at least medium so it never lands at the loosest L4 ceiling.
        pr = policy_res.get(uc_id)
        is_prohibited = uc_id in prohibited_kinds
        new_oblig = oblig_by_gate.get(uc_id) or oblig_by_gate.get(gid) or []
        new_redress = redress_by_gate.get(uc_id) or redress_by_gate.get(gid) or []
        if (pr or is_prohibited) and desired_risk == "low":
            desired_risk = "medium"
            warnings.append(f"{uc_id}: risk floored to medium (carries a reservation/prohibition)")
        # A netlist round-trip must never silently drop a chain reservation
        # or reset the earned grade. The netlist carries no fingerprint, so
        # re-registering with fingerprint={} would blank reserved_acts
        # (latest-wins) and widen a reserved-by-law task to auto-eligible. Carry
        # the existing fingerprint and the contract inputs (earned grade) forward;
        # and skip the re-register entirely on a true no-op.
        existing = get_use_case(folder_context, uc_id, log_root=log_root)
        prior_approvals = disagreement_rate = override_window_seconds = None
        if existing is not None:
            # Sticky prohibition (fail-closed): a re-applied twin that does not
            # re-declare an existing prohibition must not silently clear it. Carrying
            # it forward also keeps both apply paths (re-register and the no-op skip)
            # consistent — neither widens a severed act back to allowed.
            is_prohibited = is_prohibited or bool(existing.get("prohibited"))
            fingerprint = dict(existing.get("fingerprint") or {})
            prior_approvals = existing.get("prior_approvals")
            disagreement_rate = existing.get("disagreement_rate")
            override_window_seconds = existing.get("override_window_seconds")
            if existing.get("reserved_acts"):
                preserved_reservations.append(uc_id)
            carry_reserved_acts = existing.get("reserved_acts") or []   # sticky: merge, don't drop
            existing_oblig = existing.get("obligations") or []
            existing_redress = existing.get("redress") or []
            existing_tags = existing.get("tags") or []   # sticky: a patch must not wipe user-authored tags
            unchanged = (sorted(existing.get("allowed_agents") or []) == desired_agents
                         and (existing.get("risk") or "low") == desired_risk
                         and (existing.get("name") or uc_id) == desired_name
                         # a reservation/prohibition/duty to write is never a no-op
                         and not pr and not is_prohibited
                         and not new_oblig and not new_redress)
            if unchanged:
                n_gate += 1          # no-op: leave the chain record (grade + reservation + duties) intact
                continue
        else:
            fingerprint = {}
            existing_oblig = []
            existing_redress = []
            existing_tags = []
            carry_reserved_acts = []
        reg_kw: dict[str, Any] = {}   # carry the earned grade forward when known
        if prior_approvals is not None:
            reg_kw["prior_approvals"] = int(prior_approvals)
        if disagreement_rate is not None:
            reg_kw["disagreement_rate"] = float(disagreement_rate)
        if override_window_seconds is not None:
            reg_kw["override_window_seconds"] = int(override_window_seconds)
        register_use_case(folder_context, use_case_id=uc_id,
                          name=desired_name, fingerprint=fingerprint,
                          risk=desired_risk, allowed_agents=desired_agents,
                          actor=actor, policy_reservations=pr, prohibited=is_prohibited,
                          obligations=(new_oblig or existing_oblig),
                          redress=(new_redress or existing_redress),
                          carry_reserved=carry_reserved_acts,
                          tags=(existing_tags or None),
                          log_root=log_root, **reg_kw)
        n_gate += 1

    # A reservation/prohibition that names a gate the patch never declared cannot be
    # attached — surface it (do not silently drop), per the no-silent-disagreement rule.
    gate_kinds = {(n["id"][3:] if n["id"].startswith("uc:") else n["id"])
                  for n in nodes if n["class"] == "gate"}
    for k in sorted((set(policy_res) | prohibited_kinds) - gate_kinds):
        warnings.append(f"reservation/prohibition for {k!r} not applied — no matching gate in the patch")
    for k in sorted((set(oblig_by_gate) | set(redress_by_gate)) - gate_kinds):
        warnings.append(f"obligation/redress for {k!r} not applied — no matching gate in the patch")

    graph = governance_graph(folder_context, log_root=log_root)
    return {"ok": True,
            "applied": {"actors": n_actor, "humans": n_human, "gates": n_gate,
                        "reservations": len(policy_res), "prohibitions": len(prohibited_kinds),
                        "obligations": sum(len(v) for v in oblig_by_gate.values()),
                        "redress": sum(len(v) for v in redress_by_gate.values())},
            "preserved_reservations": sorted(set(preserved_reservations)),
            "warnings": warnings,
            "pending": pending, "graph": graph}


#: The governance authoring/navigation layer's ops — gated behind a feature flag so the whole new
#: surface can be turned off (RVND_GOVERNANCE_LAYER=off) without a redeploy. Default ON.
_GOVERNANCE_LAYER_OPS = frozenset({
    "governance_map", "governance_chat", "governance_kg",
    "model_capability", "security_dashboard", "officer"})


def _governance_layer_enabled() -> bool:
    return os.environ.get("RVND_GOVERNANCE_LAYER", "on").strip().lower() not in ("off", "0", "false", "no")


def _governance_layer_dispatch(op: str, p: dict[str, Any]) -> dict[str, Any]:
    """The governance-layer ops behind one validated boundary. Raises ValueError/TypeError on a
    bad param VALUE; the facade converts those to a structured ``{"error": ...}`` (the generic
    missing-param KeyError guard misdiagnosed them before). All ops are read-only projections."""
    def _provisions() -> Optional[list]:
        prov = p.get("provisions")
        if prov is not None and (not isinstance(prov, list)
                                 or any(not isinstance(x, dict) for x in prov)):
            raise ValueError("provisions must be a list of {pinpoint, text} objects")
        return prov

    if op == "governance_chat":
        # universal chat: one input → the right op (ingest / intake / ask). Thin over
        # governance_chat.chat(), which reuses the router + the three existing ops.
        from . import governance_chat as _gchat
        return _gchat.chat(p.get("text", ""), policy_text=p.get("policy_text", ""),
                           instrument=p.get("instrument", "policy"), intent=p.get("intent"),
                           folder=p.get("folder_context"))
    if op == "security_dashboard":
        # read-only projection over the folder's signed chain: quarantine / card-gate / erase
        # events → admitted/held/rejected + roll-ups, with the `limits` disclosure.
        # Declares, never certifies; runs nothing.
        from . import security_dashboard as _sd
        return _sd.from_log(p["folder_context"], log_root=_log_root(),
                            group_by=p.get("group_by", "verdict"))
    if op == "officer":
        # preview a policy-programmed oversight binding: compose its control form with a gate
        # floor (strictest-wins, TIGHTEN-only) and show where a reserved act escalates. The
        # officer definition is passed in (no store); read-only. Enforcing it inside
        # action_gate.decide_action (auto-loading REGISTERED officers) is a separate change.
        from . import demand_cta as _dc
        from . import officer as _off
        form = p.get("control_form", "single_approver")
        if form not in _dc._OVERSIGHT_GUARANTEES:
            # fail CLOSED: a typo'd control form must error, not silently preview as
            # "no oversight" — the op's whole invariant is tighten-only oversight.
            raise ValueError(f"unknown control_form {form!r}; "
                             f"one of {sorted(_dc._OVERSIGHT_GUARANTEES)}")
        o = _off.Officer(
            officer_id=p.get("officer_id", "officer"), name=p.get("name", ""),
            oversees=p.get("oversees") or [], control_form=form,
            escalation_party=p.get("escalation_party", ""), policy=p.get("policy") or [],
            authority=p.get("authority", ""))
        out = {"officer": o.as_dict(),
               "oversight": _off.oversight_for(o, gate_floor=p.get("gate_floor", "auto"),
                                               grade=p.get("grade", "L4"))}
        if p.get("act") is not None:
            out["reserved_route"] = _off.route_reserved(
                o, p["act"], folder_context=p.get("folder_context"), log_root=_log_root())
        return out
    if op == "model_capability":
        # read-only readiness: for each LLM task (or a given `task`), is a capable local model
        # registered → run_local, or the degrade action? Reads the registry only; runs nothing.
        # The enforcement gate on use_llm lives in policy_ingest (the ambient-proposer gate).
        from . import model_capability as _mcap
        t = p.get("task")
        return _mcap.for_task(t).as_dict() if t else _mcap.readiness()
    if op == "governance_map":
        # rules→roles/steps/risks projection (governance_map/v1). Thin over the contract's
        # serve(): the op cannot emit anything the schema does not define.
        from . import governance_map as _gmap
        return _gmap.serve(
            p.get("view"),
            provisions=_provisions(), policy_text=p.get("policy_text"),
            instrument=p.get("instrument", "policy"),
            coverage=p.get("coverage"), bindings=p.get("bindings"),
            status_of=p.get("status"), currency_of=p.get("currency"),
            question=p.get("question"))
    if op == "governance_kg":
        # universal KG projection (governance_kg/v1) over the same rules the map builds
        # (governance_map.build) — a zoom-level graph, or a reasoning path between two nodes.
        # Read-only; reuses the one rule source so the KG and the map cannot disagree.
        from . import governance_map as _gmap
        from . import governance_kg as _gkg
        dims = p.get("dimensions")
        if isinstance(dims, str):
            dims = [dims]        # a bare string would iterate as CHARACTERS → silently empty graph
        src, dst = p.get("from"), p.get("to")
        if bool(src) != bool(dst):
            raise ValueError("a reasoning path needs BOTH 'from' and 'to' (got only one) — "
                             "omit both for a graph projection")
        gm = _gmap.build(
            provisions=_provisions(), policy_text=p.get("policy_text"),
            instrument=p.get("instrument", "policy"),
            coverage=p.get("coverage"), bindings=p.get("bindings"),
            status_of=p.get("status"), currency_of=p.get("currency"))
        if src and dst:
            return _gkg.path(gm.rules, src, dst)
        return _gkg.project(
            gm.rules, level=p.get("level", "cluster"), focus=p.get("focus"),
            dimensions=dims, demand_as=p.get("demand_as", "node"))
    raise ValueError(f"unrouted governance-layer op {op!r}")           # keep sets in sync


@mcp.tool()
def workspace_workflow(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Workflow + run-lifecycle facade: one tool (replaces 14 workflow/run tools).
    workspace_workflow(op="help") lists the operations.

    op: define | list | delete | run | enqueue | active | queue | take_next |
        renew_lease | mark_done | mark_failed | inspect_stuck | resume | cancel
    """
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        _ops = [
            {"op": "define", "required": ["folder_context", "name", "steps"]},
            {"op": "list", "required": ["folder_context"]},
            {"op": "delete", "required": ["folder_context", "name"]},
            {"op": "run", "required": ["folder_context", "name"]},
            {"op": "enqueue", "required": ["folder_context", "name"]},
            {"op": "active", "required": ["folder_context"]},
            {"op": "queue", "required": []},
            {"op": "take_next", "required": []},
            {"op": "renew_lease", "required": ["run_id"]},
            {"op": "mark_done", "required": ["run_id"]},
            {"op": "mark_failed", "required": ["run_id"]},
            {"op": "inspect_stuck", "required": []},
            {"op": "resume", "required": ["run_id"]},
            {"op": "cancel", "required": ["run_id"]},
            {"op": "approval_request", "required": ["folder_context", "request_id", "now"],
             "optional": ["form", "competence", "requester", "timeout_seconds", "actor", "quorum", "competences", "on_elapse"],
             "note": "open an approval under a 1.5 control form; timeout is DENY"},
            {"op": "approval_decide", "required": ["folder_context", "request_id", "decision", "actor", "now"],
             "note": "approve|deny - whether it COUNTS is the projection's call"},
            {"op": "approval_delegate", "required": ["folder_context", "competence", "from_party", "to_party", "actor", "now"],
             "note": "absence -> delegate: a logged grant from a competence holder"},
            {"op": "approval_resolve", "required": ["folder_context", "request_id", "now"],
             "note": "pure projection: granted | denied | pending + reason"},
            {"op": "approval_list", "required": ["folder_context", "now"],
             "optional": ["state"],
             "note": "the 1.5 approval inbox: every request resolved at now (role-based quorum + temporal), beside the named-signer contract reviews"},
            {"op": "operate", "required": ["folder_context", "use_case_id", "agent_id", "issues", "now_epoch", "capability_token"],
             "note": "governed step executor: requires a live signed session capability; per-issue disposition (auto|human|reserved|refused), journalled"},
            {"op": "runs", "required": ["folder_context"],
             "note": "replay projection of journalled operate runs"},
            {"op": "use_case_register", "required": ["folder_context", "use_case_id", "name", "fingerprint", "risk", "allowed_agents", "actor"],
             "optional": ["tags", "prior_approvals", "disagreement_rate", "override_window_seconds"],
             "note": "register/re-version a governed use case (binds contract + reserved acts)"},
            {"op": "use_case_list", "required": ["folder_context"],
             "note": "latest-wins projection of registered use cases"},
            {"op": "use_case_get", "required": ["folder_context", "use_case_id"],
             "note": "one use case (latest version) or null"},
            {"op": "authority_revoke", "required": ["folder_context", "use_case_id", "agent_id", "actor"],
             "note": "remove one agent's authority over one use case — the tighten-only write a coverage cell carries; re-versions the use case with everything else carried forward (reserved acts, duties, tags, the earned grade). Granting never happens here: widening authority stays a deliberate act on the patch"},
            {"op": "governance_graph", "required": ["folder_context"],
             "optional": ["dialect"],
             "note": "read-only patch: {nodes, edges, verdicts} from parties + use cases + runs; verdicts decided server-side (strictest-wins). dialect='v05' projects the Loomground v0.5 vocabulary (actor/gate/cord, 5-verdict alphabet)"},
            {"op": "loop_graph", "required": ["folder_context"],
             "optional": ["catalogue_fingerprint"],
             "note": "read-only graph of interacting execution, oversight, drift, recovery and policy loops, grounded in the signed governance graph and latest drift baseline"},
            {"op": "governance_live", "required": ["folder_context"],
             "optional": ["chain_limit"], "mutates": False,
             "note": "read-only live-governance board: sessions (derived from the signed log's admission events; admitted = unexpired + unrevoked), per-agent verdict/grade/escalation from lane_capabilities, run-lease serialization (one holder per folder+workflow) from the queue, and the last N chain entries (seq = replay index, prev_hash). Pure projection — no chain append, no lease acquire. Honest-subset: session kind, autonomy decay, iteration budget and per-agent breaker have no folder-readable source yet and are omitted, never faked"},
            {"op": "oversight_cert_verify", "required": ["envelope"],
             "optional": ["now", "required_basis", "public_key_pem"], "mutates": False,
             "note": "read-only offline re-check of a portable oversight certificate (the oversight-certificate package's DSSE envelope): verifies signature, canonical form, disposition shape and credential-at-decision-time, returning {ok, findings:[{code, detail}]}. Verifies against a supplied PEM public key, else this host's identity key. No folder, no chain, no key generation"},
            {"op": "connected_agents", "required": [], "mutates": False,
             "note": "read-only, SERVER-LEVEL: agents that completed the MCP handshake with this server, independent of any workspace — who is CONNECTED (vs the per-workspace board's who is ADMITTED to act here). Presence, not authority; liveness is the connecting process. No folder."},
            {"op": "connected_agents_governance", "required": ["folder_context"],
             "optional": ["chain_limit"], "mutates": False,
             "note": "read-only join of SERVER-LEVEL presence (connected_agents) to this folder's REAL chain governance, at agent-NAME granularity. Per connection: real connid/agent/transport/pid/connected_at, plus a governance object. attributed=true iff the agent name appears as an actor on the signed chain (>=1 event) — only then are verdict/grade/escalation (from lane_capabilities, strictest-wins) and the actor's chain tail (recent[], event_count, last_event_ts) returned. Unattributed ⇒ honest-neutral (all nulls/empty); no fabricated or fail-closed verdict, and connid/pid never derive governance. Pure projection."},
            {"op": "session_governance", "required": ["folder_context"],
             "optional": ["chain_limit"], "mutates": False,
             "note": "read-only per-SESSION governance sourced from the SIGNED CHAIN (the real per-session identity: the actor the PreToolUse hook records). Returns sessions:[{actor, verdict, grade, escalation (REAL lane disposition via lane_capabilities strictest-wins; fail-closed 'refused' when the actor has no approved lane is a real disposition), event_count, last_event_ts, recent[] (the actor's own chain tail), connected/connid/pid (a live connection joined by session_id==actor, the host session id CLAUDE_CODE_SESSION_ID captured on connect; agent name only as a fallback)}] plus connected_only:[idle presence that has not acted]. The chain IS keyed by the per-session actor, so chain actors are the primary list; a live connection carrying the same session id surfaces as that actor's real presence. Pure projection; no fabrication."},
            {"op": "reasoning_check", "required": ["session_id"],
             "optional": ["claim"],
             "note": "T-cons: solver consistency over the session's recorded claims (session-scoped Versum working memory). With claim {atom, polarity, grounding, ts}: append it to the session's OWN store first (the op's only mutation — no chain append, no lease, no cross-session write), then check; without: pure read. Fail-closed verdict CONSISTENT | INCONSISTENT (clashing atoms carried) | OPEN — ungrounded or uncheckable claims are OPEN, never reported consistent"},
            {"op": "governance_lane_register", "required": ["folder_context", "lane_id", "agent", "max_grade", "action_classes", "approved_by", "rationale"],
             "optional": ["footprints", "use_cases", "connectors", "policy_fingerprint", "version"],
             "note": "approve a versioned governance lane on the signed chain; authority, autonomy, action, data, workspace, use-case, connector and policy scope are checked before live execution"},
            {"op": "governance_lane_list", "required": ["folder_context"],
             "note": "latest approved governance lane per agent"},
            {"op": "governance_open", "required": ["folder_context", "party", "policy_fingerprint"],
             "optional": ["ttl_seconds"],
             "note": "proxy-authenticated session mint: active agent + current lane + exact policy binding; returns a short-lived Ed25519 capability"},
            {"op": "governance_query", "required": ["folder_context", "query"],
             "note": "named read-only query over the patch (needs_human_no_human | auto_high_risk | unfired | unwired_use_cases | agent_reach)"},
            {"op": "coverage_matrix", "required": ["folder_context"],
             "optional": ["preset", "gaps_only", "tags"],
             "note": "read-only coverage lens: the same patch as a rows x cols grid so absence and policy-shape are visible (the spatial form of governance_query). preset='kind_risk' (flagship, derived read-only) is default; preset='list' returns the available lenses. Each cell is the strictest-wins verdict for its band with the source use cases attached; gaps_only drops finding-free rows"},
            {"op": "lane_capabilities", "required": ["folder_context", "actor"],
             "optional": ["kinds", "risks"],
             "note": "read-only agent-facing projection of ONE agent's governance-lane "
                     "boundaries: per (kind[, risk]) the verdict the gate would dispose "
                     "(auto|human|reserved|refused|prohibited), the grade required, the "
                     "escalation point, and the governing guard. A projection of the same "
                     ".lg policy the gate enforces — advisory, never dispositive. Carries "
                     "the policy_fingerprint it reflects. Fail-closed: unreadable policy "
                     "yields no capabilities, never 'all allowed'. Also rides the "
                     "governance_open admission response, so an agent starts knowing its "
                     "bounds; this verb re-queries it mid-session"},
            {"op": "governance_register", "required": ["folder_context"],
             "optional": ["scope"],
             "note": "read-only register/inventory of agents + use-cases (per folder; scope='all' aggregates known folders). Categorical status, never a score; per-folder + all-folders, NOT multi-tenant"},
            {"op": "governance_netlist", "required": ["folder_context"],
             "note": "render the current chain as a v0.5 .lg netlist (the editor's text surface; structure round-trips via patch_apply)"},
            {"op": "transport_audit", "required": ["folder_context"],
             "note": "the transport/clock primitive: read-only audit that every run originated from one external trigger (nothing self-starts)"},
            {"op": "connector_register", "required": ["folder_context", "connector_id", "role", "channel"],
             "optional": ["use_cases", "name", "tags", "floor", "group", "credential_ref", "destination_class", "tool_ref", "actor"],
             "note": "register a boundary connector (role: ingress|egress|oversight; channel: email/ticket/message/api/…). `floor` (permit|hold|deny) is the channel's self-governance minimum; `group` is the client/tenant group-bus it belongs to. Both honored strictest-wins in federated_decision. `credential_ref` (egress only) is the track's access binding as a known-scheme REFERENCE (env:/keydir:/oidc:/spiffe:) — never the secret. `destination_class` (egress only, llm|tool_api|message|file) declares which side of the wall the track reaches — the axis the egress board words enforcement by; unset stays undeclared. `tool_ref` ({tool_name, arg_mapping}) binds a federated connector to the host-invocable MCP tool tool_call_plan plans against. Concrete send is a permissioned external action."},
            {"op": "egress_board", "required": ["folder_context"],
             "note": "read-only per-track egress board: one row per egress connector with its floor lamp and cable state (credential ref + arm status no_cable/armed/unplugged, resolved fail-closed, secret-free). mode is honest: 'attested' until a broker actually holds the track's plug."},
            {"op": "track_strip", "required": ["folder_context"],
             "optional": ["party_id", "connector_id"],
             "note": "the channel-strip projection for ONE track (exactly one of party_id | connector_id; anything else fails closed). Read-only join of the existing projections: identity/status, the L0-L4 oversight ladder with its law-basis locks, channels/floors, use cases, reservations, the routed m-of-n sign-off meter, the per-track verdict meter; egress connectors additionally carry the cable (reference + arm status, never the secret)."},
            {"op": "console_snapshot", "required": [], "optional": ["now", "attention_limit"],
             "note": "read-only mixer rollup: one aggregate per visible workspace (worst verdict, pending count, agent tallies, tree parent) plus a bounded attention list. Folderless and cross-workspace, so it enumerates through the principal-scoped registry — a caller sees only their own workspaces. Aggregates are meters, never a score."},
            {"op": "group_floor", "required": ["folder_context", "group_id"], "optional": ["floor", "actor"],
             "note": "B′ bus: set a GROUP (client/tenant) policy floor — governs ALL its channels collectively (a channel can be stricter, never looser)"},
            {"op": "group_revoke", "required": ["folder_context", "group_id"], "optional": ["reason", "actor"],
             "note": "B′ bus group kill switch: mute a whole client/tenant — every channel in the group is dropped from every future join"},
            {"op": "connector_list", "required": ["folder_context"],
             "note": "latest-wins projection of registered connectors"},
            {"op": "tool_verdict", "required": ["folder_context", "connector_id"],
             "optional": ["raw_tier", "input_ref", "actor"],
             "note": "B′ bus: record that a federated tool returned a risk-tier over an input — Rvnd maps it to the tri-state (attributed, not asserted) and signs it. The HOST invokes the tool; Rvnd never touches the network."},
            {"op": "tool_revoke", "required": ["folder_context", "connector_id"],
             "optional": ["reason", "actor"],
             "note": "B′ bus kill switch: a revoked tool's verdicts are dropped from every future join"},
            {"op": "federated_decision", "required": ["folder_context", "use_case_id"],
             "optional": ["local"],
             "note": "B′ bus: join the local verdict with every non-revoked federated tool linked to the use case (strictest-wins) — returns the decision + per-source breakdown (disagreement is recorded, not hidden), plus any human override and the effective_decision it yields"},
            {"op": "federation_override", "required": ["folder_context", "use_case_id", "verdict", "actor", "reason"],
             "note": "B′ bus: record a human's resolution of a federated disagreement. Refused unless the current join disagrees, and the verdict must be one the non-revoked sources emitted — a human picks a real reading, never invents one. Any newer tool verdict from a linked source supersedes the override (fail-closed); superseded overrides stay visible."},
            {"op": "tool_call_plan", "required": ["folder_context", "connector_id"],
             "optional": ["input_ref"],
             "note": "B′ bus round-trip (pull model): read-only call descriptor for a tool_ref-bound connector — the HOST invokes the tool and reports back via tool_verdict; RVND signs THAT the tool said X over digest(input_ref), never that X is true, and never touches the network. Refuses unknown/unbound/revoked connectors and killed groups."},
            {"op": "patch_validate", "required": ["folder_context"],
             "optional": ["netlist", "patch"],
             "note": "typed-cord + shape validation of a patch (netlist text or patch dict), fail-closed; no writes"},
            {"op": "patch_apply", "required": ["folder_context", "actor"],
             "optional": ["netlist", "patch"],
             "note": "validate then write a patch to the signed chain (parties + use cases + authority cords); returns the resulting governance_graph"},
            {"op": "policy_ingest", "required": ["folder_context"],
             "optional": ["policy_text", "path", "use_llm"],
             "note": "ingest an AI policy → a draft v0.5 governance twin (express/policy/host classification + validated patch + netlist + host hand-offs). Source is `policy_text`, or `path` to a file in the workspace folder (txt/pdf/docx, sandboxed). Declares, does not certify; applies NOTHING (applied:false) until a human confirms via patch_apply"},
            {"op": "governance_chat", "required": [],
             "optional": ["folder_context", "text", "policy_text", "instrument", "intent"],
             "note": "universal governance chat: ONE input routed to ingest / intake / ask — a policy is compiled to a twin, a self-description fills the subject card, a question is answered from the map. Returns {intent, echo, kind, result}; the router proposes the intent, `intent` overrides it. Declares, does not certify."},
            {"op": "governance_map", "required": [],
             "optional": ["folder_context", "provisions", "policy_text", "instrument", "view", "coverage", "bindings", "status", "currency", "question"],
             "note": "project the rules→roles/steps/risks governance map (governance_map/v1): a versioned, typed rule list with roll-ups and a group-by/filter/deep-link tree the map panel renders. Read-only projection; declares, does not certify. `view` = {group_by, sort, filters, focus}."},
            {"op": "governance_kg", "required": [],
             "optional": ["folder_context", "provisions", "policy_text", "instrument", "level", "focus", "dimensions", "demand_as", "from", "to", "coverage", "bindings", "status", "currency"],
             "note": "project the governance knowledge graph (governance_kg/v1) over the SAME rules the map builds: nodes (instrument/role/room/rule/obligation/gate/artifact) + 5-dimension edges at a zoom `level` (overview/cluster/detail, `focus` a rule_id), or a reasoning `path` between two node ids (`from`/`to`) with composed dimension + edge provenance. Read-only projection; declares, does not certify."},
            {"op": "model_capability", "required": [],
             "optional": ["folder_context", "task"],
             "note": "read-only model-capability readiness (model_capability/v1): for each LLM task (or a given `task`), whether a capable LOCAL model is registered → run_local, or a bounded fallback (deterministic / keyword_only / escalate_human / …). Reads the local registry only; runs nothing. The enforcement gate on use_llm is separate."},
            {"op": "security_dashboard", "required": ["folder_context"],
             "optional": ["group_by"],
             "note": "read-only security projection (security/v1) over the folder's signed chain: quarantine / card-gate / erase-guard events → admitted/held/rejected + live holds_pending + group-by roll-ups, carrying a `limits` disclosure (a denylist tripwire, not containment — a clean board is not proof of safety). Declares, never certifies; runs nothing."},
            {"op": "officer", "required": ["folder_context"],
             "optional": ["officer_id", "name", "oversees", "control_form", "escalation_party", "policy", "authority", "gate_floor", "grade", "act"],
             "note": "preview a policy-programmed oversight binding: compose its control form with a gate `gate_floor` (strictest-wins, TIGHTEN-ONLY — an officer can never loosen a regulated gate) and, given an `act`, show where a reserved judgment escalates (the officer routes, never auto-decides). The officer is passed in; read-only. Declares, never certifies."},
        ]
        if not _governance_layer_enabled():
            _ops = [o for o in _ops if o["op"] not in _GOVERNANCE_LAYER_OPS]
        return {"ops": _ops}
    try:
        if op in _GOVERNANCE_LAYER_OPS and not _governance_layer_enabled():
            return {"ok": False, "error": "governance layer disabled",
                    "flag": "RVND_GOVERNANCE_LAYER", "op": op}
        if op == "define":        return define_workflow(p["folder_context"], p["name"], p["steps"], p.get("description", ""))
        if op == "list":          return list_workflows(p["folder_context"], p.get("include_ancestors", True))
        if op == "delete":        return delete_workflow(p["folder_context"], p["name"])
        if op == "run":           return run_workflow(p["folder_context"], p["name"])
        if op == "enqueue":       return enqueue_workflow_run(p["folder_context"], p["name"], p.get("enqueued_by", ""))
        if op == "active":        return active_workflows(p["folder_context"])
        if op == "queue":         return list_queue(p.get("state_filter", ""), p.get("folder_context", ""))
        if op == "take_next":     return take_next_run(p.get("worker_id", ""), p.get("lease_seconds", 60))
        if op == "renew_lease":   return renew_lease(p["run_id"], p.get("additional_seconds", 60))
        if op == "mark_done":     return mark_run_done(p["run_id"])
        if op == "mark_failed":   return mark_run_failed(p["run_id"], p.get("error", ""))
        if op == "inspect_stuck": return inspect_stuck_runs(p.get("stale_pending_seconds", 300))
        if op == "resume":        return resume_run(p["run_id"])
        if op == "cancel":        return cancel_run(p["run_id"])
        if op == "operate":
            from .operations import operate as _operate
            return _operate(p["folder_context"], use_case_id=p["use_case_id"],
                            agent_id=p["agent_id"], issues=p["issues"],
                            now_epoch=int(p["now_epoch"]),
                            capability_token=p.get("capability_token", ""),
                            journal=p.get("journal", True), log_root=_log_root())
        if op == "governance_open":
            from .session_admission import governance_open as _open
            return _open(
                p["folder_context"],
                party=p["party"],
                policy_fingerprint=p["policy_fingerprint"],
                ttl_seconds=int(p.get("ttl_seconds", 900)),
                log_root=_log_root(),
            )
        if op == "runs":
            from .operations import runs_for as _runs
            return {"runs": _runs(p["folder_context"], log_root=_log_root())}
        if op == "use_case_register":
            from .use_case import register_use_case as _ruc
            uid = _ruc(p["folder_context"], use_case_id=p["use_case_id"],
                       name=p["name"], fingerprint=p["fingerprint"],
                       risk=p["risk"], allowed_agents=p["allowed_agents"],
                       actor=p["actor"],
                       prior_approvals=int(p.get("prior_approvals", 0)),
                       disagreement_rate=float(p.get("disagreement_rate", 0.0)),
                       override_window_seconds=int(p.get("override_window_seconds", 0)),
                       tags=p.get("tags"),
                       log_root=_log_root())
            return {"ok": True, "use_case_id": uid}
        if op == "use_case_list":
            from .use_case import list_use_cases as _luc
            return {"use_cases": _luc(p["folder_context"], log_root=_log_root())}
        if op == "use_case_get":
            from .use_case import get_use_case as _guc
            return {"use_case": _guc(p["folder_context"], p["use_case_id"],
                                     log_root=_log_root())}
        if op == "authority_revoke":
            from .use_case import revoke_agent as _rva
            return _rva(p["folder_context"], p["use_case_id"], p["agent_id"],
                        actor=p["actor"], log_root=_log_root())
        if op == "governance_graph":
            from .governance_graph import governance_graph as _gg
            if p.get("dialect") == "v05":
                from .governance_graph import governance_graph_v05 as _gg5
                return _gg5(p["folder_context"], log_root=_log_root())
            return _gg(p["folder_context"], log_root=_log_root())
        if op == "governance_live":
            from .governance_live import governance_live as _glive
            return _glive(p["folder_context"], log_root=_log_root(),
                          chain_limit=int(p.get("chain_limit", 20)))
        if op == "oversight_cert_verify":
            import datetime as _dt

            from .oversight_cert import verify_certificate
            _now = str(p.get("now") or _dt.datetime.now(_dt.timezone.utc)
                       .isoformat().replace("+00:00", "Z"))
            return verify_certificate(
                p.get("envelope") or {}, now=_now,
                required_basis=p.get("required_basis"),
                public_key_pem=p.get("public_key_pem"))
        if op == "connected_agents":
            from .connected_agents import list_connected
            agents = list_connected()
            return {"ok": True, "count": len(agents), "agents": agents}
        if op == "connected_agents_governance":
            from .governance_live import connected_agents_governance as _cag
            return _cag(p["folder_context"], log_root=_log_root(),
                        chain_limit=int(p.get("chain_limit", 10)))
        if op == "session_governance":
            from .governance_live import session_governance as _sg
            return _sg(p["folder_context"], log_root=_log_root(),
                       chain_limit=int(p.get("chain_limit", 10)))
        if op == "reasoning_check":
            from .reasoning_integrity import Claim, check_session, record_claim
            sid = p["session_id"]
            if p.get("claim") is not None:
                c = p["claim"]
                record_claim(sid, Claim(atom=str(c.get("atom", "")),
                                        polarity=str(c.get("polarity", "+")),
                                        grounding=c.get("grounding"),
                                        ts=str(c.get("ts", ""))),
                             log_root=_log_root())
            v = check_session(sid, log_root=_log_root())
            return {"ok": True, "session_id": sid, "verdict": v.verdict,
                    "reasons": list(v.reasons), "clashing": list(v.clashing),
                    "open_claims": list(v.open_claims)}
        if op == "loop_graph":
            from .loop_graph import graph_of_loops as _gl
            return _gl(p["folder_context"], log_root=_log_root(),
                       catalogue_fingerprint=p.get("catalogue_fingerprint", ""))
        if op == "governance_lane_register":
            from .governance_lane import GovernanceLane as _Lane, register_lane as _rl
            lane = _Lane(
                lane_id=p["lane_id"], agent=p["agent"], max_grade=p["max_grade"],
                action_classes=tuple(p["action_classes"]),
                footprints=tuple(p.get("footprints") or ()),
                folder=p["folder_context"], use_cases=tuple(p.get("use_cases") or ()),
                connectors=tuple(p.get("connectors") or ()),
                policy_fingerprint=p.get("policy_fingerprint", ""),
                version=int(p.get("version", 1)), approved_by=p["approved_by"],
                rationale=p["rationale"])
            return _rl(p["folder_context"], lane, log_root=_log_root())
        if op == "governance_lane_list":
            from .governance_lane import list_lanes as _ll
            return {"lanes": [lane.to_dict() for lane in
                              _ll(p["folder_context"], log_root=_log_root())]}
        if op == "governance_query":
            from .governance_graph import governance_query as _gq
            return _gq(p["folder_context"], p["query"], log_root=_log_root())
        if op == "coverage_matrix":
            from .matrix_coverage import coverage_matrix as _cm, presets as _cmp
            if p.get("preset") == "list":
                return {"presets": _cmp()}
            return _cm(p["folder_context"], p.get("preset", "kind_risk"),
                       gaps_only=bool(p.get("gaps_only")), tags=p.get("tags"),
                       log_root=_log_root())
        if op == "lane_capabilities":
            from .mcp_impl import lane_capabilities as _lcap
            return _lcap(p["folder_context"], p["actor"],
                         kinds=p.get("kinds"), risks=p.get("risks"))
        if op == "governance_register":
            from .governance_graph import governance_register as _gr, governance_register_all as _gra
            if p.get("scope") == "all":
                return _gra(log_root=_log_root())
            return _gr(p["folder_context"], log_root=_log_root())
        if op == "governance_netlist":
            from .governance_graph import governance_netlist as _gn
            return _gn(p["folder_context"], log_root=_log_root())
        if op == "transport_audit":
            from .operations import transport_audit as _ta
            return _ta(p["folder_context"], log_root=_log_root())
        if op == "connector_register":
            from .connectors import register_connector as _rc
            return _rc(p["folder_context"], connector_id=p["connector_id"],
                       role=p["role"], channel=p["channel"],
                       use_cases=p.get("use_cases"), name=p.get("name", ""),
                       tags=p.get("tags"), floor=p.get("floor", "permit"),
                       group=p.get("group", ""),
                       credential_ref=p.get("credential_ref"),
                       destination_class=p.get("destination_class", ""),
                       tool_ref=p.get("tool_ref"),
                       actor=p.get("actor", "user"), log_root=_log_root())
        if op == "egress_board":
            from .connectors import egress_board as _eb
            from .lock import probe_broker as _pb
            # attest LLM egress live: a bound broker lets the board report
            # 'enforced'; every probe failure resolves to bound_here=False (fail-safe).
            return _eb(p["folder_context"], log_root=_log_root(),
                       llm_broker=_pb(p["folder_context"]))
        if op == "track_strip":
            from .track_strip import track_strip as _ts
            return _ts(p["folder_context"], party_id=p.get("party_id"),
                       connector_id=p.get("connector_id"), log_root=_log_root())
        if op == "console_snapshot":
            from .console_snapshot import console_snapshot as _cs
            import time as _t
            return _cs(now=float(p.get("now", _t.time())),
                       log_root=_log_root(),
                       attention_limit=int(p.get("attention_limit", 20)))
        if op == "tool_verdict":
            from .tool_federation import record_tool_verdict as _rtv
            return _rtv(p["folder_context"], connector_id=p["connector_id"],
                        raw_tier=p.get("raw_tier", ""), input_ref=p.get("input_ref", ""),
                        actor=p.get("actor", "host"), log_root=_log_root())
        if op == "tool_revoke":
            from .tool_federation import revoke_tool as _rvt
            return _rvt(p["folder_context"], connector_id=p["connector_id"],
                        actor=p.get("actor", "user"), reason=p.get("reason", ""),
                        log_root=_log_root())
        if op == "group_floor":
            from .tool_federation import set_group_floor as _sgf
            return _sgf(p["folder_context"], group_id=p["group_id"],
                        floor=p.get("floor", "permit"), actor=p.get("actor", "user"),
                        log_root=_log_root())
        if op == "group_revoke":
            from .tool_federation import revoke_group as _rvg
            return _rvg(p["folder_context"], group_id=p["group_id"],
                        actor=p.get("actor", "user"), reason=p.get("reason", ""),
                        log_root=_log_root())
        if op == "federated_decision":
            from .tool_federation import federated_decision as _fd
            from .verdict import Verdict as _V, coerce as _coerce
            _lraw = p.get("local")
            # absent ⇒ permit (no local constraint); a provided-but-invalid value ⇒
            # DENY (fail-safe), never an uncaught ValueError crashing the MCP call.
            _loc = _V.PERMIT if not _lraw else _coerce(_lraw, default=_V.DENY)
            return _fd(p["folder_context"], use_case_id=p["use_case_id"],
                       local=_loc, log_root=_log_root())
        if op == "federation_override":
            from .tool_federation import record_federation_override as _rfo
            # A refused override answers with its wording, never a stack trace.
            try:
                return _rfo(p["folder_context"], use_case_id=p["use_case_id"],
                            verdict=p["verdict"], actor=p["actor"],
                            reason=p["reason"], log_root=_log_root())
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        if op == "tool_call_plan":
            from .tool_federation import tool_call_plan as _tcp
            # A refused plan answers with its wording, never a stack trace.
            try:
                return _tcp(p["folder_context"], connector_id=p["connector_id"],
                            input_ref=p.get("input_ref", ""), log_root=_log_root())
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        if op == "connector_list":
            from .connectors import list_connectors as _lc
            return {"connectors": _lc(p["folder_context"], log_root=_log_root())}
        if op in ("patch_validate", "patch_apply"):
            # The facade speaks the published Loomground v0.5 language
            # (actor/gate/cord + reserve/prohibit/obligation/redress), via the
            # conformant engine loomground_lang. Apply maps the validated patch
            # to the chain through the documented bijection.
            from . import loomground_lang as _L
            raw = p.get("patch")
            if raw is not None:
                if not isinstance(raw, dict):
                    return {"ok": False, "errors": ["patch must be an object"]}
                patch = dict(raw)
            else:
                netlist = p.get("netlist", "")
                if not isinstance(netlist, str):
                    return {"ok": False, "errors": ["netlist must be a string"]}
                try:
                    patch = _L.parse(netlist)
                except _L.ParseError as e:
                    return {"ok": False, "stage": "parse", "errors": [str(e)]}
            # A hand-supplied patch dict is untrusted: guard the engine calls so
            # a malformed shape returns a clean error instead of crashing the op.
            try:
                v = _L.validate(patch)
                if op == "patch_validate":
                    return {"ok": v["ok"], "errors": v["errors"],
                            "projection": _L.project(patch) if v["ok"] else None}
                if not v["ok"]:
                    return {"ok": False, "errors": v["errors"]}
                return _loom_apply(p["folder_context"], patch,
                                   actor=p["actor"], log_root=_log_root())
            except (TypeError, AttributeError, KeyError) as e:
                return {"ok": False, "errors": [f"malformed patch: {e}"]}
        if op == "policy_ingest":
            # Policy ingest → digital twin; consumed by the UI panel.
            from .adapters.ingest.governance import compiler as _pi
            _txt = p.get("policy_text", "")
            _genre = None
            _path = p.get("path")
            if _path and not _txt:
                # file/PDF ingestion: read + extract + genre-route, sandboxed to the workspace
                # folder — no reading arbitrary files off the box.
                _txt, _genre, _err = _read_policy_file(_path, p.get("folder_context", ""))
                if _err:
                    return {"ok": False, "errors": [f"file ingest: {_err}"]}
            _use_llm = bool(p.get("use_llm", False))
            twin = _pi.ingest(_txt, use_llm=_use_llm)
            # Honest-degrade contract (restored). The retired RVND-local policy_ingest
            # declared the ambient local-model gate's verdict on the twin whenever the
            # local model was opted into — never a silent skip. The governance compiler
            # consumed from loomground-ingest carries no local-model registry (that is
            # RVND's concern), so it emits no `capability`. Re-apply the gate here: an
            # opt-in must either report `llm_used` or carry the capability verdict
            # (capable:false → the deterministic draft ran, declared in the panel).
            if _use_llm and isinstance(twin, dict) and twin.get("ok"):
                from . import model_capability as _mc
                # `or`, not setdefault: a present-but-None capability must still be
                # filled (setdefault only fills an absent key).
                twin["capability"] = twin.get("capability") or _mc.for_task("extraction").as_dict()
                twin["llm_used"] = bool(twin.get("llm_used"))
            # G3 (Rvnd): anchor each reservation to the statute the policy NAMES.
            twin = _attach_statute_sources(twin, _txt)
            if _genre and isinstance(twin, dict):
                twin["ingest_genre"] = _genre        # which document genre was detected
            return twin
        if op in _GOVERNANCE_LAYER_OPS:
            # One validated boundary for the layer's ops: a bad param value (unknown facet/level/
            # task, wrong shape) returns a structured {"error": ...} like the approval_* block —
            # never an uncaught exception, and never the facade's "missing param" misdiagnosis.
            try:
                return _governance_layer_dispatch(op, p)
            except (ValueError, TypeError, AttributeError) as e:
                return {"ok": False, "error": f"op {op!r}: {e}"}
        if op in ("approval_request", "approval_decide",
                  "approval_delegate", "approval_resolve", "approval_list"):
            from . import approvals as _ap
            lr = _log_root()
            try:
                if op == "approval_list":
                    return _ap.list_approvals(
                        p["folder_context"], now=float(p["now"]),
                        state=p.get("state"), log_root=lr)
                if op == "approval_request":
                    return _ap.request_approval(
                        p["folder_context"], p["request_id"],
                        form=p.get("form", "single_approver"),
                        competence=p.get("competence", ""),
                        requester=p.get("requester", ""),
                        timeout_seconds=float(p.get("timeout_seconds", 86400)),
                        now=float(p["now"]), actor=p.get("actor", ""),
                        quorum=int(p.get("quorum", 0)),
                        competences=p.get("competences"),
                        on_elapse=p.get("on_elapse", "halt"),
                        log_root=lr)
                if op == "approval_decide":
                    return _ap.decide_approval(
                        p["folder_context"], p["request_id"], p["decision"],
                        actor=p["actor"], now=float(p["now"]), log_root=lr)
                if op == "approval_delegate":
                    return _ap.delegate_competence(
                        p["folder_context"], p["competence"],
                        from_party=p["from_party"], to_party=p["to_party"],
                        actor=p["actor"], now=float(p["now"]), log_root=lr)
                return _ap.resolve_approval(
                    p["folder_context"], p["request_id"],
                    now=float(p["now"]), log_root=lr)
            except ValueError as e:
                return {"error": str(e)}
    except KeyError as e:
        return {"error": f"op {op!r} missing param {e}"}
    return {"error": f"unknown op {op!r}"}


@mcp.tool()
def workspace_lock(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Privacy Lock facade: one tool (replaces 9 lock_* / workspace_lock_* tools) —
    the minimisation gate + the encryption seal. workspace_lock(op="help") lists ops.

    op: seal | unseal | classify | threshold_get | threshold_set | reclassify |
        egress_check | ingress_check | audit_query | setup_status | setup
    """
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        return {"ops": [
            {"op": "seal", "required": ["folder_context"]},
            {"op": "unseal", "required": ["folder_context", "passphrase"]},
            {"op": "classify", "required": ["text"]},
            {"op": "threshold_get", "required": ["folder_context"]},
            {"op": "threshold_set", "required": ["folder_context", "threshold"]},
            {"op": "reclassify", "required": ["folder_context"]},
            {"op": "egress_check", "required": ["tool", "arguments", "task_scope"]},
            {"op": "ingress_check", "required": ["payload", "task_scope"]},
            {"op": "audit_query", "required": ["reason_for_query"]},
            {"op": "setup_status", "required": [],
             "note": "read-only: has lock onboarding completed, and with which backend"},
            {"op": "setup", "required": [],
             "optional": ["backend_spec", "audit_log_path", "skip_smoke_test",
                          "accepted_by", "reason"],
             "note": "run the onboarding wizard headlessly (empty backend_spec accepts "
                     "the recommendation); downgrading a real backend to mock requires "
                     "accepted_by + reason (no silent weakening)"},
        ]}
    try:
        if op == "seal":          return workspace_lock_lock(p["folder_context"])
        if op == "unseal":        return workspace_lock_unlock(p["folder_context"], p["passphrase"])
        if op == "classify":      return lock_classify_text(p["text"], p.get("folder_context", ""))
        if op == "threshold_get": return lock_threshold_get(p["folder_context"])
        if op == "threshold_set": return lock_threshold_set(p["folder_context"], p["threshold"])
        if op == "reclassify":    return lock_reclassify_folder(p["folder_context"])
        if op == "egress_check":  return lock_egress_check(p["tool"], p["arguments"], p["task_scope"], p.get("mode"), p.get("capability_token"), p.get("folder_context"))
        if op == "ingress_check": return lock_ingress_check(p["payload"], p["task_scope"], p.get("mode"), p.get("task_id"))
        if op == "audit_query":   return lock_audit_query(p["reason_for_query"], p.get("limit", 50))
        if op == "setup_status":  return lock_setup_status(p.get("config_path", ""))
        if op == "setup":         return lock_setup_run(p.get("backend_spec", ""), p.get("audit_log_path", ""), p.get("skip_smoke_test", False), p.get("config_path", ""), p.get("accepted_by", ""), p.get("reason", ""))
    except KeyError as e:
        return {"error": f"op {op!r} missing param {e}"}
    return {"error": f"unknown op {op!r}"}


@mcp.tool()
def workspace_memory(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Memory facade: one tool over the folder's pair/triple memory (replaces 12
    pair*/workspace_query/workspace_remember/recent tools). workspace_memory(op="help") lists ops.

    op: remember | query | search | recent | pairs_recent | pair | pair_spans |
        fetch_spans | safe_context | safe_context_query | layout_get | layout_set
    """
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        return {"ops": [
            {"op": "remember", "required": ["folder_context", "subject", "predicate", "object"]},
            {"op": "query", "required": ["folder_context"]},
            {"op": "search", "required": ["folder_context", "query"]},
            {"op": "recent", "required": ["folder_context"]},
            {"op": "pairs_recent", "required": ["folder_context"]},
            {"op": "pair", "required": ["folder_context", "pair_id"]},
            {"op": "pair_spans", "required": ["folder_context", "pair_id"]},
            {"op": "fetch_spans", "required": ["folder_context", "pair_ids"]},
            {"op": "safe_context", "required": ["folder_context", "pair_id"]},
            {"op": "safe_context_query", "required": ["folder_context", "query"]},
            {"op": "layout_get", "required": ["folder_context"]},
            {"op": "layout_set", "required": ["pair_id", "folder_context", "x", "y"]},
            {"op": "reason", "required": ["folder_context"]},
        ]}
    try:
        if op == "remember":           return workspace_remember(p["folder_context"], p["subject"], p["predicate"], p["object"], p.get("dimension", ""), p.get("confidence", 1.0), p.get("source", ""))
        if op == "query":              return workspace_query(p["folder_context"], p.get("subject", ""), p.get("predicate", ""), p.get("object", ""), p.get("limit", 50))
        if op == "search":             return pairs_search(p["folder_context"], p["query"], p.get("k", 5))
        if op == "recent":             return recent(p["folder_context"], p.get("limit", 20))
        if op == "pairs_recent":       return pairs_recent(p["folder_context"], p.get("limit", 20))
        if op == "pair":               return pair_by_id(p["folder_context"], p["pair_id"])
        if op == "pair_spans":         return pair_spans(p["folder_context"], p["pair_id"], p.get("span_count", 5))
        if op == "fetch_spans":        return fetch_pair_spans(p["folder_context"], p["pair_ids"], p.get("mode", "lock_default"))
        if op == "safe_context":       return pair_safe_context(p["folder_context"], p["pair_id"], p.get("mode", "auto"))
        if op == "safe_context_query": return pairs_safe_context_for_query(p["folder_context"], p["query"], p.get("k", 8), p.get("mode", "auto"), p.get("source_paths"))
        if op == "layout_get":         return get_pair_layouts(p["folder_context"])
        if op == "layout_set":         return set_pair_layout(p["pair_id"], p["folder_context"], p["x"], p["y"])
        if op == "reason":             return reason(p["folder_context"], p.get("start", ""), p.get("max_depth", 3), p.get("min_confidence", 0.0), p.get("max_results", 50), p.get("record", True))
    except KeyError as e:
        return {"error": f"op {op!r} missing param {e}"}
    return {"error": f"unknown op {op!r}"}


@mcp.tool()
def workspace_dispatch(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Skill dispatch + resolution + pinning facade, plus the decision surface
    (escalation options assembled, the originated choice recorded).
    workspace_dispatch(op="help") lists the operations.

    op: dispatch | dispatch_batch | dispatch_ingested | dry_run | resolve |
        suggest | pin | pin_many | unpin | list_pinned | list_plugin |
        recent | decision_build | decision_open | decision_pending |
        decision_dossier | decision_claim | decision_release | decision_link_mint |
        decision_notify | decision_reconfirm_request | decision_record
    """
    p = params or {}
    if op in ("help", "ops", "catalogue"):
        return {"ops": [
            {"op": "dispatch", "required": ["folder_context", "skill_id"]},
            {"op": "dispatch_batch", "required": ["skill_ids", "folder_context"]},
            {"op": "dispatch_ingested", "required": ["folder_context", "skill_id"]},
            {"op": "dry_run", "required": ["folder_context", "skill_id"]},
            {"op": "resolve", "required": ["folder_context"]},
            {"op": "suggest", "required": ["skill_id"]},
            {"op": "pin", "required": ["folder_context", "skill_id"]},
            {"op": "pin_many", "required": ["folder_context", "skill_ids"]},
            {"op": "unpin", "required": ["folder_context", "skill_id"]},
            {"op": "list_pinned", "required": ["folder_context"]},
            {"op": "list_plugin", "required": ["plugin_id"]},
            {"op": "recent", "required": ["folder_context"]},
            {"op": "decision_build", "required": ["query", "candidates"],
             "optional": ["esc_reason", "context"],
             "note": "pure: assemble a decision surface (>=1 defensible reading; single-reading warning passes through); grounding leaves banded (thin/moderate/firm), never as a score"},
            {"op": "decision_open", "required": ["folder_context", "surface", "raised_by"],
             "optional": ["competence", "claim_ttl_s", "escalate_to", "escalate_after_s", "write_reconfirm", "auto_notify", "idempotency_key", "priority", "decide_by", "panel"],
             "note": "persist an escalation as a pending decision — competence + raising actor recorded; returns the minimised, Lock-gated notification a transport may carry (title + deep link, never the question)"},
            {"op": "decision_pending", "required": ["folder_context"],
             "optional": ["for_party"],
             "note": "read-only routing state: open decisions, claims + leases; for_party narrows to what that party may claim (competence via the resolver's roster, never their own escalation)"},
            {"op": "decision_dossier", "required": ["folder_context", "decision_id"],
             "note": "read-only local join for one pending decision: the stored surface with banded grounding, the raiser's runs and standing (joined by raised_by, labelled attributed), the recourse ladder; panel seats stay sealed pre-resolution — counts and commitments only; unknown or closed ids refuse"},
            {"op": "decision_claim", "required": ["folder_context"],
             "optional": ["decision_id", "actor", "link_token"],
             "note": "first claim leases the decision (TTL) so two reviewers cannot decide the same card; expiry widens it back; the raiser cannot claim; recorded. With link_token the claimant is the token's bound party and the claim records its rung"},
            {"op": "decision_notify", "required": ["folder_context", "decision_id"],
             "optional": ["actor"],
             "note": "deliver (or re-deliver after an escalation) the minimised notification + personal action links to every holder's channels through the Lock-gated outbox; every per-channel result recorded, failures included"},
            {"op": "decision_reconfirm_request", "required": ["folder_context", "link_token"],
             "note": "mint the write-confirmation code for a link-authenticated reviewer and send it to THEIR channels only — a forwarded link can request a code it will never see"},
            {"op": "decision_link_mint", "required": ["folder_context", "decision_id", "party_id"],
             "optional": ["ttl_s", "actor"],
             "note": "signed single-use action link binding one party to one decision — the registered channel is the credential; dies on use, expiry, or a competing claim; never minted for the raiser"},
            {"op": "decision_release", "required": ["folder_context", "decision_id", "actor"],
             "note": "release a claim you hold — recorded"},
            {"op": "decision_record", "required": ["folder_context", "chosen_option_id", "rationale", "actor"],
             "optional": ["surface", "decision_id", "link_token", "reconfirm_code", "considered", "asked", "evidence_refs"],
             "note": "the one governed write: originated choice on the signed chain — real option + non-empty rationale + named actor; considered records only what was opened, never all"},
        ]}
    _DISPATCH_IMPLS = {"dispatch": dispatch_skill, "dispatch_batch": dispatch_skills_batch,
                       "dispatch_ingested": dispatch_ingested, "dry_run": dispatch_skill_dry_run,
                       "resolve": resolve_skills_for_query, "suggest": suggest_companion_skills,
                       "pin": pin_skill_to_folder, "pin_many": pin_skills_to_folder,
                       "unpin": unpin_skill_from_folder, "list_pinned": list_pinned_skills,
                       "list_plugin": list_plugin_skills, "recent": recent_dispatches,
                       "decision_build": decision_build, "decision_open": decision_open,
                       "decision_pending": decision_pending, "decision_dossier": decision_dossier,
                       "decision_claim": decision_claim,
                       "decision_release": decision_release, "decision_link_mint": decision_link_mint,
                       "decision_notify": decision_notify,
                       "decision_reconfirm_request": decision_reconfirm_request,
                       "decision_record": decision_record}
    _impl = _DISPATCH_IMPLS.get(op)
    if _impl is not None:
        from .mcp_serving import apply_principal_to_params
        _refused = apply_principal_to_params(_impl, p)
        if _refused is not None:
            return _refused
    try:
        if op == "dispatch":          return dispatch_skill(p["folder_context"], p["skill_id"], p.get("query", ""), p.get("chosen_via", "user"))
        if op == "dispatch_batch":    return dispatch_skills_batch(p["skill_ids"], p["folder_context"], p.get("query", ""), p.get("chosen_via", "user"))
        if op == "dispatch_ingested": return dispatch_ingested(p["folder_context"], p["skill_id"], p.get("query", ""), p.get("chosen_via", "user"))
        if op == "dry_run":           return dispatch_skill_dry_run(p["folder_context"], p["skill_id"], p.get("query", ""), p.get("chosen_via", "user"))
        if op == "resolve":           return resolve_skills_for_query(p["folder_context"], p.get("query", ""), p.get("include_ancestors", True))
        if op == "suggest":           return suggest_companion_skills(p["skill_id"], p.get("folder_context", ""))
        if op == "pin":               return pin_skill_to_folder(p["folder_context"], p["skill_id"], p.get("pinned_by", ""), p.get("note", ""))
        if op == "pin_many":          return pin_skills_to_folder(p["folder_context"], p["skill_ids"], p.get("pinned_by", ""), p.get("note", ""))
        if op == "unpin":             return unpin_skill_from_folder(p["folder_context"], p["skill_id"])
        if op == "list_pinned":       return list_pinned_skills(p["folder_context"])
        if op == "list_plugin":       return list_plugin_skills(p["plugin_id"])
        if op == "recent":            return recent_dispatches(p["folder_context"], p.get("limit", 50), p.get("include_workflows", True), p.get("scope", "self"))
        if op == "decision_build":    return decision_build(p["query"], p["candidates"], p.get("esc_reason", ""), p.get("context", ""))
        if op == "decision_open":     return decision_open(p["folder_context"], p["surface"], p["raised_by"], p.get("competence", ""), p.get("claim_ttl_s", 14400), p.get("escalate_to", ""), p.get("escalate_after_s", 0), p.get("write_reconfirm", False), p.get("auto_notify", True), p.get("idempotency_key", ""), p.get("priority", ""), p.get("decide_by", ""), p.get("panel"))
        if op == "decision_pending":  return decision_pending(p["folder_context"], p.get("for_party", ""))
        if op == "decision_dossier":  return decision_dossier(p["folder_context"], p["decision_id"])
        if op == "decision_claim":    return decision_claim(p["folder_context"], p.get("decision_id", ""), p.get("actor", ""), p.get("link_token", ""))
        if op == "decision_link_mint": return decision_link_mint(p["folder_context"], p["decision_id"], p["party_id"], p.get("ttl_s", 86400), p.get("actor", "system"))
        if op == "decision_notify":   return decision_notify(p["folder_context"], p["decision_id"], p.get("actor", "system"))
        if op == "decision_reconfirm_request": return decision_reconfirm_request(p["folder_context"], p["link_token"])
        if op == "decision_release":  return decision_release(p["folder_context"], p["decision_id"], p["actor"])
        if op == "decision_record":   return decision_record(p["folder_context"], p.get("surface"), p["chosen_option_id"], p["rationale"], p.get("actor", ""), p.get("considered"), p.get("asked"), p.get("evidence_refs"), p.get("decision_id", ""), p.get("link_token", ""), p.get("reconfirm_code", ""))
    except KeyError as e:
        return {"error": f"op {op!r} missing param {e}"}
    return {"error": f"unknown op {op!r}"}




@mcp.tool()
def workspace_ingest(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """workspace_ingest facade: one tool over 7 operations (stem + assemble_work
    added 2026-06-12, concept § 1.7 stem provenance). workspace_ingest(op="help")
    lists them."""
    from .stem_provenance import assemble_work, ingest_stem
    return _op_call(op, {"path": ingest_path, "url": ingest_url, "skill": ingest_skill, "list_urls": list_urls, "import_plugin": import_plugin, "stem": ingest_stem, "assemble_work": assemble_work}, params or {})


# --- contract-execution stack ops -------------------------------------------













@mcp.tool()
def workspace_contract(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """workspace_contract facade: one tool over 12 operations — reviews/approvals
    plus the contract-execution stack (machine-readable intake, obligation
    runtime, workbench seam). workspace_contract(op="help") lists them.

    op: review | list_reviews | request_approval | record_approval |
        list_approvals | ingest | state | obligations | tick | apply |
        resolve | demo.
    """
    return _op_call(op, {"review": record_contract_review, "list_reviews": list_contract_reviews, "request_approval": request_contract_approval, "record_approval": record_contract_approval, "list_approvals": list_contract_approvals,
                         "ingest": contract_ingest, "state": contract_state,
                         "obligations": contract_obligations,
                         "tick": contract_tick, "apply": contract_apply,
                         "resolve": contract_resolve, "demo": contract_demo}, params or {})


@mcp.tool()
def workspace_erase(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """workspace_erase facade: one tool over 4 operations. workspace_erase(op="help") lists them."""
    return _op_call(op, {"request": erase_request, "status": erase_status, "subject": erase_subject, "sweep": erase_sweep}, params or {})


@mcp.tool()
def workspace_workspace(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """workspace_workspace facade: one tool over 4 operations. workspace_workspace(op="help") lists them."""
    _table = {"add": add_known_workspace, "remove": remove_known_workspace, "list": list_known_workspaces, "bootstrap": bootstrap_default_workspace, "route": route_to_workspace}
    p = params or {}
    if op not in ("help", "ops", "catalogue"):
        _missing = _require_op_params(_facade_required_from_table(_table), op, p, facade="workspace_workspace")
        if _missing is not None:
            return _missing
    return _op_call(op, _table, p)


def audit_tail(folder_context: str, limit: int = 30) -> dict[str, Any]:
    """Recent signed events from the folder's mutation log — the read-only feed
    behind the Live Audit Ticker ("where did my change go?").

    Unlike ``verify_chain``, this does not self-log: a ticker polls it, and a
    read that grew the chain on every poll would add noise. Returns the
    last ``limit`` events, oldest-first, each carrying its signed fields so the
    client can render the verdict lamp + jump to the record by ``audit_id``.
    """
    import time as _t
    from .mutation_log import MutationLog

    resolved = str(Path(folder_context).expanduser().resolve())
    log = MutationLog(resolved, log_root=_log_root())
    try:
        events = list(log.replay())
    except Exception as e:  # unreadable/corrupt log → surface, never fake a clean feed
        return {"error": f"could not read log: {e}", "folder_context": resolved, "events": []}
    # Bound the response: clamp to a sane maximum so a caller cannot force the
    # whole log into one response (limit is a tail size, not a cursor).
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 30
    if n > 0:
        events = events[-min(n, 500):]
    out = []
    for evt in events:
        ex = evt.extra or {}
        out.append({
            "ts": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(evt.ts)),
            "event": evt.event,
            "state": evt.lifecycle_state or "",
            "channel": evt.channel,
            "actor": evt.actor,
            "pair_id": evt.pair_id,
            "audit_id": evt.audit_id,
            "signed": bool(evt.signature),
            "verdict": ex.get("verdict") or ex.get("gate_verdict") or "",
            "grade_ceiling": ex.get("grade_ceiling", ""),
            "kind": ex.get("kind", ""),
        })
    return {"folder_context": resolved, "count": len(out), "events": out}


@mcp.tool()
def workspace_audit(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """workspace_audit facade: chain + discipline audits plus the solver-graph
    governance guards. workspace_audit(op="help") lists them.

    solver guards (problem-solution graph): completeness (open-world
    detection residual + negative space), variety (Ashby requisite-variety
    coverage), accountability (essential vs arbitrary variety)."""
    from .completeness import completeness_report
    from .variety import variety_check
    from .accountability import account_check
    from .oversight_leverage import sampling_adequacy
    from .calibration import calibration_report, log_reuse, judge_sample
    from .reservation import reserved_acts_for
    from .review_card import overrides_for, recurrence_flags, record_override
    from .step_contract import derive_contract
    return _op_call(op, {"verify_chain": audit_verify_chain,
                         "tail": audit_tail,
                         "record_override": record_override,
                         "discipline": discipline_audit,
                         "get_event": get_audit_event,
                         "shadow_scan": workspace_shadow_scan,
                         "completeness": completeness_report,
                         "variety": variety_check,
                         "accountability": account_check,
                         "sampling": sampling_adequacy,
                         "calibration": calibration_report,
                         "log_reuse": log_reuse,
                         "judge_sample": judge_sample,
                         "reserved_acts": reserved_acts_for,
                         "overrides": overrides_for,
                         "override_recurrence": recurrence_flags,
                         "step_contract": derive_contract}, params or {})


@mcp.tool()
def workspace_model(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """workspace_model facade: one tool over 9 operations — transport, cascade,
    runtime status, and the attestation battery (attest_baseline / attest_run /
    attest_admit are governed and recorded; attest_status is read-only).
    workspace_model(op="help") lists them."""
    return _op_call(op, {"complete": local_llm_complete, "classify": local_llm_classify, "list": local_llm_list_available, "cascade": workspace_cascade, "status": model_runtime_status,
                         "attest_baseline": model_attest_baseline, "attest_run": model_attest_run,
                         "attest_admit": model_attest_admit, "attest_status": model_attest_status}, params or {})


@mcp.tool()
def workspace_capture(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """workspace_capture facade: one tool over 3 operations (llm/web write the audit
    floor; read is the capture ledger — what left vs stayed). workspace_capture(op="help") lists them."""
    return _op_call(op, {"llm": capture_llm, "web": capture_web, "read": capture_read}, params or {})


# ---------------------------------------------------------------------------
# Workspace Grounder — attribution boundary (no citation, no claim)
# ---------------------------------------------------------------------------




























@mcp.tool()
def workspace_grounder(op: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Workspace Grounder facade: one tool over the attribution boundary — no
    citation, no claim. workspace_grounder(op="help") lists the operations.

    op: ground | work.register | claim.status | claim.check | provenance.add |
        provenance.trace | swarm.frontier | bibliography | coverage |
        entities.link | subject.forget | creators.classify | source.ingest
        (or "help").
    """
    return _op_call(op, {
        "ground": grounder_ground,
        "work.register": grounder_register_work,
        "claim.status": grounder_claim_status,
        "provenance.add": grounder_add_provenance,
        "provenance.trace": grounder_trace,
        "swarm.frontier": grounder_frontier,
        "bibliography": grounder_bibliography,
        "coverage": grounder_coverage,
        "oversight.feed": grounder_oversight_feed,
        "entities.link": grounder_link_entities,
        "subject.forget": grounder_forget_subject,
        "claim.check": grounder_check_claim,
        "creators.classify": grounder_classify_creators,
        "source.ingest": grounder_ingest_source,
    }, params or {})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the MCP server over stdio.

    ``--help`` / ``--version`` answer immediately instead of starting the
    stdio server (doctor's reachability probe runs ``workspaces-mcp --help``
    with a timeout; serving on stdio made that probe hang).
    """
    import sys as _sys
    if any(a in ("--help", "-h", "--version") for a in _sys.argv[1:]):
        try:
            from importlib.metadata import version
            v = version("rvnd")
        except Exception:
            v = "unknown"
        print(f"workspaces-mcp {v} — Workspace MCP server (stdio transport). "
              f"{len(_DECLARED_TOOLS)} declared tools. Run with no "
              f"arguments from an MCP client config; this binary serves "
              f"a session over stdin/stdout.")
        return
    # Server-level presence: record this connection (post-handshake) so the agent
    # shows as CONNECTED independent of any workspace, and deregister on disconnect.
    # Identity comes from how the agent was registered (connect-agent-hub passes
    # RVND_AGENT); no folder is involved.
    import os as _os

    from .connected_agents import deregister_connection, register_connection
    _connid = register_connection(
        agent=(_os.environ.get("RVND_AGENT") or _os.environ.get("RVND_AGENT_NAME") or ""),
        transport="stdio",
        # The host session id (this stdio process inherits the client's env) is
        # the join key to the signed chain — the actor the PreToolUse hook
        # records IS this same id. Absent when the host sets none; never faked.
        session_id=(_os.environ.get("CLAUDE_CODE_SESSION_ID") or ""))
    try:
        mcp.run()
    finally:
        deregister_connection(_connid)


if __name__ == "__main__":
    main()
