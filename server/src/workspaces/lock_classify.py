# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Lock classification at ingest time.

Runs Privacy Lock over a pair's string-valued slots as the pair is being
ingested, persists the classification + a pre-scrubbed ``clean`` block on
the pair so that subsequent safe-context queries can read pre-computed
data instead of re-scanning.

Clean-Team architecture, ingest side:

    raw text (from extractor)
        ↓
    NDs / rule extractor produce triples + fingerprint + facets
        ↓
    lock_classify_pair() ← runs ONCE here
        ↓
    pair.lock = {classified_at, audit, total_findings, ...}
    pair.clean = {fingerprint (scrubbed), triples (scrubbed), ...}
        ↓
    persisted to mutation log

Query-time path still re-runs lock as a defense check in case rules
have tightened since ingest. If the cached classification is older than
the current lock disclaimer version, the cached scrub is treated as
suspect and a live re-scrub is done.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional


LOCK_DISCLAIMER_VERSION = "v1"   # bump when lock rules change


def _lock_string(text: str, context: str = "") -> dict[str, Any]:
    """One-shot wrapper around workspaces.lock.lock_text."""
    if not text:
        return {"action": "allow", "text": "", "findings": 0}
    try:
        from workspaces.lock import lock_text, Mode
        decision = lock_text(text, context=context, mode=Mode.STANDARD,
                               source="triple")
        if decision.action == "allow":
            return {"action": "allow", "text": text,
                    "findings": len(decision.findings)}
        if decision.action == "minimise":
            return {"action": "minimise",
                    "text": decision.redacted_text or "",
                    "findings": len(decision.findings)}
        return {"action": "refuse",
                "text": "[LOCK-REFUSED]",
                "findings": len(decision.findings),
                "reason": decision.reason}
    except Exception as e:
        return {"action": "refuse",
                "text": "[LOCK-UNAVAILABLE]",
                "findings": 0,
                "reason": f"lock_text unavailable: {type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Per-pair classification
# ---------------------------------------------------------------------------

# Facet keys the safe-context surface considers structural (taxonomy). Other
# keys with string values get lock-scrubbed.
_SAFE_FACET_KEYS = {
    "domain", "subject", "modal", "modal_phrase", "language",
    "has_condition", "has_exception", "primary_type",
}


def _doc_token_fn(folder_path: str) -> Callable[[Optional[str]], str]:
    """Bind a per-workspace salted doc-token function for this folder."""
    try:
        from .workspace_identity import opaque_doc_token
    except ImportError:
        def fallback(s: Optional[str]) -> str:
            return "<DOC_NONE>" if not s else "<DOC_unknown>"
        return fallback
    def fn(src: Optional[str]) -> str:
        if not src:
            return "<DOC_NONE>"
        try:
            return opaque_doc_token(src, folder_path=folder_path)
        except Exception:
            return "<DOC_unknown>"
    return fn


def lock_classify_pair(pair: dict[str, Any],
                         folder_context: str,
                         *,
                         context: str = "") -> dict[str, Any]:
    """Return a NEW pair dict enriched with ``lock`` + ``clean`` blocks.

    Does not mutate the input. The returned pair has:

    - All original fields (problem, solution, id) preserved unchanged —
      this is the dirty side; raw bodies live here.
    - ``lock``: audit metadata describing what lock did.
    - ``clean``: pre-scrubbed safe view (fingerprint + triples) ready to
      hand to the safe-context surface without re-scanning.

    The ``clean`` block is the canonical Clean-Team output. The dirty
    side is local-only and never reaches the cloud LLM.
    """
    new_pair = dict(pair)
    problem = pair.get("problem") or {}
    solution = pair.get("solution") or {}
    facets = problem.get("facets") or {}
    doc_token = _doc_token_fn(folder_context)

    # ---------- Fingerprint (clean-side projection of taxonomy) ----------
    safe_facets_raw = {k: facets[k] for k in _SAFE_FACET_KEYS if k in facets}
    raw_summary = problem.get("summary") or ""
    ptype = problem.get("type") or ""
    if ptype == "document_ingest":
        src = problem.get("source_document") or raw_summary
        summary_raw = f"document {doc_token(src)}"
    else:
        summary_raw = raw_summary

    # Lock runs on summary + each string-valued facet
    summary_scrub = _lock_string(summary_raw, context=context)
    facets_scrub: dict[str, dict[str, Any]] = {}
    clean_facets: dict[str, Any] = {}
    facets_dropped = 0
    for k, v in safe_facets_raw.items():
        if isinstance(v, str) and v:
            r = _lock_string(v, context=context)
            facets_scrub[k] = r
            if r["action"] == "refuse":
                facets_dropped += 1
                continue
            clean_facets[k] = r["text"]
        else:
            clean_facets[k] = v
            facets_scrub[k] = {"action": "allow", "findings": 0,
                               "type": type(v).__name__}

    clean_fingerprint = {
        "scope":          problem.get("scope") or "",
        "type":           ptype,
        "summary":        summary_scrub["text"]
                          if summary_scrub["action"] != "refuse"
                          else "[LOCK-REFUSED]",
        "facets":         clean_facets,
        "authority_tier": solution.get("authority_tier"),
        "confidence":     solution.get("confidence"),
        "body_format":    solution.get("body_format"),
    }

    # ---------- Triples (clean-side, every object value scrubbed) --------
    # Includes the M1 mental-model fields: structured solution slots like
    # term / defined_as / ref_number / regulation / summary_excerpt
    # become triples directly, so the cloud LLM sees the knowledge
    # without ever seeing the document body.
    pid = pair.get("id") or "?"
    context = problem.get("context") or {}
    raw_triples: list[list[Any]] = []
    if problem.get("kind"):  raw_triples.append([pid, "kind",  problem.get("kind")])
    if problem.get("scope"): raw_triples.append([pid, "scope", problem.get("scope")])
    if ptype:                raw_triples.append([pid, "type",  ptype])
    # Taxonomy + M1 structural facets
    for k in ("domain", "subject", "modal", "modal_phrase", "language",
              "has_condition", "has_exception", "primary_type",
              "term", "ref_kind", "ref_number", "doc_kind", "doc_id"):
        if k in facets:
            raw_triples.append([pid, k, facets[k]])
    # Context block
    for k in ("regulation", "kind_of_model", "doc_kind"):
        if k in context:
            raw_triples.append([pid, k, context[k]])
    for list_key in ("domains", "jurisdictions", "actors"):
        for v in (context.get(list_key) or []):
            raw_triples.append([pid, list_key + "_includes", v])
    # M1 structured solution fields — these ARE the mental-model knowledge
    for k in ("term", "defined_as", "ref_kind", "ref_number", "regulation",
              "doc_kind", "doc_id", "summary_excerpt"):
        if k in solution and solution[k] not in (None, ""):
            raw_triples.append([pid, k, solution[k]])
    if solution.get("authority_tier") is not None:
        raw_triples.append([pid, "authority_tier", solution["authority_tier"]])
    src = problem.get("source_document")
    if src:
        raw_triples.append([pid, "source_document", doc_token(src)])

    clean_triples: list[list[Any]] = []
    triples_dropped = 0
    triple_audit: list[dict[str, Any]] = []
    total_findings = summary_scrub.get("findings", 0)
    for s, p, o in raw_triples:
        if isinstance(o, str) and o:
            r = _lock_string(o, context=context)
            total_findings += r.get("findings", 0) or 0
            if r["action"] == "refuse":
                triples_dropped += 1
                triple_audit.append({"predicate": p, "action": "refuse",
                                     "reason": r.get("reason", "")})
                continue
            clean_triples.append([s, p, r["text"]])
            if r["action"] == "minimise":
                triple_audit.append({"predicate": p, "action": "minimise",
                                     "findings": r["findings"]})
        else:
            clean_triples.append([s, p, o])

    # ---------- Body classification (no clean-side output; audit only) ---
    body = solution.get("body")
    body_scrub_summary: dict[str, Any] = {"action": "n/a", "findings": 0}
    if isinstance(body, str) and body:
        # Body is dirty-side data; we don't emit it on the clean side.
        # Lock still runs so we have an audit number we can show to the
        # user ("this pair's body has N PII findings — careful when
        # exporting locally").
        r = _lock_string(body, context=context)
        body_scrub_summary = {"action": r["action"],
                              "findings": r.get("findings", 0)}
        total_findings += r.get("findings", 0) or 0
    # ---------- Assemble lock + clean blocks ---------------------------
    new_pair["lock"] = {
        "classified_at":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "disclaimer_version": LOCK_DISCLAIMER_VERSION,
        "total_findings":     total_findings,
        "triples_dropped":    triples_dropped,
        "facets_dropped":     facets_dropped,
        "audit_triples":      triple_audit,
        "audit_summary":      {"action": summary_scrub["action"],
                               "findings": summary_scrub.get("findings", 0)},
        "audit_body":         body_scrub_summary,
    }
    new_pair["clean"] = {
        "fingerprint": clean_fingerprint,
        "triples":     clean_triples,
    }
    return new_pair


def reclassify_all_pairs(folder_context: str,
                         *,
                         log_root: str | None = None,
                         actor: str = "lock-reclassify") -> dict[str, Any]:
    """Re-classify every pair in a folder. Used when lock rules change.

    Walks WorkspaceMemory, runs lock_classify_pair on each pair, writes the
    enriched version back via remember(). The mutation log keeps the old
    versions; most-recent-state-wins gives you the new ones.
    """
    from .memory import WorkspaceMemory
    mem = WorkspaceMemory(folder_context, log_root=log_root, actor=actor)
    pairs = mem.all_pairs()
    n_updated = 0
    n_skipped = 0
    for p in pairs:
        existing = p.get("lock") or {}
        if existing.get("disclaimer_version") == LOCK_DISCLAIMER_VERSION:
            n_skipped += 1
            continue
        enriched = lock_classify_pair(p, folder_context)
        mem.remember(enriched, channel="system",
                     source_hash=p.get("id", "").replace("sha256:", ""))
        n_updated += 1
    return {
        "ok": True,
        "folder_context": folder_context,
        "pairs_total": len(pairs),
        "pairs_reclassified": n_updated,
        "pairs_already_current": n_skipped,
        "disclaimer_version": LOCK_DISCLAIMER_VERSION,
    }
