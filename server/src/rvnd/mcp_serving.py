# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""MCP serving helpers — extracted from mcp_server.py.

One MCP server (workspaces-mcp) is the contract; the implementation lives in
the modules it belongs to. These are the lock/view/fingerprint helpers the
server's tools share. No FastMCP tools here — pure helpers.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any
from .identity import opaque_doc_token


# Defined in `principal` (its membership check needs it); re-exported here
# because mcp_impl resolves _log_root through this module attribute so that
# tests patching "rvnd.mcp_serving._log_root" reach every caller.
from .principal import _log_root  # noqa: F401  -- re-export


# Re-exported so `workspaces.mcp_serving.<name>` keeps working for existing
# importers and for tests that patch these by string path. The definitions
# live in `principal` -- a leaf module, so importing them cannot form a cycle.
from .principal import (  # noqa: F401  -- re-export
    CONSOLE_UNITS,
    _FOLDER_ADDRESSING_KEYS,
    _REMOTE_STORAGE_ROOT_KEYS,
    _ROLE_UNITS,
    _request_principal,
    apply_principal_to_params,
    clear_request_principal,
    get_request_principal,
    principal_member_filter,
    principal_workspace_member,
    set_request_principal,
    units_for_role,
)
from .policy import effective_policy

def _doc_token(source: str | None, folder_context: str | None = None) -> str:
    """Opaque, per-workspace salted token for a source-document path.

    Calls into ``workspace_identity.opaque_doc_token`` with the per-workspace salt.
    Same path → same token within a given workspace (cross-pair "shared source"
    joins still work), but a different workspace (or a different machine without
    the salt) computes different tokens.

    A path passed without ``folder_context`` falls back to a global-salt
    behaviour scoped to ``LOG_ROOT_DEFAULT`` (a single fallback salt for
    callers that don't know which workspace they're in).
    """
    if not source:
        return "<DOC_NONE>"
    target = folder_context if folder_context else str(Path.home())
    return opaque_doc_token(source, folder_path=target, log_root=_log_root())


def _fingerprint_of(pair: dict[str, Any],
                   folder_context: str | None = None) -> dict[str, Any]:
    """Return the safe taxonomy + structured facets — no content.

    Clean-Team architecture: this is what the cloud LLM sees. Document
    bodies never appear here. Filenames (which would leak counterparty
    names) get tokenized via the per-workspace salt.
    """
    problem = pair.get("problem") or {}
    solution = pair.get("solution") or {}
    facets = problem.get("facets") or {}
    # Only the keys we know are taxonomy-shaped; never write summary/body here.
    SAFE_FACET_KEYS = {
        "domain", "subject", "modal", "modal_phrase", "language",
        "has_condition", "has_exception", "primary_type",
    }
    safe_facets = {k: facets[k] for k in SAFE_FACET_KEYS if k in facets}
    # Summary handling: document_ingest pairs carry the filename as summary,
    # which would leak counterparty names. Replace with opaque doc-token.
    # ND-derived pairs (scope=gdpr/ai-act/music-rights/contracts, type=rule)
    # carry taxonomy summaries like "music-rights: obligation — the licensee"
    # which are safe.
    raw_summary = problem.get("summary") or ""
    ptype = problem.get("type") or ""
    if ptype == "document_ingest":
        # Summary is a filename. Tokenize.
        src = problem.get("source_document") or raw_summary
        safe_summary = f"document {_doc_token(src, folder_context)}"
    else:
        # ND-derived pair: taxonomy summary, safe.
        safe_summary = raw_summary
    return {
        "scope":            problem.get("scope") or "",
        "type":             ptype,
        "summary":          safe_summary,
        "facets":           safe_facets,
        "authority_tier":   solution.get("authority_tier"),
        "confidence":       solution.get("confidence"),
        "body_format":      solution.get("body_format"),
    }


