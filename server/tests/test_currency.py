# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Currency / validity-dating pipeline.

Covers CELEX extraction, dated status (in-force / not-yet / superseded /
unknown), the no-guessing rule, and refresh-as-graph-query with dependent
edges.
"""

from datetime import date
import rvnd.currency as cur


def _pair(pid, *, cited):
    return {"id": pid, "problem": {"id": f"{pid}-p"},
            "solution": {"id": pid, "cited_sources": cited}}


_REG = cur.CurrencyRegistry.from_rows([
    {"celex": "32016R0679", "in_force_from": "2018-05-25",
     "consolidation_version": "2016-05-04"},                          # GDPR, in force
    {"celex": "32022L2555", "in_force_from": "2023-01-16"},           # NIS2
    {"celex": "31995L0046", "in_force_from": "1995-12-13",
     "superseded_by": "32016R0679", "superseded_from": "2018-05-25"}, # Directive 95/46, repealed by GDPR
    {"celex": "32099R9999", "in_force_from": "2099-01-01"},           # future act
])


def test_extract_celex():
    assert cur.extract_celex(["CELEX:32024R1689 Art.6(2)"]) == "32024R1689"
    assert cur.extract_celex(["Art. 33 GDPR"]) is None


def test_status_in_force_superseded_future_unknown():
    asof = date(2024, 1, 1)
    assert cur.validity_status(_REG.get("32016R0679"), asof) == "in-force"
    assert cur.validity_status(_REG.get("31995L0046"), asof) == "superseded"
    assert cur.validity_status(_REG.get("32099R9999"), asof) == "not-yet-in-force"
    assert cur.validity_status(None, asof) == "unknown"           # never guessed


def test_attach_validity_writes_only_dates_no_relationship():
    p = _pair("p1", cited=["CELEX:32016R0679 Art.33"])
    cur.attach_validity(p, _REG, as_of=date(2024, 1, 1))
    v = p["solution"]["validity"]
    assert v["status"] == "in-force"
    assert v["in_force_from"] == "2018-05-25"
    # The pipeline must not invent a relationship — only date fields + status.
    assert set(v) == {"celex", "status", "in_force_from", "consolidation_version",
                      "superseded_by", "superseded_from", "as_of", "source"}


def test_unknown_celex_not_guessed():
    p = _pair("p2", cited=["CELEX:39999R0000 Art.1"])
    cur.attach_validity(p, _REG, as_of=date(2024, 1, 1))
    assert p["solution"]["validity"]["status"] == "unknown"
    assert p["solution"]["validity"]["in_force_from"] is None


def test_refresh_flags_superseded_and_dependent_edges():
    pairs = [
        _pair("old", cited=["CELEX:31995L0046 Art.7"]),    # superseded
        _pair("gdpr", cited=["CELEX:32016R0679 Art.6"]),   # in force
        _pair("future", cited=["CELEX:32099R9999 Art.1"]), # not yet
    ]
    edges = [
        {"subject": "assessment-1", "object": "old", "predicate": "rests-on"},
        {"subject": "assessment-2", "object": "gdpr", "predicate": "rests-on"},
    ]
    res = cur.refresh(pairs, _REG, as_of=date(2024, 1, 1), edges=edges)
    assert res.superseded == ["old"]
    assert res.not_yet_in_force == ["future"]
    assert res.needs_review is True
    # The edge resting on the superseded pair is surfaced; the in-force one is not.
    objs = {e["object"] for e in res.affected_edges}
    assert objs == {"old"}


def test_refresh_clean_when_all_current():
    pairs = [_pair("gdpr", cited=["CELEX:32016R0679 Art.6"])]
    res = cur.refresh(pairs, _REG, as_of=date(2024, 1, 1))
    assert res.needs_review is False
    assert res.superseded == [] and res.not_yet_in_force == []
