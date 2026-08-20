# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fact binding — connecting contract predicates to observed reality.

A structured condition ("contract value exceeds EUR 10,000") only executes
against a *fact*: an observed value with a unit, a timestamp, and a source.
This module defines the seam:

  * :class:`Fact`              — one observation, provenance-stamped;
  * :class:`FactSource`        — the protocol (``get(subject_ref)``);
  * :class:`CsvFactSource`     — reference adapter over a CSV file
                                 (subject_ref, value, unit, observed_at);
  * :class:`ManualFactSource`  — reference adapter for human-asserted facts,
                                 persisted and audited (an assertion is an
                                 identity-grade claim: named actor required);
  * :func:`evaluate`           — Predicate × Fact → ``satisfied`` /
                                 ``unsatisfied`` / ``UNKNOWN``.

The third verdict is the point: a missing fact, a unit mismatch, or an
unparseable value yields ``UNKNOWN`` — surfaced to the caller (and from there
to the decision surface), never silently treated as false and never blocking
in the dark. Database/API adapters are deliberately post-validation (P2.5);
they implement the same protocol. Pure stdlib.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from .mutation_log import LogEvent, MutationLog
from workspaces.adapters.solver.predicate import Predicate

__all__ = ["Fact", "FactSource", "CsvFactSource", "ManualFactSource",
           "evaluate", "SATISFIED", "UNSATISFIED", "UNKNOWN"]

SATISFIED = "satisfied"
UNSATISFIED = "unsatisfied"
UNKNOWN = "UNKNOWN"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Fact:
    subject_ref: str
    value: str                            # canonical string (Decimal-safe)
    unit: Optional[str] = None
    observed_at: str = ""                 # ISO timestamp
    source: str = ""                      # where this observation came from

    def to_dict(self) -> dict[str, Any]:
        return {"subject_ref": self.subject_ref, "value": self.value,
                "unit": self.unit, "observed_at": self.observed_at,
                "source": self.source}


@runtime_checkable
class FactSource(Protocol):
    """The seam the obligation runtime binds through. ``get`` returns the
    latest Fact for a subject_ref, or None — None means UNKNOWN downstream,
    never an implicit false."""

    def get(self, subject_ref: str) -> Optional[Fact]: ...


# ── reference adapter 1: CSV ──────────────────────────────────────────────────

class CsvFactSource:
    """Facts from a CSV with columns: subject_ref, value, unit, observed_at.
    Last row per subject_ref wins (append-style files). A malformed file is an
    empty source, not a crash — absence of facts is a representable state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._facts: dict[str, Fact] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    ref = (row.get("subject_ref") or "").strip()
                    if not ref:
                        continue
                    self._facts[ref] = Fact(
                        subject_ref=ref, value=(row.get("value") or "").strip(),
                        unit=(row.get("unit") or "").strip() or None,
                        observed_at=(row.get("observed_at") or "").strip(),
                        source=f"csv:{self.path.name}")
        except Exception:                                       # noqa: BLE001
            self._facts = {}

    def get(self, subject_ref: str) -> Optional[Fact]:
        return self._facts.get(subject_ref)


# ── reference adapter 2: manual assertions ────────────────────────────────────

def _assertions_path(folder: str | Path) -> Path:
    return Path(folder) / "contracts" / "fact-assertions.jsonl"


class ManualFactSource:
    """Human-asserted facts ("the deliverable was accepted on 2026-07-03").
    Asserting a fact is a claim someone must own: a named actor is required,
    every assertion is persisted and audited, and later assertions supersede
    earlier ones without erasing them (the file is append-only history; the
    in-memory view is latest-wins)."""

    def __init__(self, folder: str | Path, *, log_root: Optional[str | Path] = None):
        self.folder = Path(folder)
        self.log_root = Path(log_root) if log_root else None
        self._facts: dict[str, Fact] = {}
        self._load()

    def _load(self) -> None:
        p = _assertions_path(self.folder)
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                self._facts[d["subject_ref"]] = Fact(
                    subject_ref=d["subject_ref"], value=d["value"],
                    unit=d.get("unit"), observed_at=d.get("observed_at", ""),
                    source=d.get("source", "manual"))

    def assert_fact(self, subject_ref: str, value: str, *, actor: str,
                    unit: Optional[str] = None) -> Fact:
        if not actor or actor in ("system", "ingest", "scheduler"):
            raise ValueError("asserting a fact is a claim — name the human actor")
        fact = Fact(subject_ref=subject_ref, value=str(value), unit=unit,
                    observed_at=_now(), source=f"manual:{actor}")
        p = _assertions_path(self.folder)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(fact.to_dict(), ensure_ascii=False) + "\n")
        self._facts[subject_ref] = fact
        try:
            log = MutationLog(self.folder, log_root=self.log_root)
            log.append(LogEvent(
                event="system", folder_path=str(self.folder),
                pair_id=f"fact:{subject_ref}", channel="system", actor=actor,
                extra={"kind": "fact-assertion", "subject_ref": subject_ref,
                       "value": str(value), "unit": unit}))
        except Exception:                                       # noqa: BLE001
            pass
        return fact

    def get(self, subject_ref: str) -> Optional[Fact]:
        return self._facts.get(subject_ref)


# ── evaluation ────────────────────────────────────────────────────────────────

_OPS = {
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}

_TRUE_WORDS = frozenset({"true", "yes", "1", "done", "satisfied", "ja"})
_FALSE_WORDS = frozenset({"false", "no", "0", "not-done", "unsatisfied", "nein"})


def evaluate(predicate: Predicate, fact: Optional[Fact]) -> dict[str, Any]:
    """Predicate × Fact → verdict dict ``{verdict, reason, fact}``.

    UNKNOWN whenever the comparison cannot be made honestly: no fact, unit
    mismatch, undecodable value, or a predicate kind evaluation doesn't cover
    (event predicates resolve through the scheduler's date arithmetic, not
    here). UNKNOWN is a surfacing verdict — route it to the decision surface."""
    if fact is None:
        return {"verdict": UNKNOWN, "reason": "no fact for subject_ref", "fact": None}

    if predicate.kind == "threshold":
        if predicate.unit and fact.unit and predicate.unit != fact.unit:
            return {"verdict": UNKNOWN,
                    "reason": f"unit mismatch: predicate {predicate.unit} vs fact {fact.unit}",
                    "fact": fact.to_dict()}
        try:
            have = Decimal(fact.value)
            want = Decimal(predicate.value)            # validated at construction
        except (InvalidOperation, TypeError):
            return {"verdict": UNKNOWN,
                    "reason": f"non-decimal fact value {fact.value!r}",
                    "fact": fact.to_dict()}
        ok = _OPS[predicate.comparator](have, want)
        return {"verdict": SATISFIED if ok else UNSATISFIED,
                "reason": f"{have} {predicate.comparator} {want}",
                "fact": fact.to_dict()}

    if predicate.kind == "state":
        v = fact.value.strip().lower()
        if v in _TRUE_WORDS:
            return {"verdict": SATISFIED, "reason": f"state {v!r}", "fact": fact.to_dict()}
        if v in _FALSE_WORDS:
            return {"verdict": UNSATISFIED, "reason": f"state {v!r}", "fact": fact.to_dict()}
        return {"verdict": UNKNOWN, "reason": f"unrecognised state value {fact.value!r}",
                "fact": fact.to_dict()}

    # event predicates: deadline semantics live in the scheduler's date
    # arithmetic against contract events — not evaluable from a bare fact.
    return {"verdict": UNKNOWN,
            "reason": f"predicate kind {predicate.kind!r} not fact-evaluable",
            "fact": fact.to_dict()}
