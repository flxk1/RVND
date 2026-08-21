# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Grounder workbench I/O — export a folder's grounding state for the
workspace-grounder.html artifact (same seam pattern as ``workbench_io`` for the
contracts workbench: the artifact renders JSON, this module produces it from
the real ledger).

Read-only by design (v1): the workbench *shows* the attribution boundary —
bibliography in every style, claims by status with disputed residuals
front-and-centre, the provenance graph with its frontier, and the
honor-creators coverage report. Mutations stay with the MCP ops / oversight
surfaces; a view never resolves a residual.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .workspace_grounder import (
    CITATION_STYLES,
    GroundingLedger,
    format_citation,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def export_state(folder: str | Path, *,
                 log_root: Optional[str | Path] = None) -> dict[str, Any]:
    """Everything the 5-view workbench needs, in one JSON-safe dict."""
    ledger = GroundingLedger(folder, log_root=log_root)
    cov = ledger.coverage()
    frontier = {r["id"] for r in ledger.frontier()["frontier"]}

    works = []
    for w in ledger.works.values():
        citations = {}
        for style in CITATION_STYLES:
            try:
                citations[style] = format_citation(w, style)
            except Exception:                               # noqa: BLE001
                citations[style] = ""
        idents = w.get("identifiers", {}) or {}
        works.append({
            "id": w["id"], "title": w.get("title", ""),
            "type": w.get("type", ""),
            "creators": [c.get("name", "") for c in w.get("creators", [])],
            "creator_roles": {c.get("name", ""): c.get("role", "")
                              for c in w.get("creators", [])},
            "date": w.get("date", ""), "url": w.get("url", ""),
            "doi": w.get("doi", ""),
            "container": w.get("container", ""),
            "publisher": w.get("publisher", ""),
            "retrieved_by": w.get("retrieved_by", ""),
            "fixity": bool(idents.get("sha256") or idents.get("archive")),
            "creator_erased": bool(w.get("creator_erased")),
            "entity_refs": w.get("entity_refs", []),
            "on_frontier": w["id"] in frontier,
            "citations": citations,
        })
    works.sort(key=lambda r: r["title"].lower())

    titles = {w["id"]: w.get("title", "") for w in ledger.works.values()}
    claims = []
    for c in ledger.claims.values():
        claims.append({
            "id": c["id"], "text": c.get("text", ""),
            "status": c.get("status", ""),
            "confidence": c.get("confidence", 0.0),
            "method": c.get("method", ""), "agent": c.get("agent", ""),
            "works": [{"id": wid, "title": titles.get(wid, "?")}
                      for wid in c.get("work_ids", [])],
            "has_evidence": bool(c.get("quote") or c.get("locator")),
            "quote": (c.get("quote") or "")[:300],
            "locator": c.get("locator", ""),
            "verified_by": c.get("verified_by", []),
            "evidence_at_promotion": c.get("evidence_at_promotion"),
            "support_check": c.get("support_check"),
        })
    order = {"disputed": 0, "asserted": 1, "verified": 2, "retracted": 3}
    claims.sort(key=lambda r: (order.get(r["status"], 9), r["text"].lower()))

    edges = [{"from": e["from"], "from_title": titles.get(e["from"], "?"),
              "relation": e["relation"],
              "to": e["to"], "to_title": titles.get(e["to"], "?"),
              "evidence": e.get("evidence", ""), "basis": e.get("basis", "")}
             for e in ledger.provenance.values()]

    audit = []
    try:
        from .mutation_log import MutationLog
        log = MutationLog(ledger.folder, log_root=ledger.log_root)
        for evt in log.replay():
            kind = (evt.extra or {}).get("kind", "")
            if kind.startswith("grounding"):
                audit.append({"ts": evt.ts, "event": evt.event, "kind": kind,
                              "ref": evt.pair_id,
                              "actor": evt.actor,
                              "audit_id": evt.audit_id})
    except Exception:                                       # noqa: BLE001
        pass
    audit = audit[-200:]

    return {"meta": {"folder": str(Path(folder)), "generated": _now(),
                     "styles": list(CITATION_STYLES),
                     "tool": "workspace-grounder", "schema": 1},
            "works": works, "claims": claims,
            "provenance": {"edges": edges,
                           "frontier": sorted(frontier),
                           "traced": len(ledger.works) - len(frontier)},
            "coverage": cov,
            "audit": audit}


def write_state(folder: str | Path, out_path: str | Path, *,
                log_root: Optional[str | Path] = None) -> dict[str, Any]:
    state = export_state(folder, log_root=log_root)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return {"ok": True, "path": str(p), "works": len(state["works"]),
            "claims": len(state["claims"])}


def build_demo(folder: str | Path, *,
               log_root: Optional[str | Path] = None) -> dict[str, Any]:
    """A small honest demo: real pipeline calls, every claim state and every
    coverage signal represented at least once."""
    from .grounder_extract import ingest_source
    page = """<html><head>
<title>fallback</title>
<meta name="citation_title" content="Grounded Attribution for Agentic AI">
<meta name="citation_author" content="Doe, Jane">
<meta name="citation_author" content="Roe, Richard">
<meta name="citation_journal_title" content="Journal of AI Governance">
<meta name="citation_publisher" content="Open Governance Press">
<meta name="citation_publication_date" content="2026/01/15">
<meta name="citation_doi" content="10.1234/jaig.2026.042">
</head><body>
Building on transformers (arXiv:1706.03762) and earlier provenance work
(doi:10.5555/prov.2019.7), see also https://example.org/agentic-oversight.
</body></html>"""
    src = ingest_source(str(folder), page,
                        url="https://example.org/grounded-attribution",
                        retrieved_by="swarm",
                        log_root=str(log_root) if log_root else None)
    ledger = GroundingLedger(folder, log_root=log_root)
    main = src["work"]["id"]
    statute = ledger.register_work(
        title="Regulation (EU) 2024/1689 (AI Act)", type="statute",
        creators=[{"name": "European Parliament and Council", "role": "org"}],
        date="2024-07-12", url="https://eur-lex.europa.eu/eli/reg/2024/1689/oj")
    # verified (twin), with evidence
    a = ledger.ground_claim(
        "High-risk AI systems must allow effective human oversight.",
        [statute["id"]], method="twin", agent="twin-a",
        quote="can be effectively overseen by natural persons",
        locator="Art. 14(1)", confidence=0.9)
    ledger.ground_claim(
        "High-risk AI systems must allow effective human oversight.",
        [statute["id"]], method="twin", agent="twin-b")
    # disputed residual
    d = ledger.ground_claim(
        "The AI Act prohibits all biometric identification.",
        [statute["id"]], method="twin", agent="twin-a", confidence=0.4)
    ledger.set_claim_status(d["id"], "disputed", by="twin-b",
                            note="twin-b: prohibition is narrower (Art. 5)")
    # asserted without evidence (coverage gap on purpose)
    ledger.ground_claim("Grounded attribution improves auditability.",
                        [main], method="researcher", agent="researcher-1",
                        confidence=0.6)
    # an audited refusal (no citation, no claim)
    ledger.ground_claim("Ungrounded marketing claim.", [])
    ledger.link_creators_to_corpus()
    return {"ok": True, "folder": str(folder),
            "works": len(ledger.works), "claims": len(ledger.claims),
            "verified": a["id"], "disputed": d["id"]}
