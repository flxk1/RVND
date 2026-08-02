# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workbench I/O — the seam between the contract-execution registries and the
click-through workbench (``plugin/assets/workspace-contracts.html``).

The workbench is a self-contained HTML app (no server, no framework). It
renders five screens from one exported JSON and queues every human action
into an actions JSON the user downloads and applies here. Both directions go
through the REAL code paths:

  * :func:`export_state`   — folder → one JSON with everything the five
                             screens render: contracts (S1), clauses + anchors
                             (S2), obligations + resolved deadlines (S3), the
                             decision queue (S4), audit events (S5);
  * :func:`apply_actions`  — the queued human actions → registry calls.
                             ``resolve_obligation`` and ``record_correction``
                             enforce exactly what the registries enforce:
                             named actor, non-empty rationale, legal
                             transitions only. Nothing in the workbench can
                             bypass a gate, because the workbench cannot
                             write — only this module can, and it only calls
                             the gated APIs;
  * :func:`build_demo`     — ingests the P1 template corpus into a folder and
                             runs scheduler ticks, so the validation protocol
                             has a real, regenerable state to click through.

Corrections recorded via the workbench land in
``<folder>/contracts/corrections.jsonl`` — the cold-start feedback loop the
plan requires (confirmed/corrected extractions accumulate toward the eval
set; Shield governs anything leaving the folder, not this module).
Pure stdlib.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .contracts.instance import ContractRegistry
from .defined_terms import DefinedTermsRegistry
from .obligation_runtime import ObligationRegistry
from .obligation_scheduler import ObligationScheduler
from .rule_registry import RuleRegistry
from workspaces.adapters.solver.temporal import Date

__all__ = ["export_state", "apply_actions", "build_demo"]

SCHEMA_VERSION = "p3-1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── export ────────────────────────────────────────────────────────────────────

