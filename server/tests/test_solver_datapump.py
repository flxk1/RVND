# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""#1 productized — the RVND data-pump over the signed audit chain. Fakes the chain
so the adapter is proven without the full server: only signed PASS runs become
training data, and the swap proposal is autonomy-graded."""
from __future__ import annotations

import json

from workspaces.adapters.solver.datapump import RvndDataPump


# a fake signed audit chain: signed PASS, signed VIOLATION, UNSIGNED pass, a dupe
_CHAIN = [
    {"problem": "p1", "candidate": "good1", "verdict": "PASS",
     "signature": "sig:aaa", "rationale": "grounded"},
    {"problem": "p1", "candidate": "bad1", "verdict": "VIOLATION",
     "signature": "sig:bbb", "rationale": "defeated"},
    {"problem": "p2", "candidate": "good2", "verdict": "APPROVED",
     "signature": "sig:ccc", "rationale": "clean"},
    {"problem": "p3", "candidate": "forged", "verdict": "PASS",
     "signature": None, "rationale": "no signature"},        # must be dropped
]


def _pump(**kw):
    return RvndDataPump(chain_reader=lambda: list(_CHAIN), **kw)


def test_only_signed_passes_become_training_examples():
    out = _pump().harvest_chain()
    ex = out["training"]["examples"]
    prompts = sorted(e["prompt"] for e in ex)
    assert prompts == ["p1", "p2"]                     # good1, good2 — signed passes
    # the unsigned "forged" PASS is dropped by signature re-check
    assert out["training"]["stats"]["dropped_unverified"] == 1


def test_preference_pair_from_pass_and_fail_on_the_same_problem():
    out = _pump().harvest_chain()
    prefs = out["training"]["preferences"]
    assert prefs == [{"prompt": "p1", "chosen": "good1", "rejected": "bad1"}]


def test_proposal_is_autonomy_graded():
    auto = _pump(oversight_level="autonomous").harvest_chain()["proposal"]
    assert auto["action"] == "train_adapter" and auto["gate"] == "auto"

    supervised = _pump(oversight_level="oversight").harvest_chain()["proposal"]
    assert supervised["action"] == "train_adapter" and supervised["gate"] == "escalate"


def test_default_pump_is_safe_and_escalates():
    # the default grade must NOT auto-apply a swap (no human silently bypassed)
    assert _pump().harvest_chain()["proposal"]["gate"] == "escalate"


def test_min_examples_threshold_blocks_a_thin_proposal():
    out = _pump(min_examples=5).harvest_chain()["proposal"]
    assert out["action"] == "none" and out["examples"] == 2


def test_injected_verifier_can_reject_beyond_signature_presence():
    # a stricter verifier: only signatures on the allow-list re-check
    strict = _pump(verifier=lambda r: r.get("signature") in {"sig:aaa"})
    ex = strict.harvest_chain()["training"]["examples"]
    assert [e["prompt"] for e in ex] == ["p1"]         # only good1 survives


def test_jsonl_is_valid_and_deterministic():
    line = _pump().jsonl().splitlines()
    assert [json.loads(x)["prompt"] for x in line] == ["p1", "p2"]
