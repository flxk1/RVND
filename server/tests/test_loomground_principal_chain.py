# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Gate: Rvnd's engine implements the Loomground v0.7 principal chain.

Self-contained mirror of the published `obo-*` / `party-inheritance` /
`reject-obo-*` / `reject-delegation-ungranted-delegator` conformance vectors
(Rvnd carries its own engine, so its gate carries its own vectors). The
on-behalf-of relation must name a declared actor or human, carry at most one
delegator per actor, and be acyclic — each violation ill-formed at apply. The
binding projects as `on_behalf_of` on the delegate's node; a partyless delegate
projects the nearest declared party along the chain. A human delegator anchors
answerability and constrains no grant: no-amplification (risk and grade) ranges
over actor→actor links only, and a delegator's empty risk set over a kind at a
gate makes any delegate grant there amplification.
"""
from __future__ import annotations

from rvnd import loomground_lang as L


def _tok(kind: str = "deploy") -> dict:
    return {"id": "t", "kind": kind, "risk": "low", "party": "p", "provenance": []}


def _project(text: str) -> dict:
    patch = L.parse(text)
    v = L.validate(patch)
    assert v["ok"], v["errors"]
    return L.project(patch)


def _reject_at_apply(text: str) -> list[str]:
    patch = L.parse(text)          # must parse; the rejection is apply-stage
    v = L.validate(patch)
    assert not v["ok"]
    return v["errors"]


# ── positive: the chain round-trips, attenuates, and roots in a person ────────

def test_obo_projection():
    # the delegation binding is part of the canonical form: the delegate's node
    # projects on_behalf_of naming its delegator
    obs = _project(
        "actor boss\n"
        "actor sub on-behalf-of boss\n"
        "gate g risk low grant boss[deploy:low] sub[deploy:low]\n"
        "cord g -> master\n")
    sub = next(n for n in obs["nodes"] if n["id"] == "sub")
    assert sub["on_behalf_of"] == "boss"
    boss = next(n for n in obs["nodes"] if n["id"] == "boss")
    assert "on_behalf_of" not in boss


def test_obo_chain_attenuation():
    # a three-link chain is well-formed when the pairwise subset invariant holds
    # along it (bot's deploy risks ⊆ mgr's ⊆ ceo's at g)
    obs = _project(
        "actor ceo\n"
        "actor mgr on-behalf-of ceo\n"
        "actor bot on-behalf-of mgr\n"
        "gate g risk low grant ceo[deploy:low,high] mgr[deploy:low,high] bot[deploy:low]\n"
        "cord g -> master\n")
    assert next(n for n in obs["nodes"] if n["id"] == "bot")["on_behalf_of"] == "mgr"


def test_obo_human_root_confers_no_authority():
    # a chain terminating at a human is well-formed; the binding anchors
    # answerability and constrains no grant — bot releases on its own grant
    text = ("human alice role dpo\n"
            "actor bot on-behalf-of alice\n"
            "gate g risk low grant bot[deploy:low]\n"
            "cord g -> master\n")
    patch = L.parse(text)
    v = L.validate(patch)
    assert v["ok"], v["errors"]
    assert next(n for n in L.project(patch)["nodes"] if n["id"] == "bot")["on_behalf_of"] == "alice"
    tp = {"activations": [{"actor": "bot", "source": "g", "token": _tok()}]}
    out = L.evaluate(patch, tp)
    assert out["g"] == {"verdict": "auto", "master": "act"}
    assert L.evaluate_log(patch, tp) == [{"gate": "g", "verdict": "auto"}]


def test_human_delegator_exempt_from_grade_attenuation():
    # grade no-amplification ranges over actor→actor links only: a graded
    # delegate under a human delegator is well-formed
    obs = _project(
        "human alice role dpo\n"
        "actor bot grade L3 on-behalf-of alice\n"
        "gate g risk low grant bot[deploy:low]\n"
        "cord g -> master\n")
    assert next(n for n in obs["nodes"] if n["id"] == "bot")["grade"] == "L3"


def test_party_inheritance_nearest_declared_wins():
    # mgr (partyless) resolves through to ceo's corp; sub's declared ops wins
    # over the chain; bot takes the nearest declared party (sub's ops, not corp)
    obs = _project(
        "actor ceo party corp\n"
        "actor mgr on-behalf-of ceo\n"
        "actor sub on-behalf-of mgr party ops\n"
        "actor bot on-behalf-of sub\n"
        "gate g risk low grant ceo[deploy:low] mgr[deploy:low] sub[deploy:low] bot[deploy:low]\n"
        "cord g -> master\n")
    party = {n["id"]: n.get("party") for n in obs["nodes"]}
    assert party["ceo"] == "corp"
    assert party["mgr"] == "corp"
    assert party["sub"] == "ops"
    assert party["bot"] == "ops"


def test_partyless_chain_projects_no_party():
    # no party declared anywhere on the chain: the delegate stays partyless
    obs = _project(
        "actor boss\n"
        "actor sub on-behalf-of boss\n"
        "gate g risk low grant boss[deploy:low] sub[deploy:low]\n"
        "cord g -> master\n")
    assert all("party" not in n for n in obs["nodes"] if n["class"] == "actor")


# ── negative: ill-formed at apply (validate) ──────────────────────────────────

def test_reject_obo_cycle():
    # a <-> b cycles the principal chain; equal risk sets satisfy the subset
    # check both ways, so only the acyclicity rule rejects
    errs = _reject_at_apply(
        "actor a on-behalf-of b\n"
        "actor b on-behalf-of a\n"
        "gate g risk low grant a[deploy:low] b[deploy:low]\n"
        "cord g -> master\n")
    assert any("cycle" in e for e in errs)


def test_reject_obo_self_delegation():
    # the transitive closure must be irreflexive: self-delegation is a cycle
    errs = _reject_at_apply(
        "actor a on-behalf-of a\n"
        "gate g risk low grant a[deploy:low]\n"
        "cord g -> master\n")
    assert any("cycle" in e for e in errs)


def test_reject_obo_undeclared():
    # the delegate holds no grant, so only the declared-delegator rule rejects
    errs = _reject_at_apply(
        "actor a on-behalf-of ghost\n"
        "actor b\n"
        "gate g risk low grant b[deploy:low]\n"
        "cord g -> master\n")
    assert any("ghost" in e for e in errs)


def test_reject_obo_names_a_gate():
    # a delegator must be an actor or a human, never a gate
    errs = _reject_at_apply(
        "actor a on-behalf-of g\n"
        "gate g risk low grant a[deploy:low]\n"
        "cord g -> master\n")
    assert any("declared actor or human" in e for e in errs)


def test_reject_obo_duplicate():
    # both named delegators are declared and no grant amplifies; only the
    # at-most-one rule rejects
    errs = _reject_at_apply(
        "actor boss\n"
        "actor chief\n"
        "actor sub on-behalf-of boss on-behalf-of chief\n"
        "gate g risk low grant boss[deploy:low] chief[deploy:low] sub[deploy:low]\n"
        "cord g -> master\n")
    assert any("more than one delegator" in e for e in errs)


def test_reject_delegation_ungranted_delegator():
    # the empty-set corner of no-amplification: boss holds no grant at g, so
    # its risk set over deploy there is empty and any grant to sub amplifies
    errs = _reject_at_apply(
        "actor boss\n"
        "actor sub on-behalf-of boss\n"
        "gate g risk low grant sub[deploy:low]\n"
        "cord g -> master\n")
    assert any("amplifies" in e for e in errs)


def test_reject_bare_delegate_grant_without_covering_bare_grant():
    # a bare delegate grant spans every kind at every risk; a delegator granted
    # only a specific kind does not cover it
    errs = _reject_at_apply(
        "actor boss\n"
        "actor sub on-behalf-of boss\n"
        "gate g risk low grant boss[deploy:low] sub\n"
        "cord g -> master\n")
    assert any("bare grant" in e for e in errs)
