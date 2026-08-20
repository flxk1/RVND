# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Decision surface — what MANUAL-level Oversight presents at a residual choice.

At an `Esc ∧ Stake` point (`norm_contract` returned ESCALATE on a stake-bearing
query, §4a of the manifest) the system must *not* hand the human one AI-generated
answer to confirm. That is the moral crumple zone: approval without a real choice
launders machine output as human judgment. Meaningful approval requires the human
to be **informed**, to face **≥2 defensible options**, and to see each option's
**consequences** — and then to *originate* the choice with reasons of her own.

This module assembles that surface. It is built so that the crumple zone is
structurally impossible:

  * **No single answer.** A surface with one option is flagged
    `single_reading_warning` — it is still presented as a *choice to make*, never
    as an answer to confirm. A surface with zero options is an error (you cannot
    choose nothing).
  * **No "recommended" option, no default, stable ordering.** Options are ordered
    by id (not by support strength) so the interface does not anchor the human on
    one reading. `grounding` is exposed as *support strength*, explicitly **not**
    correctness.
  * **Origination is enforced.** `record_choice` refuses an empty rationale: a
    choice cannot be recorded without the chooser's own reasons (the reasons-must-
    be-the-decider's-own duty), and it writes a signed audit event naming who
    chose, what, and why.

Pure stdlib + the mutation log. The *content* of the options (which readings are
defensible, what follows from each) comes from the legal layer / the human; this
module is the presentation-and-record contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..mutation_log import MutationLog, LogEvent
from ..urn import mint_canonical


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Option:
    id: str
    label: str
    conclusion: str                       # the deontic reading, e.g. "retain (Art. 18 restriction)"
    supporting: list[dict] = field(default_factory=list)   # norm refs: {pinpoint/entity, text}
    reasons: str = ""                     # why this reading is defensible
    consequences: list[str] = field(default_factory=list)  # what follows if chosen
    grounding: float = 0.0                # support strength in [0,1] — NOT probability of being correct

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DecisionSurface:
    query: str
    options: list[Option]
    esc_reason: str = ""                  # why this is residual (from norm_contract)
    context: str = ""
    residual: bool = True
    single_reading_warning: bool = False
    options_may_be_incomplete: bool = True
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["options"] = [o.to_dict() for o in self.options]
        return d

    def choice_schema(self) -> dict:
        """The form the human fills to *originate* the decision. There is no
        default and no pre-selected option — the human must choose and must give a
        rationale. `considered` records which options were actually reviewed."""
        return {
            "chosen_option_id": None,                 # REQUIRED, no default
            "rationale": "",                          # REQUIRED, non-empty (origination)
            "considered": [],                         # option ids the human reviewed
            "actor": "",                              # who decides (standing)
            "option_ids": [o.id for o in self.options],
        }


def _grounding(n_support: int, max_tier: Optional[int]) -> float:
    base = min(1.0, 0.4 + 0.2 * max(0, n_support))
    if max_tier is not None:                          # primary law (tier 1) lifts grounding
        base = min(1.0, base + (0.15 if max_tier <= 1 else 0.0))
    return round(base, 3)


def _decision_urn(query: str, chosen_option_id: str, actor: str) -> str:
    """Deterministic address of one originated choice: the same query, option
    and actor mint the same URN, so re-recording is idempotent on the graph.
    No timestamp — the identity is what was decided, not when."""
    h = hashlib.sha256(
        f"{query}|{chosen_option_id}|{actor}".encode("utf-8")).hexdigest()[:24]
    return mint_canonical("decision-" + h)


def _resolve_rule_urn(item: dict, folder) -> str:
    """A supporting item's rule address: an explicit ``rule_urn`` wins; else
    recovered by (pinpoint, document) from the folder's rule registry; else
    empty — the caller records the miss rather than guessing."""
    if item.get("rule_urn"):
        return str(item["rule_urn"])
    pin = item.get("pinpoint") or ""
    doc = item.get("source_document") or ""
    if not (folder and pin):
        return ""
    try:
        from ..rule_registry import RuleRegistry
        for rec in RuleRegistry(folder).workspace_items():
            sp = rec.get("span") or {}
            if sp.get("pinpoint") == pin and (not doc or sp.get("document") == doc):
                return rec.get("canonical_urn", "")
    except Exception:                                   # noqa: BLE001
        return ""
    return ""