def export_state(folder, *, log_root=None,
                 as_of: Optional[Date] = None) -> dict[str, Any]:
    """Everything the five screens render, from the folder's registries."""
    folder = Path(folder)
    contracts = ContractRegistry(folder, log_root=log_root)
    spans = RuleRegistry(folder, log_root=log_root)
    obligations = ObligationRegistry(folder, log_root=log_root)
    terms = DefinedTermsRegistry(folder, log_root=log_root)

    out_contracts = []
    for inst in contracts.all_latest():
        if inst is None:
            continue
        chain = contracts.chain(inst.contract_id)
        out_contracts.append({
            "instance": inst.to_dict(),
            "ref": inst.ref,
            "chain": chain,
            "missing": inst.missing_fields(),
            "defined_terms": terms.terms_for(inst.ref),
            "unbound_terms": terms.unbound(inst.ref),
        })

    out_clauses = []
    for rec in spans.items.values():
        out_clauses.append({
            "id": rec["id"], "span": rec["span"], "norm": rec["norm"],
            "anchors": rec.get("anchors", []), "kind": rec.get("kind"),
            "orphaned": rec.get("orphaned"),
        })

    fundstelle_by_rule = {rec["id"]: (rec.get("span") or {}).get("pinpoint", "")
                          for rec in spans.items.values()}
    out_obligations = []
    for rec in obligations.items.values():
        ob = obligations.get(rec["obligation_id"])
        contract = None
        cid, _, ver = ob.contract_ref.partition("@")
        try:
            contract = contracts.get(cid, int(ver)) if ver else None
        except Exception:                                       # noqa: BLE001
            contract = None
        deadline = ob.resolved_deadline(contract)
        derivation = ""
        if ob.deadline_rel is not None and contract is not None:
            derivation = ob.deadline_rel.derivation(contract.event_dates())
        elif ob.deadline_date is not None:
            derivation = f"explicit {ob.deadline_date.iso}"
        out_obligations.append({
            **rec,
            "resolved_deadline": deadline.iso if deadline else None,
            "derivation": derivation,
            "fundstelle": fundstelle_by_rule.get(ob.rule_id, ""),
        })

    # S4 decision queue: breach candidates + escalated + orphaned spans +
    # unbound defined terms. Options are NEVER pre-ranked by the export —
    # stable id order, no default (decision-surface discipline).
    queue = []
    for ob in obligations.candidates():
        kind = ("breach-candidate" if ob.state == "breached_candidate"
                else "escalated-obligation")
        queue.append({
            "id": f"q:{ob.obligation_id}", "kind": kind,
            "subject": ob.summary, "contract_ref": ob.contract_ref,
            "obligation_id": ob.obligation_id,
            "fundstelle": fundstelle_by_rule.get(ob.rule_id, ""),
            "options": sorted([
                {"id": "disputed", "label": "Disputed / cure agreed — keep open",
                 "consequence": "stays open and watched; your note goes on record"},
                {"id": "satisfied", "label": "It was fulfilled",
                 "consequence": "obligation closes; your reason becomes the record"},
                {"id": "waived", "label": "We let it go (waived)",
                 "consequence": "duty stands down; your reason becomes the record"},
            ], key=lambda o: o["id"]),
            "note": "A deadline passed. Whether that is a real breach — cure period, "
                    "waiver, force majeure — is your call, not the system's."
            if kind == "breach-candidate" else
            "The new contract version dropped the clause this duty came from. "
            "Does the duty still stand?",
        })
    for rec in spans.orphans():
        queue.append({
            "id": f"q:span:{rec['id']}", "kind": "orphaned-span",
            "subject": rec["span"].get("text", "")[:160],
            "contract_ref": rec["span"].get("document", ""),
            "span_id": rec["id"],
            "options": sorted([
                {"id": "amended", "label": "Clause was amended — needs re-extraction"},
                {"id": "deleted", "label": "Clause was removed — retire the span"},
            ], key=lambda o: o["id"]),
            "note": "Span text no longer occurs in the current document version.",
        })
    for c in out_contracts:
        gaps = (c["instance"].get("facets") or {}).get("mandatory_content", {})
        if gaps.get("not_found"):
            cname = gaps.get("name") or "mandatory content"
            queue.append({
                "id": f"q:mandatory:{c['ref']}", "kind": "mandatory-content-gap",
                "subject": f"{cname} not found by cue: "
                           + "; ".join(gaps["not_found"]),
                "contract_ref": c["ref"],
                "options": sorted([
                    {"id": "confirmed-missing",
                     "label": "Confirmed missing — the contract needs amending",
                     "consequence": "the gap goes on record with your reason"},
                    {"id": "present-unrecognised",
                     "label": "It is in there — the cue missed it",
                     "consequence": "your pointer becomes a correction and "
                                    "trains the cue list"},
                ], key=lambda o: o["id"]),
                "note": f"The installed checklist ({cname}) prescribes minimum "
                        "content. Not-found means not found by cue — whether a "
                        "creatively-drafted clause covers it is your reading.",
            })
        for term in c["unbound_terms"]:
            queue.append({
                "id": f"q:term:{c['ref']}:{term}", "kind": "unbound-term",
                "subject": f"“{term}” is defined but bound to no entity",
                "contract_ref": c["ref"], "term": term,
                "options": [], "free_entity_input": True,
                "note": "Binding a term is an identity claim — name the entity and own it.",
            })

    # S5 audit: read the mutation log file directly (read-only view).
    audit = []
    try:
        from .mutation_log import MutationLog
        log = MutationLog(folder, log_root=log_root)
        for evt in log.replay():
            e = getattr(evt, "__dict__", {}) if not isinstance(evt, dict) else evt
            extra = e.get("extra") or {}
            if extra.get("kind") in {"contract-instance", "obligation",
                                     "defined-term", "rule-item",
                                     "fact-assertion", "workbench-action"}:
                audit.append({"ts": e.get("ts"), "actor": e.get("actor"),
                              "op": extra.get("op", extra.get("kind")),
                              "ref": e.get("pair_id"),
                              "extra": {k: v for k, v in extra.items()
                                        if k not in ("kind",)}})
    except Exception:                                           # noqa: BLE001
        pass

    # S6: the graph rides in the same state file — one folder, one export,
    # one app (gap-closure 2026-06-05; no second JSON for the user to manage).
    from .graph_export import export_graph
    graph = export_graph(folder, log_root=log_root)

    return {"schema": SCHEMA_VERSION, "exported_at": _now(),
            "folder": str(folder),
            "as_of": (as_of.iso if as_of else None),
            "contracts": out_contracts, "clauses": out_clauses,
            "obligations": out_obligations, "decision_queue": queue,
            "audit": audit, "graph": graph}


def export_state_file(folder, out_path, **kw) -> Path:
    p = Path(out_path)
    p.write_text(json.dumps(export_state(folder, **kw), ensure_ascii=False,
                            indent=1), encoding="utf-8")
    return p


# ── apply ─────────────────────────────────────────────────────────────────────

def _corrections_path(folder) -> Path:
    return Path(folder) / "contracts" / "corrections.jsonl"


