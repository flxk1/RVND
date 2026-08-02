# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Human-readable layer: names and titles, not spans — and gaps that are
either closed by evidence or owned by a signed human waiver."""

from __future__ import annotations

import pytest

from workspaces import humanize_legal as hl, legal_corpus, problem_kg as pk
from workspaces.rule_registry import RuleRegistry

GDPR = """REGULATION (EU) 2016/679 (General Data Protection Regulation)
Article 33
1. In the case of a personal data breach, the controller shall without undue delay and, where feasible, not later than 72 hours after having become aware of it, notify the personal data breach to the supervisory authority."""


@pytest.fixture()
def registry(tmp_path):
    legal_corpus.seed_registry(tmp_path)
    reg = RuleRegistry(tmp_path, user="alex")
    reg.place_legal_text(GDPR, "gdpr", source_document="gdpr.txt")
    return reg


def test_citations_speak_human(registry):
    assert hl.expand_citation("Art. 33(1)") == "Article 33(1)"
    assert hl.expand_citation("§ 147(1)") == "§ 147(1)"      # German form IS human
    d = hl.describe_pinpoint(registry, "Art. 33(1)")
    assert d["citation"] == "Article 33(1)" and d["held"]
    assert d["instrument"] == "General Data Protection Regulation"
    assert "personal data breach" in d["gist"]
    assert hl.describe_pinpoint(registry, "Art. 99")["held"] is False


def test_posture_names_the_library_and_owns_the_scope(registry):
    p = hl.corpus_posture(registry)
    assert p["total_provisions"] == 1
    assert p["documents"][0]["name"] == "General Data Protection Regulation"
    assert "Your library" in p["line"] and "nothing else" in p["line"]
    # empty library says so, with the verb that fixes it
    import tempfile
    tmp2 = tempfile.mkdtemp(); legal_corpus.seed_registry(tmp2)
    empty = RuleRegistry(tmp2, user="alex")
    assert "empty" in hl.corpus_posture(empty)["line"]


def test_information_form_leads_with_names_never_spans(registry):
    case = pk.build_case("Notify? (Regulation (EU) 2016/679)", registry=registry,
                         required_rooms=["Art. 33(1)", "Art. 34"], answer="72h")
    form = hl.render_information_form("review", case.to_dict(), registry)
    assert form["form"] == "preview"
    joined = " ".join(form["lines"])
    assert "General Data Protection Regulation" in joined
    assert "Article 33(1)" in joined
    assert "not in your library" in joined                  # the gap, in words
    assert "Your library" in form["posture"]
    assert form["coverage_pct"] == 50 and form["gaps_open"] == 1


def test_gap_waiver_is_owned_signed_and_visible(registry, tmp_path):
    case = pk.build_case("Notify? (Regulation (EU) 2016/679)", registry=registry,
                         required_rooms=["Art. 33(1)", "Art. 34"], answer="72h")
    assert "Art. 34" in case.gaps
    with pytest.raises(Exception):                          # rationale mandatory
        pk.waive_gap(case, "Art. 34", registry=registry, actor="alex", rationale="")
    pk.waive_gap(case, "Art. 34", registry=registry, actor="alex",
                 rationale="no high-risk to data subjects at current scope (Art. 34(1) threshold)")
    assert case.gaps == []
    assert case.waivers[0]["gap"] == "Art. 34" and case.waivers[0]["actor"] == "alex"
    with pytest.raises(ValueError):                         # can't waive twice
        pk.waive_gap(case, "Art. 34", registry=registry, actor="alex", rationale="x")
    # contract: a waiver without actor/rationale is a violation
    from workspaces import reasoning_contract as rc
    bad = case.to_dict(); bad["waivers"] = [{"gap": "Art. 34", "actor": "", "rationale": ""}]
    assert any(f.code == "RC-1" for f in rc.check_case(bad).violations)


def test_record_renders_titles_posture_and_waivers(registry):
    case = pk.build_case("Notify? (Regulation (EU) 2016/679)", registry=registry,
                         required_rooms=["Art. 33(1)", "Art. 34"], answer="72h")
    pk.waive_gap(case, "Art. 34", registry=registry, actor="alex",
                 rationale="threshold not met")
    html = pk.render_case_record_html([case], document="d.md", registry=registry)
    for must in ("General Data Protection Regulation", "Article 33(1)",
                 "Your library", "Gaps waived (signed)", "owned by"):
        assert must in html, must
