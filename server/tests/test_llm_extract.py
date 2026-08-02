# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the Lock-gated LLM extractor (Workspace operation).

Hermetic: a mock backend stands in for the local model, so no endpoint needed.
Proves the extractor emits the SAME pair schema the regex NDs do, flags
below-floor items for oversight, falls back gracefully, and honours the lock.
"""

from __future__ import annotations

import json

from workspaces.llm_extract import (
    DomainProfile,
    ExtractionResult,
    extract,
    extract_obligations_hybrid,
    _parse_json,
)
from workspaces.applicability import enrich_pairs
from workspaces.matcher import assess
from workspaces.subject_card import make_card


PROFILE = DomainProfile(
    domain="ai-act",
    vocabulary={"operator": ["O", "P", "F", "R"]},
    confidence_floor=0.7,
)


def _mock(response_text):
    """A backend returning a fixed response (the model's 'output')."""
    return lambda prompt: {"ok": True, "response": response_text,
                           "model_used": "mock-7b"}


# --- JSON robustness --------------------------------------------------------

def test_parse_plain_array():
    assert _parse_json('[{"a":1}]') == [{"a": 1}]


def test_parse_fenced_json():
    assert _parse_json('```json\n[{"a":1}]\n```') == [{"a": 1}]


def test_parse_json_with_leading_prose():
    assert _parse_json('Here is the result: [{"a":1}] done') == [{"a": 1}]


def test_parse_garbage_returns_none():
    assert _parse_json("not json at all") is None


# --- schema identity (the contract) -----------------------------------------

def test_llm_obligations_match_regex_pair_schema():
    model_out = json.dumps([
        {"operator": "O", "bearer": "providers of high-risk AI systems",
         "action": "establish a risk management system", "condition": "",
         "exception": "", "confidence": 0.9},
    ])
    res = extract("…", "obligations", PROFILE, backend=_mock(model_out))
    assert res.ok and len(res.items) == 1
    pair = res.items[0]
    # same keys the downstream pipeline expects
    assert "problem" in pair and "solution" in pair
    sol = pair["solution"]
    assert sol["operator"] == "O"
    assert sol["bearer"].startswith("providers")
    assert sol["extractor"] == "llm"
    # and it flows through enrich + assess unchanged
    enrich_pairs(res.items, "ai-act")
    card = make_card("ai-act", role="provider", risk_tier="high-risk")
    out = assess(res.items, card)
    assert any("risk management" in (m.action or "").lower() for m in out.applies)


def test_below_floor_items_are_flagged_for_oversight():
    model_out = json.dumps([
        {"operator": "O", "bearer": "x", "action": "do something",
         "confidence": 0.5},   # below the 0.7 floor
    ])
    res = extract("…", "obligations", PROFILE, backend=_mock(model_out))
    assert res.items[0]["solution"]["below_floor"] is True


def test_above_floor_items_not_flagged():
    model_out = json.dumps([
        {"operator": "F", "bearer": "the provider", "action": "place on market",
         "confidence": 0.95},
    ])
    res = extract("…", "obligations", PROFILE, backend=_mock(model_out))
    assert res.items[0]["solution"]["below_floor"] is False


def test_invalid_operator_defaults_to_O():
    model_out = json.dumps([{"operator": "Z", "bearer": "x", "action": "y",
                             "confidence": 0.8}])
    res = extract("…", "obligations", PROFILE, backend=_mock(model_out))
    assert res.items[0]["solution"]["operator"] == "O"


# --- graceful fallback ------------------------------------------------------

def test_backend_unavailable_returns_not_ok():
    res = extract("…", "obligations", PROFILE,
                  backend=lambda p: {"ok": False, "error": "no endpoint"})
    assert res.ok is False
    assert res.items == []


# --- lock -----------------------------------------------------------------

def test_lock_refusal_aborts_call():
    called = {"n": 0}

    def counting_backend(p):
        called["n"] += 1
        return {"ok": True, "response": "[]"}

    res = extract("confidential text", "obligations", PROFILE,
                  backend=counting_backend,
                  lock=lambda t: {"action": "refuse"})
    assert res.locked is True
    assert res.ok is False
    assert called["n"] == 0          # the model was never called


def test_lock_allow_proceeds():
    res = extract("ok text", "obligations", PROFILE,
                  backend=_mock("[]"),
                  lock=lambda t: {"action": "allow"})
    assert res.ok is True


# --- hybrid cost discipline -------------------------------------------------

def test_hybrid_prefers_regex_when_present():
    called = {"n": 0}

    def backend(p):
        called["n"] += 1
        return {"ok": True, "response": "[]"}

    regex_pairs = [{"id": "r1", "problem": {}, "solution": {"operator": "O"}}]
    out = extract_obligations_hybrid("…", PROFILE, regex_pairs=regex_pairs,
                                     backend=backend)
    assert out == regex_pairs
    assert called["n"] == 0          # LLM not called when regex sufficed


def test_hybrid_falls_to_llm_when_regex_thin():
    model_out = json.dumps([{"operator": "O", "bearer": "x", "action": "y",
                             "confidence": 0.8}])
    out = extract_obligations_hybrid("…", PROFILE, regex_pairs=[],
                                     backend=_mock(model_out))
    assert len(out) == 1
    assert out[0]["solution"]["extractor"] == "llm"


# --- facets target ----------------------------------------------------------

def test_facets_target_returns_parsed_object():
    model_out = json.dumps({"role": "deployer", "risk_tier": "high-risk",
                            "_confidence": {"role": 0.9}})
    res = extract("we deploy a hiring AI", "facets",
                  DomainProfile(domain="ai-act"), backend=_mock(model_out))
    assert res.ok
    assert res.items and res.items[0]["role"] == "deployer"
