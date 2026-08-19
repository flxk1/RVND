# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The obligation runtime — contract norms as tracked, dated, gated state.

An admitted contract rule ("the processor shall notify the controller within
72 hours") becomes an :class:`Obligation`: who owes what to whom, by when,
under which clause of which contract version. The runtime tracks each
obligation through an explicit state machine and persists every transition to
the folder's signed mutation log.

The state machine::

    pending ──> due_soon ──> due ──> breached_candidate   (machine stops here)
       │            │         │
       └────────────┴─────────┴──> satisfied | waived     (human-recorded)
    any open state ──> superseded                          (version migration)
    any open state ──> escalated                           (orphaned rule, …)

Two hard lines, by design:

  * The machine NEVER declares breach. Passing a deadline produces
    ``breached_candidate`` — a finding for the decision surface, where a human
    records breach, waiver, or satisfaction with a rationale. Whether a missed
    date is a breach is a legal judgment (force majeure, waiver by conduct,
    cure periods); the runtime surfaces, it does not judge.
  * ``satisfied`` and ``waived`` require a named actor. Anonymous resolution
    of an obligation is refused.

Deadlines are typed (``temporal.Date`` / ``RelativeDeadline``); a relative
deadline that cannot be resolved against the contract's known event dates
keeps the obligation in ``pending`` with ``deadline_unresolved`` — visible,
never guessed. Pure stdlib.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .contracts.instance import ContractInstance
from .mutation_log import LogEvent, MutationLog
from rvnd.adapters.solver.temporal import Date, Duration, RelativeDeadline, TemporalError

__all__ = ["Obligation", "ObligationRegistry", "ObligationError",
           "OPEN_STATES", "TERMINAL_STATES"]


class ObligationError(ValueError):
    """Raised on malformed obligations or illegal transitions."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── states ────────────────────────────────────────────────────────────────────

OPEN_STATES = ("pending", "due_soon", "due", "breached_candidate", "escalated")
TERMINAL_STATES = ("satisfied", "waived", "superseded")
_ALL_STATES = OPEN_STATES + TERMINAL_STATES

# machine-drivable transitions (the scheduler walks these)
_MACHINE_TRANSITIONS = {
    ("pending", "due_soon"), ("pending", "due"), ("pending", "breached_candidate"),
    ("due_soon", "due"), ("due_soon", "breached_candidate"),
    ("due", "breached_candidate"),
}
# human-recorded transitions (named actor + rationale required)
_HUMAN_TRANSITIONS = {
    (s, t) for s in OPEN_STATES for t in ("satisfied", "waived")
}
# migration transitions (system, audited, reason required)
_MIGRATION_TRANSITIONS = {(s, "superseded") for s in OPEN_STATES} | {
    (s, "escalated") for s in ("pending", "due_soon", "due")
}


def _oblig_id(contract_ref: str, rule_id: str, obligor_role: str) -> str:
    h = hashlib.sha256(f"{contract_ref}|{rule_id}|{obligor_role}".encode("utf-8"))
    return "oblig:" + h.hexdigest()[:24]


_ARTICLES = re.compile(r"^(?:the|a|an|der|die|das|den|dem|des|la|le|les|el|il)\s+",
                       re.I)


def _obligor_role(subject: str, contract: ContractInstance) -> str:
    """The obligor as a PARTY ROLE, not a raw grammar subject. A rule's
    subject ("The Processor", "Die Auftragnehmerin", or a whole leaked
    condition clause) must resolve to the contract's party vocabulary so the
    intake card, the timeline, and the decision surface all point at the same
    person. Resolution order: a party role contained in the subject wins;
    else strip articles and slug; a subject that still looks like a sentence
    fragment yields "unknown" — visible, never a garbage identifier."""
    s = re.sub(r"\s+", " ", (subject or "").strip().lower())
    for p in contract.parties:
        role_words = p.role.replace("-", " ")
        if role_words and role_words in s:
            return p.role
    s = _ARTICLES.sub("", s).split(",")[0].strip()
    slug = re.sub(r"[^a-z0-9äöüß]+", "-", s).strip("-")
    if not slug or len(slug) > 40 or slug.count("-") > 4:
        return "unknown"
    return slug


@dataclass
class Obligation:
    """One tracked duty under one contract version."""

    obligation_id: str
    contract_ref: str                       # "cid@version"
    rule_id: str                            # SpanNorm id the duty derives from
    summary: str = ""                       # human-readable: who must do what
    obligor_role: str = ""                  # role slug (resolved via parties)
    obligee_role: str = ""
    deadline_date: Optional[Date] = None    # absolute, once resolved
    deadline_rel: Optional[RelativeDeadline] = None
    state: str = "pending"
    facets: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in _ALL_STATES:
            raise ObligationError(f"unknown state {self.state!r}")
        if self.deadline_date is not None and not isinstance(self.deadline_date, Date):
            raise ObligationError("deadline_date must be a temporal.Date")
        if self.deadline_rel is not None and not isinstance(self.deadline_rel, RelativeDeadline):
            raise ObligationError("deadline_rel must be a temporal.RelativeDeadline")

    @property
    def open(self) -> bool:
        return self.state in OPEN_STATES

    def resolved_deadline(self, contract: Optional[ContractInstance] = None) -> Optional[Date]:
        """Absolute deadline if known or resolvable; None = unresolved (and the
        registry surfaces that, it never guesses)."""
        if self.deadline_date is not None:
            return self.deadline_date
        if self.deadline_rel is not None and contract is not None:
            return contract.resolve_deadline(self.deadline_rel)
        return None

    def to_dict(self) -> dict:
        return {"obligation_id": self.obligation_id, "contract_ref": self.contract_ref,
                "rule_id": self.rule_id, "summary": self.summary,
                "obligor_role": self.obligor_role, "obligee_role": self.obligee_role,
                "deadline_date": self.deadline_date.iso if self.deadline_date else None,
                "deadline_rel": self.deadline_rel.to_dict() if self.deadline_rel else None,
                "state": self.state, "facets": self.facets}

    @classmethod
    def from_dict(cls, d: dict) -> "Obligation":
        return cls(obligation_id=d["obligation_id"], contract_ref=d["contract_ref"],
                   rule_id=d["rule_id"], summary=d.get("summary", ""),
                   obligor_role=d.get("obligor_role", ""),
                   obligee_role=d.get("obligee_role", ""),
                   deadline_date=Date(d["deadline_date"]) if d.get("deadline_date") else None,
                   deadline_rel=RelativeDeadline.from_dict(d["deadline_rel"])
                   if d.get("deadline_rel") else None,
                   state=d.get("state", "pending"), facets=d.get("facets", {}))


def _obligations_path(folder: str | Path) -> Path:
    return Path(folder) / "contracts" / "obligations.jsonl"


class ObligationRegistry:
    """Persisted, audited obligation store for one folder."""

    def __init__(self, folder: str | Path, *, log_root: Optional[str | Path] = None):
        self.folder = Path(folder)
        self.log_root = Path(log_root) if log_root else None
        self.items: dict[str, dict] = {}
        self.load()

    # ── persistence ───────────────────────────────────────────────────────────
    def load(self) -> None:
        p = _obligations_path(self.folder)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.items[r["obligation_id"]] = r

    def _flush(self) -> None:
        p = _obligations_path(self.folder)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                               for r in self.items.values())
                     + ("\n" if self.items else ""), encoding="utf-8")

    def _log(self, op: str, ref: str, extra: dict) -> Optional[str]:
        try:
            log = MutationLog(self.folder, log_root=self.log_root)
            return log.append(LogEvent(
                event="system", folder_path=str(self.folder), pair_id=ref,
                channel="system", actor=extra.get("actor", "system"),
                extra={"kind": "obligation", "op": op, **extra}))
        except Exception:                                       # noqa: BLE001
            return None

    # ── instantiation ─────────────────────────────────────────────────────────
    def instantiate(self, contract: ContractInstance, rules: Iterable[dict], *,
                    actor: str = "system") -> dict:
        """Create obligations from a contract's admitted span-norms. Only
        modal == 'obligation' / 'prohibition' rules instantiate (permissions
        and rights are not duties). Deadline comes from the rule's structured
        condition when it is an event predicate with a temporal part; absent
        that, the obligation is tracked without a deadline (visible, not
        invented). Idempotent on (contract_ref, rule_id, obligor_role)."""
        created, skipped = [], []
        for rec in rules:
            norm = rec.get("norm") or {}
            modal = norm.get("modal", "")
            if modal not in ("obligation", "prohibition"):
                skipped.append({"rule_id": rec.get("id"), "why": f"modal={modal or 'none'}"})
                continue
            rule_id = rec.get("id", "")
            obligor = _obligor_role(norm.get("subject") or "", contract)
            oid = _oblig_id(contract.ref, rule_id, obligor)
            if oid in self.items:
                skipped.append({"rule_id": rule_id, "why": "exists"})
                continue
            deadline_rel = None
            struct = norm.get("condition_struct") or rec.get("condition_struct")
            if isinstance(struct, dict) and struct.get("temporal"):
                try:
                    deadline_rel = RelativeDeadline.from_dict(struct["temporal"])
                except (TemporalError, KeyError):
                    deadline_rel = None                          # abstain, don't guess
            ob = Obligation(
                obligation_id=oid, contract_ref=contract.ref, rule_id=rule_id,
                summary=f"{norm.get('subject', '?')} {norm.get('modal', '?')}: "
                        f"{norm.get('action', '?')}"[:200],
                obligor_role=obligor,
                obligee_role=norm.get("counterparty", ""),
                deadline_rel=deadline_rel,
                facets={"modal": modal, "condition": norm.get("condition", ""),
                        "incident": norm.get("incident", ""),
                        "condition_kind": norm.get("condition_kind", "")})
            rec_d = dict(ob.to_dict(), first_seen=_now(), last_seen=_now(),
                         history=[{"state": "pending", "at": _now(), "actor": actor,
                                   "reason": "instantiated"}])
            self.items[oid] = rec_d
            created.append(oid)
            self._log("obligation.create", oid, {
                "actor": actor, "contract_ref": contract.ref, "rule_id": rule_id,
                "deadline_rel": ob.deadline_rel.to_dict() if ob.deadline_rel else None})
        if created:
            self._flush()
        return {"contract_ref": contract.ref, "created": created, "skipped": skipped}

    # ── transitions ───────────────────────────────────────────────────────────
    def _transition(self, oid: str, to_state: str, *, actor: str, reason: str,
                    allowed: set, require_named_actor: bool = False) -> dict:
        rec = self.items.get(oid)
        if rec is None:
            raise ObligationError(f"unknown obligation {oid!r}")
        frm = rec["state"]
        if (frm, to_state) not in allowed:
            raise ObligationError(f"illegal transition {frm} -> {to_state} for {oid}")
        if require_named_actor and (not actor or actor == "system"):
            raise ObligationError(
                f"{to_state!r} is a human judgment — name the actor")
        if require_named_actor and not reason.strip():
            raise ObligationError(f"{to_state!r} requires a rationale")
        rec["state"] = to_state
        rec["last_seen"] = _now()
        rec.setdefault("history", []).append(
            {"state": to_state, "at": _now(), "actor": actor, "reason": reason})
        self._flush()
        self._log("obligation.transition", oid,
                  {"actor": actor, "from": frm, "to": to_state, "reason": reason})
        return dict(rec)

    def advance(self, oid: str, to_state: str, *, reason: str) -> dict:
        """Machine transition (scheduler): pending/due_soon/due forward motion,
        terminating — for the machine — at breached_candidate."""
        return self._transition(oid, to_state, actor="scheduler", reason=reason,
                                allowed=_MACHINE_TRANSITIONS)

    def resolve(self, oid: str, to_state: str, *, actor: str, reason: str) -> dict:
        """Human resolution: satisfied or waived. Named actor + rationale."""
        return self._transition(oid, to_state, actor=actor, reason=reason,
                                allowed=_HUMAN_TRANSITIONS, require_named_actor=True)

    def annotate(self, oid: str, *, actor: str, note: str) -> dict:
        """A human note WITHOUT a state change: "disputed", "cure period
        agreed until …", "counterparty notified". The legally common third
        answer between satisfied and waived — the duty stays open and watched,
        the judgment call is on record. Named actor + note required."""
        rec = self.items.get(oid)
        if rec is None:
            raise ObligationError(f"unknown obligation {oid!r}")
        if not actor or actor in ("system", "ingest", "scheduler"):
            raise ObligationError("annotating an obligation is a judgment — name the actor")
        if not (note or "").strip():
            raise ObligationError("an annotation needs content")
        rec["last_seen"] = _now()
        rec.setdefault("history", []).append(
            {"state": rec["state"], "at": _now(), "actor": actor,
             "reason": note, "annotation": True})
        self._flush()
        self._log("obligation.annotate", oid,
                  {"actor": actor, "state": rec["state"], "note": note})
        return dict(rec)

    def supersede_for(self, old_ref: str, new_ref: str, *,
                      migrated_rules: Iterable[str],
                      orphaned_rules: Iterable[str],
                      actor: str = "system") -> dict:
        """Version migration: obligations of ``old_ref`` whose rule survived the
        re-anchor move to ``new_ref`` (open state preserved, history kept);
        obligations whose rule was orphaned go to ``escalated`` — a human
        decides on the decision surface whether the duty survives the
        amendment. Closed obligations are left untouched (history is history)."""
        migrated_set, orphaned_set = set(migrated_rules), set(orphaned_rules)
        moved, escalated, untouched = [], [], []
        for oid, rec in list(self.items.items()):
            if rec["contract_ref"] != old_ref:
                continue
            if rec["state"] in TERMINAL_STATES:
                untouched.append(oid)
                continue
            if rec["rule_id"] in migrated_set:
                rec["contract_ref"] = new_ref
                rec["last_seen"] = _now()
                rec.setdefault("history", []).append(
                    {"state": rec["state"], "at": _now(), "actor": actor,
                     "reason": f"migrated {old_ref} -> {new_ref}"})
                moved.append(oid)
                self._log("obligation.migrate", oid,
                          {"actor": actor, "from_ref": old_ref, "to_ref": new_ref})
            elif rec["rule_id"] in orphaned_set:
                self._transition(oid, "escalated", actor=actor,
                                 reason=f"rule orphaned by {new_ref} — human review",
                                 allowed=_MIGRATION_TRANSITIONS)
                escalated.append(oid)
            else:
                untouched.append(oid)
        if moved:
            self._flush()
        return {"old_ref": old_ref, "new_ref": new_ref, "migrated": moved,
                "escalated": escalated, "untouched": untouched,
                "escalate": bool(escalated)}

    # ── queries ───────────────────────────────────────────────────────────────
    def get(self, oid: str) -> Optional[Obligation]:
        r = self.items.get(oid)
        return Obligation.from_dict(r) if r else None

    def for_contract(self, contract_ref: str, *, open_only: bool = False) -> list[Obligation]:
        out = [Obligation.from_dict(r) for r in self.items.values()
               if r["contract_ref"] == contract_ref]
        if open_only:
            out = [o for o in out if o.open]
        return sorted(out, key=lambda o: o.obligation_id)

    def in_state(self, state: str) -> list[Obligation]:
        return sorted((Obligation.from_dict(r) for r in self.items.values()
                       if r["state"] == state), key=lambda o: o.obligation_id)

    def candidates(self) -> list[Obligation]:
        """The decision-surface feed: breach candidates + escalated."""
        return self.in_state("breached_candidate") + self.in_state("escalated")

    def history(self, oid: str) -> list[dict]:
        rec = self.items.get(oid)
        return list(rec.get("history", [])) if rec else []