def _triples_of(pair: dict[str, Any],
                folder_context: str | None = None) -> list[list[Any]]:
    """Derive subject-predicate-object triples from the pair's mental-model
    structure.

    Subject = pair id. Predicates name structural slots. Objects are:
    - Taxonomy values (scope, kind, modal, ref_kind, doc_kind, …)
    - Structured knowledge fields (term, defined_as, ref_number,
      regulation, summary_excerpt, …) — these ARE the mental model;
      they get lock-scrubbed at the output boundary.
    - Opaque doc-tokens for source filenames (per-workspace salted).

    NEVER includes raw document body. The body lives only on the dirty
    side. What reaches the LLM is taxonomy + structured knowledge fields
    + doc-tokens — enough to reason from, without exposing source text.
    """
    pid = pair.get("id") or "?"
    problem = pair.get("problem") or {}
    solution = pair.get("solution") or {}
    facets = problem.get("facets") or {}
    context = problem.get("context") or {}
    triples: list[list[Any]] = []
    # Top-level problem taxonomy
    if problem.get("kind"):
        triples.append([pid, "kind", problem.get("kind")])
    if problem.get("scope"):
        triples.append([pid, "scope", problem.get("scope")])
    if problem.get("type"):
        triples.append([pid, "type", problem.get("type")])
    # Facets — structural slots that describe the model
    for k in ("domain", "subject", "modal", "modal_phrase", "language",
              "has_condition", "has_exception", "primary_type",
              # M1 additions:
              "term", "ref_kind", "ref_number", "doc_kind", "doc_id"):
        if k in facets:
            triples.append([pid, k, facets[k]])
    # Context block — when this knowledge applies
    for k in ("regulation", "kind_of_model", "doc_kind"):
        if k in context:
            triples.append([pid, k, context[k]])
    # Domain lists in context (kept as separate triples per element so
    # the LLM can pattern-match individually)
    for list_key in ("domains", "jurisdictions", "actors"):
        for v in (context.get(list_key) or []):
            triples.append([pid, list_key + "_includes", v])
    # Structured solution fields — these ARE the mental-model content.
    # Knowledge atoms (a definition, a reference, a doc-summary), not
    # raw text. Lock scrubs each value at the output boundary.
    for k in ("term", "defined_as", "ref_kind", "ref_number",
              "regulation", "doc_kind", "doc_id", "summary_excerpt"):
        if k in solution and solution[k] not in (None, ""):
            triples.append([pid, k, solution[k]])
    if solution.get("authority_tier") is not None:
        triples.append([pid, "authority_tier", solution.get("authority_tier")])
    src = problem.get("source_document")
    if src:
        triples.append([pid, "source_document", _doc_token(src, folder_context)])
    return triples


def _lock_string(text: str, context: str = "") -> dict[str, Any]:
    """Pass a single string through lock_text(). Returns a transport dict.

    Best-effort: if the lock module is unavailable for any reason, falls
    back to a conservative refuse so the artifact doesn't leak.

    Used by ``_scrub_triple_object`` to clean PII from triple object values
    before they reach the cloud LLM.
    """
    if not text:
        return {"action": "allow", "text": "", "findings": 0}
    try:
        from rvnd.lock import lock_text, Mode
        decision = lock_text(text, context=context, mode=Mode.STANDARD,
                               source="triple")
        if decision.action == "allow":
            return {"action": "allow", "text": text,
                    "findings": len(decision.findings)}
        if decision.action == "minimise":
            return {"action": "minimise",
                    "text": decision.redacted_text or "",
                    "findings": len(decision.findings)}
        # refuse
        return {"action": "refuse",
                "text": "[LOCK-REFUSED]",
                "findings": len(decision.findings),
                "reason": decision.reason}
    except Exception as e:
        return {"action": "refuse",
                "text": "[LOCK-UNAVAILABLE]",
                "findings": 0,
                "reason": f"lock_text unavailable: {e}"}


def _scrub_triple_object(obj: Any) -> tuple[Any, dict[str, Any]]:
    """Run lock on a triple's object value if it's a string.

    Returns ``(scrubbed_value, audit)`` where audit records what lock did.
    Non-string objects (bool, int, taxonomy enum values) pass through
    unchanged with audit.action='allow'.

    Clean-Team architecture: this is the gate that keeps PII out of the
    KG. Any triple where the object is a free-form string gets checked.
    Subjects (always pair_id) and predicates (always taxonomy enum) are
    structural and never carry PII.
    """
    if not isinstance(obj, str):
        return obj, {"action": "allow", "findings": 0, "type": type(obj).__name__}
    # Empty taxonomy values pass through.
    if not obj.strip():
        return obj, {"action": "allow", "findings": 0, "type": "empty-str"}
    r = _lock_string(obj, context="")
    return r["text"], {
        "action":   r["action"],
        "findings": r["findings"],
        "type":     "str",
        "reason":   r.get("reason", ""),
    }


