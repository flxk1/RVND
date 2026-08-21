# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Defined-terms registry — binding "the Processor" in a clause to a party.

Contracts lean on defined terms the way statutes lean on Art. 4 definitions:
"the Licensee shall…" is unexecutable until *Licensee* resolves to a party.
This module extracts definition clauses, stores them per contract version,
and lets a human (or a confident extractor) *bind* a term to a world-map
entity. Extraction and binding are separate steps on purpose: finding the
clause '"Processor" means ACME GmbH' is deterministic; asserting that this
is the same ACME GmbH as entity ``acme-gmbh`` is an identity claim that
needs either a registered party match or a human confirmation.

Patterns (Phase-1, EN + DE, deliberately narrow):

  * ``"Term" means …`` / ``"Term" shall mean …`` / ``"Term" refers to …``
  * ``… GmbH (the "Term")`` / ``… (hereinafter "Term")`` — parenthetical
  * ``„Begriff" bezeichnet …`` / ``„Begriff" bedeutet …`` / ``„Begriff" ist …``

Storage: ``<folder>/contracts/defined-terms.jsonl`` keyed by
(contract_ref, term, lowercased), idempotent, audited best-effort via the
folder's signed mutation log. Pure stdlib.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .mutation_log import LogEvent, MutationLog

__all__ = ["DefinedTerm", "DefinedTermsRegistry", "extract_defined_terms"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DefinedTerm:
    term: str                            # as written ("Processor")
    definition: str                      # the defining text, verbatim
    span: dict = field(default_factory=dict)   # {start, end} char offsets
    binds_to: Optional[str] = None       # entity code, once bound
    bound_by: str = ""                   # actor who asserted the binding
    confidence: float = 0.0              # extraction confidence
    source: str = "ingest"

    def to_dict(self) -> dict[str, Any]:
        return {"term": self.term, "definition": self.definition,
                "span": self.span, "binds_to": self.binds_to,
                "bound_by": self.bound_by, "confidence": self.confidence,
                "source": self.source}


# ── extraction (deterministic) ────────────────────────────────────────────────

# "Term" means / shall mean / refers to / has the meaning …
_EN_MEANS = re.compile(
    r"[\"“](?P<term>[A-Z][\w \-/]{1,60}?)[\"”]\s+"
    r"(?:shall\s+mean|means|refers\s+to|has\s+the\s+meaning)\s+"
    r"(?P<def>[^.;]{3,300})", re.U)

# … ACME GmbH (the "Processor") / (hereinafter "Processor") / (the “Processor”)
# / DE: … GmbH (nachfolgend „Auftragsverarbeiter“) / (im Folgenden „Kunde“)
_EN_PAREN = re.compile(
    r"(?P<def>[^.;()]{3,200}?)\s*\(\s*"
    r"(?:the|hereinafter(?:\s+referred\s+to\s+as)?|as|nachfolgend|im\s+Folgenden|der|die|das)?\s*"
    r"[\"“„](?P<term>[A-ZÄÖÜ][\wÄÖÜäöüß \-/]{1,60}?)[\"”“]\s*\)", re.U)

# „Begriff" bezeichnet / bedeutet / ist / meint …
_DE_MEANS = re.compile(
    r"[„\"](?P<term>[A-ZÄÖÜ][\wÄÖÜäöüß \-/]{1,60}?)[“\"”]\s+"
    r"(?:bezeichnet|bedeutet|meint|ist)\s+"
    r"(?P<def>[^.;]{3,300})", re.U)

_PATTERNS = ((_EN_MEANS, 0.9), (_DE_MEANS, 0.9), (_EN_PAREN, 0.85))


def extract_defined_terms(text: str) -> list[DefinedTerm]:
    """Find definition clauses in a contract text. Deterministic, narrow,
    deduplicated on the lowercased term (first definition wins — a re-defined
    term is a drafting smell the caller can detect by comparing counts)."""
    out: list[DefinedTerm] = []
    seen: set[str] = set()
    for rx, conf in _PATTERNS:
        for m in rx.finditer(text or ""):
            term = m["term"].strip()
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(DefinedTerm(
                term=term, definition=m["def"].strip(),
                span={"start": m.start(), "end": m.end()},
                confidence=conf))
    return out


# ── registry ──────────────────────────────────────────────────────────────────

def _terms_path(folder: str | Path) -> Path:
    return Path(folder) / "contracts" / "defined-terms.jsonl"


class DefinedTermsRegistry:
    """Per-folder store of defined terms, keyed by (contract_ref, term)."""

    def __init__(self, folder: str | Path, *, log_root: Optional[str | Path] = None):
        self.folder = Path(folder)
        self.log_root = Path(log_root) if log_root else None
        self.records: dict[str, dict] = {}     # "ref|term_lower" -> record
        self.load()

    @staticmethod
    def _key(contract_ref: str, term: str) -> str:
        return f"{contract_ref}|{term.strip().lower()}"

    def load(self) -> None:
        p = _terms_path(self.folder)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.records[self._key(r["contract_ref"], r["term"])] = r

    def _flush(self) -> None:
        p = _terms_path(self.folder)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                               for r in self.records.values())
                     + ("\n" if self.records else ""), encoding="utf-8")

    def _log(self, op: str, ref: str, extra: dict) -> Optional[str]:
        try:
            log = MutationLog(self.folder, log_root=self.log_root)
            return log.append(LogEvent(
                event="ingest", folder_path=str(self.folder), pair_id=ref,
                channel="document", actor=extra.get("actor", "ingest"),
                extra={"kind": "defined-term", "op": op, **extra}))
        except Exception:                                       # noqa: BLE001
            return None

    # ── write path ────────────────────────────────────────────────────────────
    def register(self, contract_ref: str, dt: DefinedTerm, *,
                 actor: str = "ingest") -> dict:
        key = self._key(contract_ref, dt.term)
        now = _now()
        existing = self.records.get(key)
        if existing is None:
            rec = dict(dt.to_dict(), contract_ref=contract_ref,
                       first_seen=now, last_seen=now)
            self.records[key] = rec
            self._flush()
            self._log("term.register", key, {"actor": actor, "term": dt.term})
            return dict(rec, status="created")
        existing["last_seen"] = now
        if dt.definition and not existing.get("definition"):
            existing["definition"] = dt.definition
        self._flush()
        return dict(existing, status="unchanged")

    def register_from_text(self, contract_ref: str, text: str, *,
                           actor: str = "ingest") -> list[dict]:
        return [self.register(contract_ref, dt, actor=actor)
                for dt in extract_defined_terms(text)]

    def bind(self, contract_ref: str, term: str, entity_code: str, *,
             actor: str) -> dict:
        """Assert that a term denotes a world-map entity. Identity assertions
        require a named actor — an anonymous binding is refused, and a re-bind
        to a different entity must come from a (named) human, audited."""
        if not actor or actor == "ingest":
            raise ValueError("binding a term is an identity claim — name the actor")
        key = self._key(contract_ref, term)
        rec = self.records.get(key)
        if rec is None:
            raise KeyError(f"term {term!r} not registered for {contract_ref!r}")
        prev = rec.get("binds_to")
        rec["binds_to"] = entity_code
        rec["bound_by"] = actor
        rec["last_seen"] = _now()
        self._flush()
        self._log("term.bind", key, {"actor": actor, "entity": entity_code,
                                     "previous": prev})
        return dict(rec, status="bound" if prev is None else "rebound")

    # ── read path ─────────────────────────────────────────────────────────────
    def resolve(self, contract_ref: str, term: str) -> Optional[str]:
        """Term → entity code, or None (unbound terms resolve to nothing,
        never to a guess)."""
        rec = self.records.get(self._key(contract_ref, term))
        return rec.get("binds_to") if rec else None

    def terms_for(self, contract_ref: str) -> list[dict]:
        return [r for r in self.records.values()
                if r.get("contract_ref") == contract_ref]

    def unbound(self, contract_ref: str) -> list[str]:
        """Terms still awaiting a binding — the decision-surface queue feed."""
        return [r["term"] for r in self.terms_for(contract_ref)
                if not r.get("binds_to")]
