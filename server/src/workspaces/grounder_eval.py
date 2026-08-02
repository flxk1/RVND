# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Grounder support-gate evaluation — scores a model against the gold-set.

The gate (``workspace_grounder.check_claim_support``) classifies claim/quote pairs
as ``supports | does_not_support | insufficient``. Before any model's verdicts
are trusted in production, it must clear the bar on the gold-set (conformance
data owned by the language kit; resolved via ``loomground_assets``):

  * **accuracy on decided pairs ≥ 0.90** — where the model decided
    (predicted ``supports`` or ``does_not_support``), it must match gold;
    deciding on a gold-``insufficient`` pair is an overconfidence error;
  * **zero fatal inversions** — gold ``does_not_support`` predicted as
    ``supports`` is the failure mode that admits refuted claims; one is
    too many;
  * **decided fraction ≥ 0.50** — a model cannot pass by escalating
    everything; escalation is safe but unhelpful beyond a point.

Predicting ``insufficient`` on a decided gold label is *safe escalation*:
it never counts as wrong, only against the decided fraction. This mirrors
the privacy-benchmark posture (2026-05-19): INSUFFICIENT routes to
human/cloud, never passes.

Pure stdlib; the model is injected as a callable so the harness is testable
without an endpoint and usable with ``workspaces.local_llm.classify`` when one
is configured.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from .loomground_assets import grounder_gold_path

GOLD_DEFAULT = grounder_gold_path()

LABELS = ("supports", "does_not_support", "insufficient")

BAR = {"accuracy_decided_min": 0.90,
       "fatal_inversions_max": 0,
       "decided_fraction_min": 0.50}


def load_gold(path: str | Path = GOLD_DEFAULT) -> list[dict]:
    """Labelled rows only (the leading ``_meta`` row is skipped)."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if "_meta" in r:
            continue
        if r.get("label") not in LABELS:
            raise ValueError(f"row {r.get('id')}: bad label {r.get('label')!r}")
        rows.append(r)
    return rows


def validate_gold(path: str | Path = GOLD_DEFAULT) -> dict:
    """Structural checks: size, label coverage, unique ids, non-empty fields."""
    rows = load_gold(path)
    ids = [r["id"] for r in rows]
    by_label: dict[str, int] = {}
    for r in rows:
        by_label[r["label"]] = by_label.get(r["label"], 0) + 1
        for fld in ("claim", "quote", "rationale", "domain"):
            if not r.get(fld, "").strip():
                raise ValueError(f"row {r['id']}: empty {fld}")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate ids in gold-set")
    return {"rows": len(rows), "by_label": by_label,
            "meets_minimum": len(rows) >= 32 and len(by_label) == 3}


def classify_with_local_llm(claim: str, quote: str, *,
                            model: str = "") -> str:
    """Default model adapter: route one pair through the local-LLM endpoint.
    Returns a label, or ``insufficient`` if the endpoint answers garbage
    (never invent a decision out of a transport problem... a transport
    *failure* raises instead, so the caller can distinguish)."""
    from .local_llm import classify
    res = classify(
        "CLAIM: " + claim + "\nQUOTE: " + quote
        + "\nDoes the quote support the claim?",
        list(LABELS), model=model or None)
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "local-LLM classify failed"))
    cat = res.get("category", "")
    return cat if cat in LABELS else "insufficient"


def evaluate(classify_fn: Callable[[str, str], str], *,
             gold_path: str | Path = GOLD_DEFAULT,
             bar: Optional[dict] = None) -> dict:
    """Run every gold pair through ``classify_fn(claim, quote) -> label`` and
    score against the bar. Returns the full per-case record so a failing run
    is debuggable, plus the verdict."""
    bar = dict(BAR, **(bar or {}))
    rows = load_gold(gold_path)
    cases = []
    decided = correct_decided = fatal = 0
    for r in rows:
        pred = classify_fn(r["claim"], r["quote"])
        if pred not in LABELS:
            pred = "insufficient"
        is_decided = pred != "insufficient"
        ok: Optional[bool]
        if not is_decided:
            ok = None if r["label"] != "insufficient" else True
        else:
            decided += 1
            ok = pred == r["label"]
            correct_decided += int(ok)
            if r["label"] == "does_not_support" and pred == "supports":
                fatal += 1
        cases.append({"id": r["id"], "gold": r["label"], "pred": pred,
                      "ok": ok, "difficulty": r.get("difficulty", "")})
    n = len(rows)
    accuracy_decided = (correct_decided / decided) if decided else 0.0
    decided_fraction = decided / n if n else 0.0
    passed = (accuracy_decided >= bar["accuracy_decided_min"]
              and fatal <= bar["fatal_inversions_max"]
              and decided_fraction >= bar["decided_fraction_min"])
    return {"cases": cases, "total": n, "decided": decided,
            "accuracy_decided": round(accuracy_decided, 4),
            "decided_fraction": round(decided_fraction, 4),
            "fatal_inversions": fatal,
            "escalations": n - decided,
            "bar": bar, "passed": passed,
            "failures": [c for c in cases if c["ok"] is False]}


def evaluate_local_llm(*, model: str = "",
                       gold_path: str | Path = GOLD_DEFAULT) -> dict:
    """Convenience: evaluate the configured local endpoint."""
    return evaluate(
        lambda c, q: classify_with_local_llm(c, q, model=model),
        gold_path=gold_path)


if __name__ == "__main__":                      # pragma: no cover
    import sys
    res = evaluate_local_llm(model=sys.argv[1] if len(sys.argv) > 1 else "")
    res.pop("cases")
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["passed"] else 1)