def apply_actions(folder, actions: list[dict], *, log_root=None) -> dict[str, Any]:
    """Apply the workbench's queued human actions through the gated APIs.

    Action kinds:
      resolve_obligation  {obligation_id, choice: satisfied|waived, actor, rationale}
      note_obligation     {obligation_id, actor, rationale}  — disputed/cure, no state change
      bind_term           {contract_ref, term, entity_code, actor, rationale}
      record_correction   {contract_ref, field, extracted, corrected, actor, rationale}

    Every action needs actor + rationale at THIS layer too (defence in depth —
    the registries enforce it again). Failures are reported per action, never
    silently skipped."""
    obligations = ObligationRegistry(folder, log_root=log_root)
    terms = DefinedTermsRegistry(folder, log_root=log_root)
    applied, failed = [], []
    for i, a in enumerate(actions):
        kind = a.get("kind", "")
        actor = (a.get("actor") or "").strip()
        rationale = (a.get("rationale") or "").strip()
        try:
            if not actor or actor in ("system", "ingest", "scheduler"):
                raise ValueError("workbench actions need a named human actor")
            if not rationale:
                raise ValueError("workbench actions need a rationale")
            if kind == "resolve_obligation":
                if a["choice"] not in ("satisfied", "waived"):
                    raise ValueError(f"invalid choice {a.get('choice')!r}")
                obligations.resolve(a["obligation_id"], a["choice"],
                                    actor=actor, reason=rationale)
            elif kind == "note_obligation":
                obligations.annotate(a["obligation_id"], actor=actor,
                                     note=rationale)
            elif kind == "bind_term":
                terms.bind(a["contract_ref"], a["term"], a["entity_code"],
                           actor=actor)
            elif kind == "record_correction":
                rec = {"contract_ref": a["contract_ref"], "field": a["field"],
                       "extracted": a.get("extracted"), "corrected": a["corrected"],
                       "actor": actor, "rationale": rationale, "at": _now()}
                p = _corrections_path(folder)
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                try:
                    from .mutation_log import LogEvent, MutationLog
                    MutationLog(Path(folder), log_root=log_root).append(LogEvent(
                        event="system", folder_path=str(folder),
                        pair_id=f"correction:{a['contract_ref']}:{a['field']}",
                        channel="system", actor=actor,
                        extra={"kind": "workbench-action", "op": "correction", **rec}))
                except Exception:                               # noqa: BLE001
                    pass
            else:
                raise ValueError(f"unknown action kind {kind!r}")
            applied.append({"index": i, "kind": kind})
        except Exception as exc:                                # noqa: BLE001
            failed.append({"index": i, "kind": kind, "error": str(exc)})
    return {"applied": applied, "failed": failed, "ok": not failed}


def apply_actions_file(folder, actions_path, **kw) -> dict[str, Any]:
    data = json.loads(Path(actions_path).read_text(encoding="utf-8"))
    return apply_actions(folder, data.get("actions", data), **kw)


# ── demo state ────────────────────────────────────────────────────────────────

def build_demo(folder, *, log_root=None,
               tick_dates: tuple[str, ...] = ("2026-08-01", "2026-12-01")) -> dict:
    """Ingest the P1 template corpus into ``folder`` and run scheduler ticks —
    a real, regenerable state for the validation protocol. Returns a summary."""
    from .contracts.extractor import (REFERENCE_PACKS_DIR, ingest_contract,
                                     load_checklist)
    # One corpus location: reuse the canonical path so a move cannot leave a
    # second resolver pointing at the old tree. The demo and the eval gate
    # ingest the same template corpus, shipped as package data.
    from .contracts.eval import CORPUS_DIR as corpus
    langs = {"avv_de.txt": "de"}
    # The demo simulates a folder with the EU reference pack INSTALLED — the
    # checklist is loaded from pack DATA and supplied as an explicit opt-in,
    # exactly as a jurisdiction pack would. The substrate applies none itself.
    ctype, cname, checklist = load_checklist(
        REFERENCE_PACKS_DIR / "eu-gdpr-dpa.json")
    demo_checklists = {ctype: (cname, checklist)}
    ingested = []
    for f in sorted(corpus.glob("*.txt")):
        out = ingest_contract(folder, f.read_text(encoding="utf-8"),
                              contract_id=f.stem.replace("_", "-"),
                              language=langs.get(f.name, "en"),
                              source_document=f.name, log_root=log_root,
                              checklists=demo_checklists)
        ingested.append({"file": f.name, "ref": out["contract"]["ref"],
                         "obligations": len(out["obligations"]["created"])})
    sched = ObligationScheduler(folder, log_root=log_root)
    ticks = [sched.tick(Date(d)).to_dict() for d in tick_dates]
    return {"ingested": ingested, "ticks": ticks}


def main() -> int:                                              # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description="contract workbench I/O")
    ap.add_argument("command", choices=["export", "apply", "demo"])
    ap.add_argument("folder")
    ap.add_argument("--out", default="workbench-state.json")
    ap.add_argument("--actions", default="workbench-actions.json")
    args = ap.parse_args()
    if args.command == "export":
        p = export_state_file(args.folder, args.out)
        print(f"state → {p}")
    elif args.command == "apply":
        out = apply_actions_file(args.folder, args.actions)
        print(json.dumps(out, indent=1))
        return 0 if out["ok"] else 1
    else:
        out = build_demo(args.folder)
        print(json.dumps(out, indent=1))
        p = export_state_file(args.folder, args.out)
        print(f"state → {p}")
    return 0


if __name__ == "__main__":                                      # pragma: no cover
    import sys
    sys.exit(main())
