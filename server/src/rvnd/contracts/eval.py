# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Contract-extraction eval — the P1 gate, measured not asserted.

Runs the Phase-1 intake pipeline (``contract_extractor``) over the template
gold corpus (``workspaces/data/eval/contracts/``, shipped as package data) and
scores per field class against
``gold.json``. Floors:

    parties          P >= 0.95 and R >= 0.95
    effective dates  accuracy >= 0.95
    governing law    accuracy >= 0.95
    obligations      P >= 0.80 and R >= 0.75
    predicates       P >= 0.85   (abstention allowed — recall unfloored)

The eval is a development gate, not a generalisation guarantee: it proves the
pipeline on templates; arbitrary user contracts are covered by the cold-start
posture (confidence shown, abstention, human confirmation), not by this score.

Run: ``python -m rvnd.contract_eval`` (prints the scorecard, exit 1 on a
floor breach). Pure stdlib.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .extractor import intake_contract

__all__ = ["run_eval", "FLOORS"]

# Corpus ships inside the package (workspaces/data/eval/contracts) so it survives
# a wheel install, not just a source checkout. Resolve relative to this module.
CORPUS_DIR = Path(__file__).resolve().parent.parent / "data" / "eval" / "contracts"

FLOORS = {
    "parties_precision": 0.95, "parties_recall": 0.95,
    "effective_date_accuracy": 0.95,
    "governing_law_accuracy": 0.95,
    "obligations_precision": 0.80, "obligations_recall": 0.75,
    "predicates_precision": 0.85,
}

_DUTY_MODALS = {"obligation", "prohibition"}


def _norm(s: str) -> str:
    """Whitespace-fold for keyword matching — source text wraps lines."""
    import re
    return re.sub(r"\s+", " ", s).lower()


@dataclass
class Tally:
    matched: int = 0
    gold: int = 0
    extracted: int = 0

    @property
    def precision(self) -> float:
        return self.matched / self.extracted if self.extracted else 1.0

    @property
    def recall(self) -> float:
        return self.matched / self.gold if self.gold else 1.0


def _score_parties(intake, gold_parties: list[dict], t: Tally, notes: list) -> None:
    t.gold += len(gold_parties)
    t.extracted += len(intake.instance.parties)
    unmatched = list(intake.instance.parties)
    for g in gold_parties:
        hit = next((p for p in unmatched
                    if p.role == g["role"] and g["name_kw"] in p.name.lower()), None)
        if hit is not None:
            t.matched += 1
            unmatched.remove(hit)
        else:
            notes.append(f"party MISS: {g['role']} ~ {g['name_kw']}")
    for p in unmatched:
        notes.append(f"party SPURIOUS: {p.role} = {p.name}")


def _score_obligations(intake, gold_obls: list[dict], t: Tally, notes: list) -> None:
    duties = [f for f in intake.rules if f.modal in _DUTY_MODALS]
    t.gold += len(gold_obls)
    t.extracted += len(duties)
    unmatched = list(duties)
    for g in gold_obls:
        def fits(f):
            blob = _norm(f.action + " " + f.raw_sentence)
            subj = _norm(f.subject + " " + f.raw_sentence)
            return (f.modal == g["modal"] and g["action_kw"] in blob
                    and g["subject_kw"] in subj)
        hit = next((f for f in unmatched if fits(f)), None)
        if hit is not None:
            t.matched += 1
            unmatched.remove(hit)
        else:
            notes.append(f"obligation MISS: {g['modal']} ~ {g['action_kw']}")
    for f in unmatched:
        notes.append(f"obligation SPURIOUS: {f.modal} ~ {f.raw_sentence[:70]!r}")


def _score_predicates(intake, gold_preds: list[dict], t: Tally, notes: list) -> None:
    with_struct = [f for f in intake.rules if f.condition_struct]
    t.gold += len(gold_preds)
    t.extracted += len(with_struct)
    unmatched = list(with_struct)
    for g in gold_preds:
        def fits(f):
            blob = _norm(f.action + " " + f.raw_sentence)
            if g["action_kw"] not in blob:
                return False
            s = f.condition_struct
            if "offset" in g:
                return (s.get("temporal") or {}).get("offset") == g["offset"]
            return (s.get("value") == g.get("threshold_value")
                    and s.get("unit") == g.get("threshold_unit"))
        hit = next((f for f in unmatched if fits(f)), None)
        if hit is not None:
            t.matched += 1
            unmatched.remove(hit)
        else:
            notes.append(f"predicate MISS: {g}")
    for f in unmatched:
        notes.append(f"predicate SPURIOUS: {f.condition_struct} on {f.raw_sentence[:60]!r}")


def _score_scalar(fname: str, field_name: str, extracted, gold_val,
                  abstain_ok: bool, notes: list) -> tuple[bool, bool]:
    """Score one scalar field. Returns (correct, silently_wrong).

    Semantics: a MISS (extracted nothing where gold has a value) costs
    accuracy; a WRONG value (extracted something that contradicts gold) is
    the cardinal sin and counted separately — the cold-start promise is
    "incomplete allowed, silently wrong never". When gold marks the field
    ``expect_abstain``, extracting nothing is CORRECT (the document does not
    support a confident answer); extracting the gold value, where one exists,
    also counts; anything else is silently wrong."""
    if extracted in (None, ""):
        if abstain_ok or gold_val in (None, ""):
            return True, False
        notes.append(f"{fname}: {field_name} MISS (got nothing, want {gold_val!r})")
        return False, False
    if gold_val not in (None, "") and extracted == gold_val:
        return True, False
    notes.append(f"{fname}: {field_name} SILENTLY WRONG "
                 f"(got {extracted!r}, want {gold_val!r}"
                 f"{', abstention expected' if abstain_ok else ''})")
    return False, True


