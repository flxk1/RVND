# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Workspace Grounder — attribution boundary tests.

The invariant under test: no citation, no claim. Plus idempotency, twin
verification, provenance tracing (cycle-safe), swarm frontier, citation
styles, coverage report, and the creator→entity bridge.
"""

from __future__ import annotations

import json

import pytest

from workspaces.workspace_grounder import (
    CITATION_STYLES,
    GroundingLedger,
    format_citation,
    ground,
)


@pytest.fixture()
def ledger(tmp_path):
    return GroundingLedger(tmp_path, log_root=tmp_path / "log")


def _work(**over):
    base = dict(title="Attention Is All You Need",
                type="article",
                creators=[{"name": "Vaswani, Ashish"},
                          {"name": "Shazeer, Noam"}],
                container="NeurIPS",
                publisher="Curran Associates",
                date="2017",
                url="https://arxiv.org/abs/1706.03762")
    base.update(over)
    return base


def test_mcp_register_work_reports_failure_with_public_type_parameter(
        tmp_path, monkeypatch):
    """The public ``type`` argument must not break the exception path."""
    from workspaces.mcp_impl import grounder_register_work

    def fail_register(*args, **kwargs):
        raise ValueError("invalid work")

    monkeypatch.setattr(GroundingLedger, "register_work", fail_register)
    result = grounder_register_work(str(tmp_path), "Broken", type="policy")
    assert result == {"ok": False, "error": "ValueError: invalid work"}


def test_work_gets_a_canonical_urn_from_its_doi(ledger):
    w = ledger.register_work(**_work(doi="10.5555/3295222.3295349"))
    assert w["canonical_urn"] == "urn:lg:doi:10.5555/3295222.3295349"


def test_work_canonical_urn_uses_any_identifier_namespace(ledger):
    # a legal work carries its CELEX; no scheme is privileged over another
    w = ledger.register_work(**_work(identifiers={"celex": "32024R1689"}))
    assert w["canonical_urn"] == "urn:lg:celex:32024r1689"


def test_work_without_an_identifier_falls_back_to_a_source_key(ledger):
    w = ledger.register_work(**_work(url="", doi=""))
    assert w["canonical_urn"] == "urn:lg:source:attention-is-all-you-need"


def test_work_carries_tags_and_confidence(ledger):
    w = ledger.register_work(**_work(tags=["topic:ml", "jurisdiction:EU"],
                                     confidence="filename-derived"))
    assert w["tags"] == ["jurisdiction:EU", "topic:ml"]     # stored sorted
    assert w["confidence"] == "filename-derived"


def test_batch_registers_many_works_with_one_deferred_flush(tmp_path):
    led = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    with led.batch():
        led.register_work(title="A", url="https://x.test/a")
        led.register_work(title="B", url="https://x.test/b")
        # flush is deferred inside the batch — nothing persisted to the sink yet,
        # and the second register must not reload state and drop the unflushed first
        assert len(GroundingLedger(tmp_path, log_root=tmp_path / "log").works) == 0
        assert len(led.works) == 2
    # a fresh open sees both works after the batch flushes once on exit
    reopened = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    assert len(reopened.works) == 2
    assert {w["url"] for w in reopened.works.values()} == {"https://x.test/a",
                                                           "https://x.test/b"}


# ── works ─────────────────────────────────────────────────────────────────────

def test_register_work_idempotent(ledger):
    a = ledger.register_work(**_work())
    b = ledger.register_work(**_work())
    assert a["status"] == "created"
    assert b["status"] == "updated"
    assert a["id"] == b["id"]
    assert len(ledger.works) == 1


def test_register_work_fills_blanks_never_overwrites(ledger):
    a = ledger.register_work(**_work(publisher=""))
    b = ledger.register_work(**_work(publisher="Curran Associates"))
    assert b["publisher"] == "Curran Associates"
    c = ledger.register_work(**_work(publisher="Someone Else"))
    assert c["publisher"] == "Curran Associates"   # first honest value wins
    assert a["id"] == c["id"]


def test_creators_as_strings_normalised(ledger):
    r = ledger.register_work(title="T", creators=["Ada Lovelace"], url="https://x.test/t")
    assert r["creators"] == [{"name": "Ada Lovelace"}]


def test_persistence_roundtrip(tmp_path):
    led = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    led.register_work(**_work())
    led2 = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    assert len(led2.works) == 1
    # persisted through the versum sink and reloaded intact
    assert next(iter(led2.works.values()))["title"] == "Attention Is All You Need"


# ── no citation, no claim ─────────────────────────────────────────────────────

def test_claim_refused_without_work(ledger):
    res = ledger.ground_claim("Transformers use self-attention.", [])
    assert res["status"] == "refused"
    assert "no citation, no claim" in res["reason"]


def test_claim_refused_with_unknown_work(ledger):
    res = ledger.ground_claim("Some claim.", ["work:doesnotexist"])
    assert res["status"] == "refused"


def test_claim_grounded_with_known_work(ledger):
    w = ledger.register_work(**_work())
    res = ledger.ground_claim("Transformers use self-attention.", [w["id"]],
                              confidence=0.9)
    assert res["status"] == "created"
    assert res["work_ids"] == [w["id"]]
    assert res["confidence"] == 0.9


def test_refusal_is_audited(tmp_path):
    led = GroundingLedger(tmp_path, log_root=tmp_path / "log")
    led.ground_claim("Ungrounded claim.", [])
    from workspaces.mutation_log import MutationLog
    events = list(MutationLog(tmp_path, log_root=tmp_path / "log").replay())
    kinds = [e.extra.get("kind") for e in events]
    assert "grounding-refusal" in kinds
    assert any(e.event == "reject" for e in events)


# ── twin mode ─────────────────────────────────────────────────────────────────

def test_twin_independent_confirmation_verifies(ledger):
    w = ledger.register_work(**_work())
    a = ledger.ground_claim("Claim X.", [w["id"]], method="twin", agent="twin-a")
    assert a["status"] == "created" and a["work_ids"]
    b = ledger.ground_claim("Claim X.", [w["id"]], method="twin", agent="twin-b")
    assert b["status"] == "updated"
    assert ledger.claims[a["id"]]["status"] == "verified"
    assert "twin-b" in ledger.claims[a["id"]]["verified_by"]


def test_disputed_claim_is_residual(ledger):
    w = ledger.register_work(**_work())
    c = ledger.ground_claim("Contested claim.", [w["id"]])
    res = ledger.set_claim_status(c["id"], "disputed", by="twin-b",
                                  note="twin-b found contrary source")
    assert res["status"] == "disputed"
    assert c["id"] in ledger.coverage()["disputed_residuals"]


def test_unknown_status_rejected(ledger):
    w = ledger.register_work(**_work())
    c = ledger.ground_claim("Claim.", [w["id"]])
    with pytest.raises(ValueError):
        ledger.set_claim_status(c["id"], "blessed")


# ── provenance + swarm ────────────────────────────────────────────────────────

def _chain(ledger):
    a = ledger.register_work(title="Survey 2024", url="https://x.test/a")
    b = ledger.register_work(title="Paper 2020", url="https://x.test/b")
    c = ledger.register_work(title="Origin 1948", url="https://x.test/c",
                             creators=[{"name": "Shannon, Claude"}])
    ledger.add_provenance(a["id"], "cites", b["id"], evidence="ref [12]")
    ledger.add_provenance(b["id"], "derives_from", c["id"])
    return a, b, c


def test_provenance_requires_registered_works(ledger):
    a = ledger.register_work(title="A", url="https://x.test/a")
    res = ledger.add_provenance(a["id"], "cites", "work:ghost")
    assert res["status"] == "unknown-work"


def test_provenance_unknown_relation(ledger):
    a = ledger.register_work(title="A", url="https://x.test/a")
    b = ledger.register_work(title="B", url="https://x.test/b")
    with pytest.raises(ValueError):
        ledger.add_provenance(a["id"], "vibes_with", b["id"])


def test_trace_reaches_root_and_entities(ledger):
    a, b, c = _chain(ledger)
    res = ledger.trace(a["id"])
    assert res["status"] == "ok"
    assert res["roots"] == [c["id"]]
    ents = res["root_entities"][0]
    assert ents["creators"] == ["Shannon, Claude"]


def test_trace_cycle_safe(ledger):
    a, b, c = _chain(ledger)
    ledger.add_provenance(c["id"], "cites", a["id"])     # close the loop
    res = ledger.trace(a["id"])
    assert res["status"] == "ok"
    assert any(step.get("cycle") for chain in res["chains"] for step in chain)


def test_frontier_lists_untraced_works(ledger):
    a, b, c = _chain(ledger)
    frontier_ids = {r["id"] for r in ledger.frontier()["frontier"]}
    assert frontier_ids == {c["id"]}                      # a and b are traced
    ledger.add_provenance(c["id"], "cites", b["id"])
    assert ledger.frontier()["count"] == 0


# ── citation styles ───────────────────────────────────────────────────────────

def test_all_styles_render_nonempty():
    w = _work()
    w["id"] = "work:x"
    for style in CITATION_STYLES:
        out = format_citation(w, style)
        assert "Attention Is All You Need" in out
        assert "Vaswani" in out


def test_apa_shape():
    w = dict(_work(), id="work:x")
    out = format_citation(w, "apa")
    assert out.startswith("Vaswani, A., & Shazeer, N. (2017).")
    assert "https://arxiv.org/abs/1706.03762" in out


def test_doi_preferred_over_url():
    w = dict(_work(), id="work:x", doi="10.1000/xyz")
    assert "https://doi.org/10.1000/xyz" in format_citation(w, "apa")


def test_missing_fields_omitted_not_invented():
    w = {"id": "work:x", "title": "Untitled note", "creators": [],
         "container": "", "publisher": "", "date": "", "url": "", "doi": ""}
    out = format_citation(w, "apa")
    assert "n.d." in out                                  # honest, not invented
    assert "None" not in out


def test_unknown_style_rejected():
    with pytest.raises(ValueError):
        format_citation(dict(_work(), id="work:x"), "fancy")


def test_bibliography_numbered_for_ieee(ledger):
    ledger.register_work(**_work())
    ledger.register_work(title="Zeta", creators=[{"name": "Zorn, Max"}],
                         url="https://x.test/z", date="2001")
    bib = ledger.bibliography(style="ieee")
    assert bib["count"] == 2
    assert bib["entries"][0]["citation"].startswith("[1] ")
    apa = ledger.bibliography(style="apa")
    assert not apa["entries"][0]["citation"].startswith("[")


# ── coverage + entity bridge ──────────────────────────────────────────────────

def test_coverage_reports_attribution_gaps(ledger):
    w1 = ledger.register_work(**_work())
    w2 = ledger.register_work(title="Anon post", url="https://x.test/anon")
    ledger.ground_claim("Claim.", [w1["id"]])
    cov = ledger.coverage()
    assert cov["works"] == 2 and cov["claims"] == 1
    assert w2["id"] in cov["works_missing_creators"]
    assert 0.0 <= cov["attribution_completeness"] <= 1.0


def test_link_creators_to_corpus(ledger):
    ledger.register_work(**_work())
    res = ledger.link_creators_to_corpus()
    assert res["status"] == "ok"
    assert {l["name"] for l in res["linked"]} == {"Vaswani, Ashish",
                                                  "Shazeer, Noam"}
    from workspaces.legal_corpus import EntityRegistry
    reg = EntityRegistry(ledger.folder, log_root=ledger.log_root)
    names = {e["name"] for e in reg.entities.values()}
    assert "Vaswani, Ashish" in names
    # entity refs written back onto the work
    w = next(iter(ledger.works.values()))
    assert len(w["entity_refs"]) == 2


# ── one-shot convenience ──────────────────────────────────────────────────────

def test_ground_one_shot(tmp_path):
    res = ground(str(tmp_path), "Self-attention scales as O(n^2).",
                 [_work()], style="chicago", confidence=0.8,
                 log_root=str(tmp_path / "log"))
    assert res["claim"]["status"] == "created"
    assert len(res["citations"]) == 1
    assert "Vaswani" in res["citations"][0]


def test_ground_one_shot_refuses_without_works(tmp_path):
    res = ground(str(tmp_path), "Ungrounded.", [],
                 log_root=str(tmp_path / "log"))
    assert res["claim"]["status"] == "refused"
    assert res["citations"] == []


def test_org_creator_never_name_split():
    w = {"id": "work:x", "title": "Regulation (EU) 2024/1689",
         "creators": [{"name": "European Parliament and Council", "role": "org"}],
         "container": "", "publisher": "", "date": "2024", "url": "https://x.test/r",
         "doi": ""}
    for style in CITATION_STYLES:
        assert "European Parliament and Council" in format_citation(w, style)


def test_grounds_is_a_valid_provenance_relation(ledger):
    # obligation --grounds--> source work travels the grounding ledger
    ob = ledger.register_work(title="Obligation record", url="https://x.test/ob")
    src = ledger.register_work(**_work())
    res = ledger.add_provenance(ob["id"], "grounds", src["id"])
    assert res.get("status") not in ("unknown-relation",)
    assert "grounds" in json.dumps(ledger.provenance)


def test_policy_is_a_registrable_work_type(ledger):
    w = ledger.register_work(title="Acceptable-use policy", type="policy",
                             url="https://x.test/aup")
    assert w["type"] == "policy"