def _resolve_mode_for_folder(folder_context: str, requested: str) -> str:
    """Map the caller's mode-request through the folder's lock policy.

    requested ∈ { 'auto', 'fingerprint_only', 'triples_only', 'safe_minimal',
                  'safe_full' }
    - safe_minimal = fingerprint + triples (Clean-Team / no body)
    - safe_full    = fingerprint + triples + DOCUMENT BODY as context.
                     Only legal when the folder's lock is OFF — i.e.
                     the user has explicitly accepted that the cloud LLM
                     may see raw document content for this workspace.
    - 'auto' resolves from policy: lock ON → safe_minimal, lock OFF → safe_full.
    """
    if requested != "auto":
        return requested
    try:
        from .policy import load_policy
        pol = effective_policy(folder_context, log_root=_log_root())
        lock_on = bool(getattr(pol, "lock_is_active", True))
    except Exception:
        lock_on = True
    return "safe_minimal" if lock_on else "safe_full"


def _folder_lock_on(folder_context: str) -> bool:
    """Read the folder's lock policy. Defaults to True if unreadable.

    Used by _safe_view to enforce the rule that ``safe_full`` only emits
    a document body if the folder has explicitly disabled the lock.
    """
    try:
        from . import subject as _subject
        from .policy import resolve_policy
        # log_root matters: the deployment policy lives under the OPERATIVE log
        # root, so an operator running with --log-root must have their posture
        # read, not the default one. This is the same trap the A6 allowlist fell
        # into when it read the default registry regardless of --log-root.
        pol = resolve_policy(_subject.folder(folder_context), log_root=_log_root())
        return bool(getattr(pol, "lock_is_active", True))
    except Exception:
        return True


def _lock_gate_text(folder_context: str, text: str,
                      context: str = "") -> dict[str, Any]:
    """Gate a free-text payload (e.g. a skill body) through Privacy Lock,
    honouring the folder's policy.

    If the folder has Lock disabled, returns a passthrough allow. Otherwise
    runs ``lock_classify._lock_string`` and returns its verdict:
    ``{action: "allow"|"minimise"|"refuse", findings, text, reason?}``.
    Used at ingest (block storing a body that must not cross to a cloud LLM)
    and at dispatch (egress gate before the body leaves for the client model).
    """
    if not _folder_lock_on(folder_context):
        return {"action": "allow", "findings": 0, "text": text,
                "lock_active": False}
    try:
        from .lock_classify import _lock_string
        v = _lock_string(text, context=context)
        v["lock_active"] = True
        return v
    except Exception as e:
        # Fail safe: if the lock can't run while it's meant to be active,
        # refuse rather than leak.
        return {"action": "refuse", "findings": 0, "text": "[LOCK-UNAVAILABLE]",
                "reason": f"{type(e).__name__}: {e}", "lock_active": True}


def _folder_lock_mode(folder_context: str) -> str:
    """Return the tri-state lock mode for this folder.

    One of:
      "clean_room_with_algo" — privacy by default (state 3)
      "clean_room"           — HITL only (state 2)
      "off"                  — no guard, body crosses (state 1)
    """
    try:
        from . import subject as _subject
        from .policy import resolve_policy
        # log_root matters: the deployment policy lives under the OPERATIVE log
        # root, so an operator running with --log-root must have their posture
        # read, not the default one. This is the same trap the A6 allowlist fell
        # into when it read the default registry regardless of --log-root.
        pol = resolve_policy(_subject.folder(folder_context), log_root=_log_root())
        return pol.lock_mode
    except Exception:
        return "clean_room_with_algo"   # fail-safe default