def build_surface(query: str, candidates: list[dict], *, esc_reason: str = "",
                  context: str = "", options_may_be_incomplete: bool = True) -> DecisionSurface:
    """Assemble a decision surface from candidate readings. Each candidate:
    ``{"id","label","conclusion",[supporting],[reasons],[consequences],[authority_tier]}``.

    Invariants: 0 candidates → ValueError (no surface). 1 candidate → presented but
    `single_reading_warning=True` (still a choice, not an answer). ≥2 → residual.
    Options are sorted by id (stable, non-anchoring); none is marked recommended."""
    if not candidates:
        raise ValueError("a decision surface needs at least one option; got none")
    opts: list[Option] = []
    for c in candidates:
        sup = c.get("supporting", []) or []
        opts.append(Option(
            id=c["id"], label=c.get("label", c["id"]),
            conclusion=c.get("conclusion", ""), supporting=sup,
            reasons=c.get("reasons", ""), consequences=c.get("consequences", []) or [],
            grounding=_grounding(len(sup), c.get("authority_tier"))))
    opts.sort(key=lambda o: o.id)                     # stable order, NOT by grounding
    single = len(opts) == 1
    return DecisionSurface(
        query=query, options=opts, esc_reason=esc_reason, context=context,
        residual=not single, single_reading_warning=single,
        options_may_be_incomplete=options_may_be_incomplete,
        note=("Single defensible reading found — but this is a residual (ESCALATE) "
              "point: treat it as a choice to make, not an answer to confirm."
              if single else
              "Residual choice: select among the defensible options below. The "
              "system presents grounds and consequences; the decision is yours."))


def record_choice(surface: DecisionSurface, *, chosen_option_id: str, rationale: str,
                  actor: str, folder: Optional[str | Path] = None,
                  log_root: Optional[str | Path] = None,
                  considered: Optional[list[str]] = None,
                  asked: Optional[list[dict]] = None,
                  evidence_refs: Optional[list[str]] = None,
                  auth_rung: str = "",
                  corpus: Optional[str | Path] = None) -> dict:
    """Record the human's *originated* choice. Refuses unless the choice is a real
    option AND a non-empty rationale is supplied AND an actor is named — origination,
    not rubber-stamp. Writes a signed audit event when a folder is given.

    ``considered`` records only what the decider actually reviewed — absent means
    none, never all (an auto-claimed review would be ratification theatre).
    ``asked`` carries the recorded assistance exchanges consulted for this choice;
    ``evidence_refs`` names works brought inside the boundary as grounds.

    The record carries a deterministic ``canonical_urn``. With ``corpus`` given,
    a ``decides`` edge is written into that corpus for each of the chosen
    option's supporting rules (explicit ``rule_urn`` or recovered from the rule
    registry by pinpoint); an unresolvable reference is surfaced on the record
    as ``decides_unresolved`` — the choice itself is never blocked by it.

    Returns the decision record (with `audit_id` if logged), or an `error` dict."""
    ids = {o.id for o in surface.options}
    if chosen_option_id not in ids:
        return {"error": f"chosen_option_id {chosen_option_id!r} is not an option",
                "options": sorted(ids)}
    if not (rationale or "").strip():
        return {"error": "a choice cannot be recorded without the chooser's own "
                         "rationale (origination is required, ratification is not enough)"}
    if not (actor or "").strip():
        return {"error": "an actor (the responsible decider) must be named"}
    record = {
        "query": surface.query, "chosen_option_id": chosen_option_id,
        "chosen_label": next(o.label for o in surface.options if o.id == chosen_option_id),
        "canonical_urn": _decision_urn(surface.query, chosen_option_id, actor.strip()),
        "rationale": rationale.strip(), "actor": actor.strip(),
        "considered": list(considered) if considered is not None else [],
        "esc_reason": surface.esc_reason, "decided_at": _now(),
    }
    if corpus is not None:
        chosen = next(o for o in surface.options if o.id == chosen_option_id)
        emitted: list[str] = []
        unresolved: list[str] = []
        try:
            from ..legal_corpus import EntityRegistry
            reg = EntityRegistry(corpus,
                                 log_root=Path(log_root) if log_root else None)
            for item in chosen.supporting:
                item = dict(item)
                rule_urn = _resolve_rule_urn(item, corpus)
                if not rule_urn:
                    unresolved.append(item.get("pinpoint")
                                      or (item.get("text") or "")[:40])
                    continue
                reg.ingest_edge(subject=record["canonical_urn"],
                                connection="decides", obj=rule_urn,
                                basis=item.get("pinpoint", ""), source="decision")
                emitted.append(rule_urn)
        except Exception as exc:                       # noqa: BLE001 — never lose the choice
            record["edge_error"] = f"{type(exc).__name__}: {exc}"
        record["decides"] = emitted
        if unresolved:
            record["decides_unresolved"] = unresolved
    if asked:
        record["asked"] = [dict(a) for a in asked]
    if evidence_refs:
        record["evidence_refs"] = [str(r) for r in evidence_refs]
    if auth_rung:
        # how strongly the system knew the actor — the record never claims
        # more certainty than the authentication carried
        record["auth_rung"] = str(auth_rung)
    if folder is not None:
        try:
            log = MutationLog(Path(folder), log_root=Path(log_root) if log_root else None)
            record["audit_id"] = log.append(LogEvent(
                event="system", folder_path=str(folder),
                pair_id=f"decision:{chosen_option_id}", channel="reasoning",
                actor=record["actor"],
                extra={"kind": "residual-decision", **record}))
        except Exception as exc:                       # noqa: BLE001 — never lose the choice
            record["audit_error"] = f"{type(exc).__name__}: {exc}"
    return record