def run_eval(corpus_dir: Path = CORPUS_DIR, *,
             enforce_floors: bool = True) -> dict[str, Any]:
    gold = json.loads((corpus_dir / "gold.json").read_text(encoding="utf-8"))
    parties, obls, preds = Tally(), Tally(), Tally()
    date_hits = law_hits = type_hits = n = wrong = 0
    notes: list[str] = []

    for fname, g in gold["contracts"].items():
        n += 1
        text = (corpus_dir / fname).read_text(encoding="utf-8")
        intake = intake_contract(text, language=g.get("language", "en"))
        _score_parties(intake, g["parties"], parties, notes)
        _score_obligations(intake, g["obligations"], obls, notes)
        _score_predicates(intake, g.get("predicates", []), preds, notes)
        abstain = set(g.get("expect_abstain", []))
        eff = intake.instance.effective_date
        ok_d, w_d = _score_scalar(fname, "effective_date",
                                  eff.iso if eff else None, g["effective_date"],
                                  "effective_date" in abstain, notes)
        ok_l, w_l = _score_scalar(fname, "governing_law",
                                  intake.instance.governing_law, g["governing_law"],
                                  "governing_law" in abstain, notes)
        ok_t, w_t = _score_scalar(fname, "contract_type",
                                  intake.instance.contract_type, g["contract_type"],
                                  "contract_type" in abstain, notes)
        date_hits += ok_d; law_hits += ok_l; type_hits += ok_t
        wrong += w_d + w_l + w_t

    scores = {
        "parties_precision": parties.precision, "parties_recall": parties.recall,
        "effective_date_accuracy": date_hits / n,
        "governing_law_accuracy": law_hits / n,
        "obligations_precision": obls.precision, "obligations_recall": obls.recall,
        "predicates_precision": preds.precision,
        "predicates_recall_unfloored": preds.recall,
        "contract_type_accuracy_unfloored": type_hits / n,
        "silently_wrong": wrong,
    }
    breaches = ({k: (scores[k], FLOORS[k]) for k in FLOORS if scores[k] < FLOORS[k]}
                if enforce_floors else {})
    if wrong:
        breaches["silently_wrong"] = (wrong, 0)       # zero tolerance, every tier
    return {"contracts": n, "scores": scores,
            "floors": FLOORS if enforce_floors else {"silently_wrong": 0},
            "breaches": breaches, "ok": not breaches, "notes": notes}


def run_tiers(base_dir: Path = CORPUS_DIR) -> dict[str, Any]:
    """Score all difficulty tiers. Tier 1 (clean templates, the floors) must
    pass; tiers 2 (drafting friction) and 3 (hostile input) are measured —
    their recall is a Phase-2 target, not a gate. ONE invariant spans all
    tiers: silently_wrong == 0."""
    out: dict[str, Any] = {"tiers": {}}
    out["tiers"]["tier1"] = run_eval(base_dir, enforce_floors=True)
    for tier in ("tier2", "tier3"):
        d = base_dir / tier
        if (d / "gold.json").exists():
            out["tiers"][tier] = run_eval(d, enforce_floors=False)
    out["silently_wrong_total"] = sum(
        t["scores"]["silently_wrong"] for t in out["tiers"].values())
    out["ok"] = (out["tiers"]["tier1"]["ok"]
                 and out["silently_wrong_total"] == 0)
    return out


def main() -> int:
    out = run_tiers()
    for tier, rep in out["tiers"].items():
        floored = tier == "tier1"
        print(f"\n== {tier} ({rep['contracts']} contracts"
              f"{', floors enforced' if floored else ', measured'}) ==")
        for k, v in rep["scores"].items():
            floor = FLOORS.get(k) if floored else None
            mark = ("" if floor is None
                    else ("  OK" if v >= floor else f"  BREACH (floor {floor})"))
            if k == "silently_wrong":
                mark = "  OK" if v == 0 else "  BREACH (zero tolerance)"
            print(f"  {k:36s} {v if isinstance(v, int) else round(v, 3)}{mark}")
        for note in rep["notes"]:
            print(f"  - {note}")
    print(f"\nsilently_wrong across all tiers: {out['silently_wrong_total']}")
    # Honest headline: PASS binds tier-1 floors + zero-silently-wrong only.
    # Carry the harder-tier recall so 'PASS' never reads as 'all tiers clean'.
    harder = []
    for tier in ("tier2", "tier3"):
        s = out["tiers"].get(tier, {}).get("scores", {})
        if "obligations_recall" in s:
            harder.append(f"{tier} obl-recall {round(s['obligations_recall'], 2)}")
    if out["ok"]:
        tail = (" (tier-1 floors + silently_wrong=0; "
                + "; ".join(harder) + " measured, not floored)") if harder else ""
        print("RESULT: PASS" + tail)
    else:
        print("RESULT: FAIL")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
