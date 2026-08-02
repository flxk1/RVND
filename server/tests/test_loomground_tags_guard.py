# SPDX-License-Identifier: AGPL-3.0-only
"""loomground v0.5 amended — the `tags`-guard (J2-ratified upstream, commit 2399070).

A guard may test a token's declared, non-id `tags` by membership
(`when tags contains <tag>`) — the information-flow / data-lineage family. Additive
and backward-compatible. This also closes a pre-existing gap: validate() now enforces
the §6 guard-field domain (no-id wall) — `when id = …` / `when provenance …` are
ill-formed at apply stage. Mirrors the ratifier loomground_b.mjs; cross-checkable.
"""
from __future__ import annotations

from workspaces.loomground_lang import parse, validate, validate_token, evaluate


def _tok(kind="transfer", tags=None, risk="low"):
    t = {"id": "t1", "kind": kind, "risk": risk, "party": "a", "provenance": []}
    if tags is not None:
        t["tags"] = tags
    return t


def _patch(extra=""):
    return parse(
        "actor a\n"
        "human dpo role data-protection\n"
        "gate g risk low grant a\n"
        "cord a -> g\n"
        "cord g -> master\n"
        "reserve transfer by dpo when tags contains non_eu\n" + extra)


# ── positive: the guard selects the reservation only when the tag is present ──
def test_tagged_token_reserves_and_master_withholds():
    p = _patch()
    assert validate(p)["ok"], validate(p)["errors"]
    r = evaluate(p, {"activations": [{"token": _tok(tags=["non_eu"]), "source": "g"}]})
    assert r["g"]["verdict"] == "reserved"
    assert r["g"]["master"] == "withhold"


def test_untagged_token_runs_auto_and_master_acts():
    p = _patch()
    r = evaluate(p, {"activations": [{"token": _tok(tags=["eu"]), "source": "g"}]})
    assert r["g"]["verdict"] == "auto"
    assert r["g"]["master"] == "act"


def test_absent_tags_is_treated_as_empty_no_new_restriction():
    p = _patch()
    r = evaluate(p, {"activations": [{"token": _tok(tags=None), "source": "g"}]})
    assert r["g"]["verdict"] == "auto" and r["g"]["master"] == "act"


# ── strictest-wins membership across a pipe propagates to the terminal/master ──
def test_tag_reserved_propagates_strictest_wins_through_pipe():
    p = parse(
        "actor a\nhuman dpo role d\n"
        "gate g1 risk low grant a\ngate g2 risk low\n"
        "cord a -> g1\ncord g1 -> g2\ncord g2 -> master\n"
        "reserve move by dpo when tags contains non_eu\n")
    assert validate(p)["ok"], validate(p)["errors"]
    r = evaluate(p, {"activations": [{"token": _tok(kind="move", tags=["non_eu"]), "source": "g1"}]})
    assert r["g2"]["verdict"] == "reserved"      # joined strictest-wins to the terminal
    assert r["g2"]["master"] == "withhold"


# ── the no-id wall (apply stage): a guard over id/provenance is ill-formed ──
def test_guard_over_id_is_rejected():
    p = parse("human d role d\ngate g risk low\ncord g -> master\nreserve x by d when id = t1\n")
    v = validate(p)
    assert v["ok"] is False
    assert any("no-id wall" in e or "id" in e for e in v["errors"])


def test_guard_over_provenance_is_rejected():
    p = parse("human d role d\ngate g risk low\ncord g -> master\nreserve x by d when provenance contains s\n")
    assert validate(p)["ok"] is False


def test_unknown_operator_for_field_is_rejected():
    p = parse("human d role d\ngate g risk low\ncord g -> master\nreserve x by d when tags = non_eu\n")
    assert validate(p)["ok"] is False           # tags admits only `contains`


# ── v0.6: a tag-guard on a prohibition is VALID and prohibits the matched subset ──
# (Loomground §6 / the `prohibit-tags` conformance vector). The earlier J4 hold that
# rejected it is lifted; the guarded prohibition fires only on the tagged token.
def test_tag_guard_on_prohibition_is_valid_and_matches_subset():
    p = parse("actor a\ngate g1 risk high grant a\ngate g2 risk high grant a\n"
              "prohibit deploy when tags contains untrusted_model\n"
              "cord a -> g1\ncord a -> g2\ncord g1 -> master\ncord g2 -> master\n")
    v = validate(p)
    assert v["ok"] is True, v["errors"]
    tp = {"activations": [
        {"actor": "a", "source": "g1", "token": _tok(kind="deploy", tags=["untrusted_model"])},
        {"actor": "a", "source": "g2", "token": _tok(kind="deploy", tags=["vetted"])},
    ]}
    out = evaluate(p, tp)
    assert out["g1"]["verdict"] == "prohibited"   # tagged → severed
    assert out["g2"]["verdict"] == "auto"          # untagged → unprohibited, releasable


# ── token shape: tags optional; when present a list of strings ──
def test_validate_token_tags_optional_and_typed():
    assert validate_token(_tok(tags=None)) is True
    assert validate_token(_tok(tags=["non_eu", "synthetic"])) is True
    assert validate_token(_tok(tags="non_eu")) is False        # not a list
    assert validate_token(_tok(tags=[1, 2])) is False          # not strings
