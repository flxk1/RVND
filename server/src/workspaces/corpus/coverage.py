# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Corpus read-coverage — Vollständigkeit at the INGESTION layer.

This module measures ingestion coverage: a corpus has 30 documents but the agent, under an
attention/context budget, actually reads only 23. The 7 unread documents are a
silent completeness failure — the Amtsermittlungsgrundsatz violated not by bad
ranking but by documents never attended to at all.

`requirements_house`/`evidence_coverage` answer a *different* question (which
obligations have evidence). This module answers: **were all N documents actually
read, and if not, which were not — surfaced, never dropped silently.**

The guarantee is a read-receipt ledger: every declared document is accounted for
as read / unread / skipped(with reason). `complete` is true only when nothing is
unread. A class-C (Verwaltungsakt) gate can refuse to proceed on an incomplete
corpus — a ranking may never stand in for having looked.

Pure stdlib; operates on ids the ingestion layer supplies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


@dataclass
class DocStatus:
    doc_id: str
    status: str            # "read" | "unread" | "skipped"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"doc_id": self.doc_id, "status": self.status, "reason": self.reason}


@dataclass
class CoverageReport:
    items: list[DocStatus] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def read(self) -> list[str]:
        return [d.doc_id for d in self.items if d.status == "read"]

    @property
    def unread(self) -> list[str]:
        return [d.doc_id for d in self.items if d.status == "unread"]

    @property
    def skipped(self) -> list[DocStatus]:
        return [d for d in self.items if d.status == "skipped"]

    @property
    def ratio(self) -> float:
        return (len(self.read) / self.total) if self.total else 1.0

    @property
    def complete(self) -> bool:
        """True only if no document is unread. A *skipped* doc is allowed only
        because it carries an explicit reason (a recorded decision, not a drop)."""
        return not self.unread

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "read": len(self.read),
                "unread": self.unread, "skipped": [d.to_dict() for d in self.skipped],
                "ratio": round(self.ratio, 4), "complete": self.complete}


def assess(declared_ids: Iterable[str], processed_ids: Iterable[str], *,
           skipped: Optional[dict[str, str]] = None) -> CoverageReport:
    """Account for every declared document.

    declared_ids — the full corpus the agent was given (all N).
    processed_ids — those actually read/extracted (the M that fit attention).
    skipped — {doc_id: reason} for documents deliberately not read (e.g. out of
        scope), which must carry a reason; anything neither processed nor
        explicitly skipped is **unread** (the silent-drop the essay warns about).
    """
    declared = list(dict.fromkeys(declared_ids))      # preserve order, dedupe
    processed = set(processed_ids)
    skipped = skipped or {}
    rep = CoverageReport()
    for doc_id in declared:
        if doc_id in processed:
            rep.items.append(DocStatus(doc_id, "read"))
        elif doc_id in skipped:
            rep.items.append(DocStatus(doc_id, "skipped", skipped[doc_id]))
        else:
            rep.items.append(DocStatus(doc_id, "unread",
                                       "in corpus but never read — attention/budget gap"))
    return rep


class CorpusIncomplete(Exception):
    def __init__(self, report: CoverageReport):
        self.report = report
        super().__init__(f"corpus not fully read: {len(report.unread)} unread "
                         f"of {report.total} — {report.unread}")


def require_full(report: CoverageReport) -> CoverageReport:
    """Gate: raise if any declared document went unread. Use for class-C corpora
    where 'I ranked the top-k' may not substitute for 'I read everything'."""
    if not report.complete:
        raise CorpusIncomplete(report)
    return report
