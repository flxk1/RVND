# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Policy resolve — the rule graph as the policy pipeline.

Covers the facets→graph bridge (plain prose becomes retrievable norms), the
front-door twin shape, the fast regex fallback for prose the extractor misses,
and the honest degrade when no local model is available (deterministic resolve
runs, the reasoning step is marked unavailable, the walker is never called).

Run: ``python3 -m pytest tests/test_policy_resolve.py`` from ``server/``.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

from rvnd import policy_resolve as PR
from rvnd import problem_kg
from rvnd import rule_extractor as RE
from rvnd import governance_chat as GC
from rvnd.rule_registry import RuleRegistry

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

_POLICY = ("Providers must log every automated decision. "
           "The system shall not process biometric data.")

# The adversarial single-sentence policy: an actor-subject ("the system") that is
# not a recognised regulated entity, and a "must never" negation the shallow
# drafter reads as an appeals right. The resolver must engage on it, not fall
# back to the drafter.
_FLAGSHIP = "The system must never deploy to production without human review."

_NO_MODEL = SimpleNamespace(capable=False, reason="no capable local model for interpretation")


def _registry(tmp_path):
    return RuleRegistry(tmp_path / "ws", user="t",
                        user_root=tmp_path / "user", log_root=tmp_path / "log")


def test_bridge_places_one_norm_span_per_facet(tmp_path):
    reg = _registry(tmp_path)
    PR.resolve(_POLICY, registry=reg, capability=_NO_MODEL)
    anchor = PR.policy_anchor(reg)
    placed = reg.rules_at(anchor)
    # two operative sentences → two norm-spans, all kind="norm" (the walker's filter)
    assert len(placed) == 2
    assert {r["kind"] for r in placed} == {"norm"}


def test_placed_norms_are_retrievable_by_a_citation_free_question(tmp_path):
    # the exact thing that returns 0 before the bridge: retrieval by the policy
    # anchor, without the question citing any instrument.
    reg = _registry(tmp_path)
    PR.resolve(_POLICY, registry=reg, capability=_NO_MODEL)
    anchor = PR.policy_anchor(reg)
    assert len(problem_kg._norm_spans_for(reg, {anchor})) == 2


def test_degrade_is_honest_and_never_calls_the_walker(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("walker must not run without a capable model")

    monkeypatch.setattr(PR.reasoning_walker, "walk", _boom)
    reg = _registry(tmp_path)
    twin = PR.resolve(_POLICY, registry=reg, capability=_NO_MODEL)
    assert twin["reasoning"]["available"] is False
    assert twin["reasoning"]["reason"]
    # the deterministic ledger is still populated — the resolve ran
    assert len(twin["classification"]["express"]) == 2
    assert twin["resolved_norms"] and twin["applied"] is False


def test_empty_facets_fall_back_to_the_regex_drafter(tmp_path):
    # bare-`right` prose the extractor misses must still reach a twin.
    prose = "Users have the right to appeal a decision."
    from rvnd import rule_extractor
    assert rule_extractor.extract_rules(prose) == []
    reg = _registry(tmp_path)
    twin = PR.resolve(prose, registry=reg, capability=_NO_MODEL)
    assert twin["ok"] is True
    # the drafter path carries no resolver blocks — it is the fallback, stated as such
    assert "reasoning" not in twin
    assert "resolved_norms" not in twin


def test_twin_shape_matches_the_front_door(tmp_path):
    reg = _registry(tmp_path)
    twin = PR.resolve(_POLICY, registry=reg, capability=_NO_MODEL)
    assert twin["ok"] is True and twin["applied"] is False
    c = twin["classification"]
    assert set(c) == {"express", "host", "policy", "unmapped"}
    assert twin.get("netlist") and twin.get("patch") is not None
    # express is facet-derived: the norms decide what the governor absorbs
    assert any("prohibit" in e for e in c["express"])
    assert any("gate" in e for e in c["express"])


def test_a_prohibition_sentence_is_still_withheld_from_the_residual(tmp_path):
    # The span fix must not weaken the withholding it replaced: the prohibition's
    # own sentence stays out of the residual compile, so the netlist cannot carry
    # a shallow re-reading of a clause the facets already absorbed.
    reg = _registry(tmp_path)
    twin = PR.resolve(_FLAGSHIP, registry=reg, capability=_NO_MODEL)
    c = twin["classification"]
    handed = " ".join(c["host"] + c["policy"] + c["unmapped"]).lower()
    assert "deploy to production" not in handed
    assert "redress" not in twin["netlist"]


def test_model_step_runs_the_walker_over_the_placed_norms(tmp_path):
    calls = {"n": 0}

    def fake_model(prompt: str) -> str:
        calls["n"] += 1
        return "{}"

    reg = _registry(tmp_path)
    twin = PR.resolve(_POLICY, registry=reg, model_fn=fake_model,
                      capability=SimpleNamespace(capable=True, reason="ok"))
    assert calls["n"] > 0                       # the walker asked the model
    assert twin["reasoning"]["available"] is True
    # retrieval reached the placed norms → the case has coverage over held rooms
    assert twin["reasoning"]["coverage"] == 1.0
    assert twin["reasoning"]["gaps"] == []


def test_governance_chat_routes_policy_through_the_resolver():
    # folder=None → an ephemeral isolated registry; the route still runs the resolver.
    r = GC.chat(_POLICY)
    assert r["intent"] == "policy" and r["kind"] == "twin"
    twin = r["result"]
    assert twin["ok"] and twin["patch"] is not None
    # the resolver path is taken: it carries the reasoning block the drafter lacks
    assert "reasoning" in twin and "resolved_norms" in twin


def test_must_never_is_a_prohibition_not_a_swallowed_obligation():
    # Latent modal-inversion regression: ungated, "must never deploy" must classify
    # as a prohibition with the negation stripped from the action — not an
    # obligation whose action begins "never deploy".
    rules = RE.extract_rules(_FLAGSHIP, gated_by_fingerprint=False)
    assert len(rules) == 1
    f = rules[0]
    assert f.modal == "prohibition"
    assert f.action == "deploy to production without human review"
    assert not f.action.startswith("never")


def test_flagship_engages_the_resolver_not_the_drafter(tmp_path):
    # The exact adversarial input: it must reach the graph (a norm placed) and
    # carry the resolver blocks, never fall through to the regex drafter.
    reg = _registry(tmp_path)
    twin = PR.resolve(_FLAGSHIP, registry=reg, capability=_NO_MODEL)
    assert "reasoning" in twin and twin["resolved_norms"]
    placed = reg.rules_at(PR.policy_anchor(reg))
    assert len(placed) == 1 and placed[0]["kind"] == "norm"
    assert placed[0]["norm"]["modal"] == "prohibition"


def test_flagship_ledger_and_netlist_are_a_prohibition_not_appeals(tmp_path):
    # The shallow drafter mis-read this as express=['redress x by appeals']. The
    # resolver's ledger AND the netlist it would apply must both be the deploy
    # prohibition — no phantom appeals node survives into the apply artifact.
    reg = _registry(tmp_path)
    twin = PR.resolve(_FLAGSHIP, registry=reg, capability=_NO_MODEL)
    express = twin["classification"]["express"]
    assert len(express) == 1 and express[0].startswith("prohibit")
    assert not any("appeal" in e.lower() for e in express)
    netlist = twin["netlist"]
    assert "prohibit deploy_to_production_without_human_review" in netlist
    assert "redress" not in netlist and "appeals" not in netlist
    assert not twin["patch"].get("redress")