def _lock_fingerprint(fp: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    """Run lock over fingerprint string values (summary + facet values).

    Returns ``(scrubbed_fp, findings, dropped_facets)``. Mutates a copy;
    does not touch the input dict.
    """
    out = dict(fp)
    findings = 0
    dropped = 0
    if isinstance(out.get("summary"), str) and out["summary"]:
        new_summary, info = _scrub_triple_object(out["summary"])
        findings += info.get("findings", 0) or 0
        out["summary"] = new_summary
    if isinstance(out.get("facets"), dict):
        safe_facets = {}
        for k, v in out["facets"].items():
            if isinstance(v, str) and v:
                new_v, info = _scrub_triple_object(v)
                findings += info.get("findings", 0) or 0
                if info["action"] == "refuse":
                    dropped += 1
                    continue  # drop facet entirely if refused
                safe_facets[k] = new_v
            else:
                safe_facets[k] = v
        out["facets"] = safe_facets
    return out, findings, dropped


def _rerank_by_dimension(pairs: list[dict[str, Any]], hint) -> list[dict[str, Any]]:
    """Promote pairs whose edges carry the hinted reasoning dimension.

    When a question leans toward a dimension ("why" -> causal, "what is it
    for" -> intentional, ...), pairs that have an edge in that dimension move
    ahead of those that don't, while keyword-relevance order is preserved
    within each group (stable sort). When ``hint`` is None the order is
    returned unchanged. This keeps the five-dimensional edges a quiet guide to
    better retrieval inside ordinary questions, not a separate query language.
    """
    if hint is None:
        return pairs
    want = hint.value

    def _has_dim(p: dict[str, Any]) -> bool:
        edges = p.get("edges")
        if not isinstance(edges, list):
            return False
        return any(isinstance(e, dict) and e.get("dimension") == want for e in edges)

    return sorted(pairs, key=lambda p: 0 if _has_dim(p) else 1)


def _safe_view(pair: dict[str, Any], folder_context: str, mode: str
              ) -> dict[str, Any]:
    """Build the safe-context view of a single pair.

    Two-phase lock architecture:

    1. **Ingest-time:** when the extractor produced the pair, lock ran
       over every string-valued slot (summary, facet values, triple
       objects, body). The result was persisted on the pair under
       ``lock`` (audit) and ``clean`` (pre-scrubbed safe view).

    2. **Query-time (here):** if the cached ``clean`` block is current
       against today's disclaimer version, use it directly. Otherwise
       re-run the lock as a defense layer. This catches the case
       where lock rules tightened after ingest — the cached scrub
       may be stale and we live-scrub instead.

    Either way the dirty side (pair.problem.summary, pair.solution.body)
    is never exposed to the cloud-bound caller. Only the clean side
    (fingerprint + triples) leaves this function.
    """
    from .lock_classify import LOCK_DISCLAIMER_VERSION
    cached_lock = pair.get("lock") or {}
    cached_clean  = pair.get("clean")  or {}
    cached_version = cached_lock.get("disclaimer_version")
    cache_is_current = (cached_version == LOCK_DISCLAIMER_VERSION
                        and isinstance(cached_clean, dict)
                        and "fingerprint" in cached_clean
                        and "triples" in cached_clean)

    if cache_is_current:
        # Fast path: pre-classified at ingest. Reuse the clean block.
        fingerprint = cached_clean.get("fingerprint") or {}
        triples     = cached_clean.get("triples") or []
        scrubbed_at = (f"ingest at {cached_lock.get('classified_at', '?')} "
                       f"(disclaimer={cached_version})")
        lock_audit = {
            "scrubbed_at":     scrubbed_at,
            "total_findings":  cached_lock.get("total_findings", 0),
            "triples_dropped": cached_lock.get("triples_dropped", 0),
            "facets_dropped":  cached_lock.get("facets_dropped", 0),
            "audit":           cached_lock.get("audit_triples", []),
            "from_cache":      True,
        }
    else:
        # Defense layer: legacy pair or stale cache → live-scrub now.
        raw_fp = _fingerprint_of(pair, folder_context=folder_context)
        fingerprint, fp_findings, fp_dropped = _lock_fingerprint(raw_fp)
        raw_triples = _triples_of(pair, folder_context=folder_context)
        scrubbed: list[list[Any]] = []
        total_findings = 0
        dropped = 0
        audit: list[dict[str, Any]] = []
        for s, p, o in raw_triples:
            new_o, info = _scrub_triple_object(o)
            total_findings += info.get("findings", 0) or 0
            if info["action"] == "refuse":
                dropped += 1
                audit.append({"predicate": p, "action": "refuse",
                              "reason": info.get("reason", "")})
                continue
            if info["action"] == "minimise":
                audit.append({"predicate": p, "action": "minimise",
                              "findings": info["findings"]})
            scrubbed.append([s, p, new_o])
        triples = scrubbed
        why_not_cached = (
            "no lock block on pair" if not cached_lock
            else f"stale (cached={cached_version}, current={LOCK_DISCLAIMER_VERSION})"
        )
        lock_audit = {
            "scrubbed_at":     f"query-time (defense layer; {why_not_cached})",
            "total_findings":  total_findings + fp_findings,
            "triples_dropped": dropped,
            "facets_dropped":  fp_dropped,
            "audit":           audit,
            "from_cache":      False,
        }

    out: dict[str, Any] = {
        "pair_id":      pair.get("id"),
        "mode_applied": mode,
        "fingerprint":  fingerprint,
        "triples":      triples,
        "lock":       lock_audit,
    }
    if mode == "fingerprint_only":
        out["triples"] = []
    if mode == "triples_only":
        out["fingerprint"] = {}

    # ---- Lock-OFF document-body inclusion -------------------------------
    # If the user has explicitly turned lock OFF for this folder AND the
    # caller asked for safe_full mode, include the document body as
    # additional context for the cloud LLM. The KG triples are the
    # precision layer; the body is the broad context the LLM grounds
    # against. Rule: lock-OFF folder = LLM
    # gets BOTH the rich KG and the document as context.
    #
    # Lock-ON folders NEVER see this path regardless of mode — that's
    # the Clean-Team architecture and it stays untouched.
    if mode == "safe_full":
        if not _folder_lock_on(folder_context):
            solution = pair.get("solution") or {}
            body = solution.get("body") or ""
            if isinstance(body, str) and body:
                out["document_context"] = {
                    "body": body,
                    "body_format": solution.get("body_format", "prose"),
                    "lock_off": True,
                    "notice": ("Folder has Privacy Lock OFF; document body "
                              "is included as additional context for the "
                              "cloud LLM. The user accepted the disclosure "
                              "risk when disabling the lock."),
                }
        else:
            # safe_full requested on a lock-ON folder → refuse the body
            # inclusion (Clean-Team rule). Surface the refusal so the
            # caller can decide how to react.
            out["document_context"] = {
                "body": None,
                "lock_off": False,
                "notice": ("Folder has Privacy Lock ON; safe_full mode "
                          "cannot include the document body. Disable the "
                          "lock for this folder (with accepted_by + "
                          "disclaimer) to allow body inclusion, or keep "
                          "lock ON and rely on the KG triples alone."),
            }
    return out


def _wrap_scanned(payload: dict[str, Any], views: list[dict[str, Any]] | None = None
                  ) -> dict[str, Any]:
    """Wrap a safe-context payload in a ScannedResponse, run the egress
    guard, return the transport dict.

    ``views`` (when provided) lets the audit count the aggregate lock
    findings across all the views in this response.
    """
    from rvnd.lock import (
        ScannedResponse, LockAudit, assert_scanned,
    )
    total = 0
    refused = 0
    minimised = 0
    cached = 0
    for v in views or []:
        s = v.get("lock") or {}
        total += int(s.get("total_findings", 0) or 0)
        refused += int(s.get("triples_dropped", 0) or 0)
        minimised += int(s.get("facets_dropped", 0) or 0)
        if s.get("from_cache") is True:
            cached += 1
    notes = (f"{cached}/{len(views)} views from cache" if views else "")
    response = ScannedResponse(
        value=payload,
        audit=LockAudit(
            tier="ingest-cached" if (views and cached == len(views)) else "A",
            total_findings=total,
            refused=refused,
            minimised=minimised,
            notes=notes,
        ),
    )
    assert_scanned(response)   # runtime guard — refuses anything else
    return response.to_mcp_payload()
