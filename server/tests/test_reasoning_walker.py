# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Walker + phase briefs: a reasoning machine, never a judge — abstract
schemas, readings laid out, every closure originated or ratified by a human."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rvnd import legal_corpus, reasoning_phases as rp, reasoning_walker as rw
from rvnd import reasoning_contract as rc
from rvnd.reasoning_contract import ReasoningViolation
from rvnd.rule_registry import RuleRegistry

GDPR = """REGULATION (EU) 2016/679 (General Data Protection Regulation)
Article 33
1. The controller shall notify the personal data breach to the supervisory authority within 72 hours.
Article 17
1. The data subject shall have the right to obtain erasure of personal data without undue delay.
3. Paragraphs 1 and 2 shall not apply to the extent processing is necessary for compliance with a legal obligation."""


@pytest.fixture()
def registry(tmp_path):
    legal_corpus.seed_registry(tmp_path)
    reg = RuleRegistry(tmp_path, user="alex")
    reg.place_legal_text(GDPR, "gdpr", source_document="gdpr.txt")
    return reg


# ── the briefs: short, clean, profile-aware, judge-free ──────────────────────

def test_briefs_are_local_model_sized_and_clean():
    for phase in rp.PHASE_ORDER:
        b = rp.brief(phase)
        assert len(b) < 1300, f"{phase} brief too long for a small model"
        assert "JSON" in b                      # strict output format named
    assert "Norm -> Tatbestand" in rp.brief("application", profile="legal-de")
    assert "Issue -> Rule" in rp.brief("application", profile="legal-irac")


def test_application_brief_demands_abstraction_and_resolution_forbids_judging():
    a = rp.brief("application")
    assert "ABSTRACT" in a and "do NOT subsume" in a.lower().replace("do not", "do NOT")
    r = rp.brief("resolution")
    assert "NEVER decide" in r and "preferred" in r


def test_curriculum_is_one_teaching_page():
    c = rp.curriculum()
    assert len(c) < 2700
    for must in ("QUESTION", "FACTS", "NORMS", "RESOLVE", "never invent",
                 "reasoning machine", "WHITEPAPER_reasoning-pattern"):
        assert must.lower() in c.lower()


# ── scripted model ────────────────────────────────────────────────────────────

def _scripted_model(replies: dict):
    """A fake model_fn keyed on the phase header in the prompt's first line."""
    def fn(prompt: str) -> str:
        for key, reply in replies.items():
            if f"— {key.upper()}." in prompt[:40].upper():
                return json.dumps(reply)
        return "{}"
    return fn


_BREACH = {
    "QUESTION": {"question": "When must the controller notify a breach "
                             "under Regulation (EU) 2016/679?"},
    "FACTS": {"facts": [{"text": "breach detected on the CRM",
                         "source": "SIEM alert"}], "unsourced": []},
    "NORMS": {"selected": ["Art. 33(1)"], "gaps": [], "why": "breach notification"},
    "APPLICATION": {"chain": [
        {"step": "Norm", "text": "Does Art. 33(1) impose a notification duty?",
         "warrant": "wording: shall notify within 72 hours"},
        {"step": "Ergebnis", "text": "If a notifiable breach and no derogation: "
                                     "notify within 72h; otherwise: document why not",
         "warrant": "Art. 33(1) structure"}]},
    "RESOLUTION": {"readings": [
        {"id": "notify", "label": "Notify the supervisory authority within 72 hours",
         "grounds": ["Art. 33(1)"], "consequences": ["clock runs from awareness"]}]},
    "ACTION": {"actions": [
        {"obligation": "notify the supervisory authority", "actor": "controller",
         "deadline": "72h", "source_norm": "Art. 33(1) GDPR"},
        {"obligation": "do something vague", "source_norm": ""}]},   # → dropped (R5)
}


def test_walk_proposes_and_only_a_human_closes(registry):
    out = rw.walk("breach happened, what now? (Regulation (EU) 2016/679)",
                  registry=registry, model_fn=_scripted_model(_BREACH))
    case = out["case"]
    assert case.resolution["type"] == "open"            # walker emitted NO answer
    assert case.resolution["proposed"]["id"] == "notify"
    assert case.actions == []                           # nothing done pre-human
    assert all(s.get("schema") for s in case.chain)     # frame, not findings
    # the human ratifies — rationale mandatory
    with pytest.raises(ValueError):
        rw.ratify(out, registry=registry, actor="alex", rationale="")
    done = rw.ratify(out, registry=registry, actor="alex",
                     rationale="reading matches Art. 33(1) verbatim; no derogation engaged",
                     model_fn=_scripted_model(_BREACH))
    case2 = done["case"]
    assert case2.resolution["type"] == "determinate"
    assert case2.resolution["ratified_by"] == "alex"
    assert case2.contract["ok"]
    # the deontic tail is DERIVED from the held norm-span, not model prose
    act = next(a for a in case2.actions if "33(1)" in a["source_norm"])
    assert act["derived"] and act["actor"] == "controller"
    assert "72 hours" in act["deadline"]
    assert "edpb" in act["enforced_by"]                 # consequence: who enforces
    assert any("DERIVED from the deontic structure" in str(t.get("note", ""))
               for t in done["transcript"])


def test_action_model_fallback_drops_unanchored(tmp_path):
    """When nothing deontic is held, the model is asked — and its unanchored
    output is dropped visibly (R5)."""
    legal_corpus.seed_registry(tmp_path)
    reg = RuleRegistry(tmp_path, user="alex")
    model = _scripted_model({"ACTION": {"actions": [
        {"obligation": "notify someone", "source_norm": ""}]}})
    transcript = []
    actions = rw._actions_phase(model, registry=reg, question="q", answer="a",
                                rooms=["Art. 99"], profile="legal-de",
                                transcript=transcript)
    assert actions == []
    assert any("dropped unanchored action" in str(t.get("repair", ""))
               for t in transcript)


def test_walk_residual_human_decides(registry):
    model = _scripted_model({
        "QUESTION": {"question": "Erase on request despite retention duties "
                                 "(Regulation (EU) 2016/679)?"},
        "FACTS": {"facts": [{"text": "termination notice received",
                             "source": "letter 2026-05-01"}]},
        "NORMS": {"selected": ["Art. 17(1)", "Art. 17(3)"], "gaps": []},
        "APPLICATION": {"chain": [
            {"step": "Norm", "text": "Is there an erasure right?", "warrant": "Art. 17(1)"},
            {"step": "Ausnahme", "text": "Is a legal obligation engaged?",
             "warrant": "Art. 17(3)"}]},
        "RESOLUTION": {"esc_reason": "17(1) vs 17(3)", "readings": [
            {"id": "erase", "label": "Erase", "grounds": ["Art. 17(1)"],
             "consequences": ["clean exit"]},
            {"id": "retain", "label": "Retain & restrict",
             "grounds": ["Art. 17(3)"], "consequences": ["Art. 18"]}]},
        "ACTION": {"actions": [{"obligation": "restrict retained fields",
                                "actor": "processor", "deadline": "ongoing",
                                "source_norm": "Art. 18 GDPR"}]},
    })
    out = rw.walk("erasure?", registry=registry, model_fn=model,
                  oversight_level="approve", stake=True)
    case = out["case"]
    assert case.resolution["type"] == "residual"
    assert case.resolution["choice"] is None            # the walker did NOT decide
    assert any("NO choice made" in str(t.get("note", "")) for t in out["transcript"])
    done = rw.decide(out, registry=registry, chosen_option_id="retain",
                     actor="alex", rationale="§ 147 AO engages Art. 17(3)(b)",
                     model_fn=model)
    case2 = done["case"]
    assert case2.resolution["choice"]["chosen_label"] == "Retain & restrict"
    assert case2.contract["ok"] and len(case2.actions) == 1


def test_walk_stake_below_floor_raises(registry):
    model = _scripted_model({
        "RESOLUTION": {"readings": [{"id": "a", "label": "A"},
                                    {"id": "b", "label": "B"}]},
    })
    with pytest.raises(ReasoningViolation):
        rw.walk("staked question (Regulation (EU) 2016/679)", registry=registry,
                model_fn=model, oversight_level="review", stake=True)


def test_walk_without_model_is_honest_and_open(registry):
    out = rw.walk("Erasure under Regulation (EU) 2016/679?", registry=registry)
    case = out["case"]
    assert case.resolution["type"] == "open"
    assert case.contract["ok"]                          # honest ≠ malformed
    assert all(t.get("skipped") or "OPEN" in str(t.get("note", ""))
               for t in out["transcript"] if "phase" in t)


def test_unsourced_model_facts_are_quarantined_not_used(registry):
    model = _scripted_model({
        "FACTS": {"facts": [{"text": "sourced", "source": "exhibit A"},
                            {"text": "rumour", "source": ""}],
                  "unsourced": ["hearsay item"]},
        "RESOLUTION": {"readings": [], "why_open": "no facts close it"},
    })
    out = rw.walk("q (Regulation (EU) 2016/679)", registry=registry, model_fn=model)
    case = out["case"]
    assert [f.text for f in case.facts] == ["sourced"]
    assert "rumour" in case.contract["quarantined_facts"]
    assert "hearsay item" in case.contract["quarantined_facts"]


# ── information forms: what the user is shown, per oversight level ────────────

def test_oversight_information_forms_ladder():
    assert rc.oversight_form("notify")["form"] == "notice"
    assert rc.oversight_form("review")["form"] == "preview"
    assert rc.oversight_form("approve")["form"] == "decision-surface"
    assert "consequences" in rc.oversight_form("approve")["show"]
    assert rc.oversight_form("supervised")["form"] == "transcript"
    assert rc.oversight_form("manual")["form"] == "schema-only"
    assert "originates" in rc.oversight_form("manual")["interaction"]
    assert set(rc.INFORMATION_FORMS) == set(rc.LEVELS)


# ── the skill file obeys the validation rules ─────────────────────────────────

def test_skill_md_is_valid():
    p = Path(__file__).resolve().parents[2] / "plugin" / "skills" / "reasoning-walker" / "SKILL.md"
    if not p.exists():
        import pytest
        pytest.skip("plugin/ ships as a separate companion; SKILL.md not in the core repo")
    text = p.read_text(encoding="utf-8")
    m = re.search(r"^---\n(.*?)\n---", text, re.S)
    assert m, "frontmatter missing"
    desc = re.search(r"description:\s*(.+?)(?:\n[a-z_]+:|\n---)", m.group(1) + "\n---", re.S)
    assert desc
    d = desc.group(1).strip()
    assert len(d) <= 1024, f"description too long: {len(d)}"
    assert "<" not in d and ">" not in d, "no angle brackets in descriptions"


# ── the gate question: "Can I ship this product?" ─────────────────────────────

GDPR_SHIP = GDPR + """
Article 35
1. Where processing is likely to result in a high risk, the controller shall, prior to the processing, carry out a data protection impact assessment."""


def _ship_registry(tmp_path):
    legal_corpus.seed_registry(tmp_path)
    reg = RuleRegistry(tmp_path, user="alex")
    reg.place_legal_text(GDPR_SHIP, "gdpr", source_document="gdpr.txt")
    return reg


def test_gate_blocked_offers_conditions_never_ships_itself(tmp_path):
    from rvnd import problem_kg as pk
    reg = _ship_registry(tmp_path)
    sub1 = pk.build_case("Breach process in place? (Regulation (EU) 2016/679)",
                         registry=reg, required_rooms=["Art. 33(1)"],
                         answer="Yes — 72h notification path documented (Art. 33(1)).")
    sub2 = pk.build_case("DPIA done? (Regulation (EU) 2016/679)",
                         registry=reg, required_rooms=["Art. 35(1)", "Art. 36"],
                         answer="n/a")                       # Art. 36 → real gap
    out = pk.gate_case("Can we ship the recommender feature?",
                       [sub1, sub2], registry=reg, document="ship-rec-2026.md")
    case = out["case"]
    assert case.resolution["type"] == "residual"             # gaps ⇒ no auto-ship
    assert case.resolution["choice"] is None
    labels = {r["id"] for r in out["inputs"]["readings"]}
    assert labels == {"ship-conditional", "hold"}
    cond = next(r for r in out["inputs"]["readings"] if r["id"] == "ship-conditional")
    assert any("Art. 36" in c for c in cond["consequences"])         # gap → condition
    assert any("data protection impact assessment" in c.lower()
               for c in cond["consequences"])                # derived deontic duty
    # the human decides; conditions become the record
    done = rw.decide(out, registry=reg, chosen_option_id="ship-conditional",
                     actor="alex", rationale="gap is Art. 36 consultation — "
                     "not engaged at current risk level; DPIA scheduled pre-launch")
    case2 = done["case"]
    assert case2.resolution["choice"]["chosen_label"] == "Proceed with conditions"
    assert case2.contract["ok"]
    assert any(a["derived"] for a in case2.actions)          # deontic tail attached


def test_gate_all_green_is_ratified_not_auto_emitted(tmp_path):
    from rvnd import problem_kg as pk
    reg = _ship_registry(tmp_path)
    subs = [pk.build_case("Breach process? (Regulation (EU) 2016/679)", registry=reg,
                          required_rooms=["Art. 33(1)"], answer="documented"),
            pk.build_case("DPIA? (Regulation (EU) 2016/679)", registry=reg,
                          required_rooms=["Art. 35(1)"], answer="completed 2026-05")]
    out = pk.gate_case("Can we ship?", subs, registry=reg, document="ship.md")
    case = out["case"]
    assert case.resolution["type"] == "open"                 # proposed, NOT emitted
    assert case.resolution["proposed"]["id"] == "ship"
    assert case.coverage == 1.0 and case.gaps == []
    done = rw.ratify(out, registry=reg, actor="alex",
                     rationale="both regimes closed with receipts; record signed")
    assert done["case"].resolution["type"] == "determinate"
    assert done["case"].resolution["ratified_by"] == "alex"
    assert done["case"].contract["ok"]


def test_gate_facts_are_the_subcase_records(tmp_path):
    from rvnd import problem_kg as pk
    reg = _ship_registry(tmp_path)
    sub = pk.build_case("Breach process? (Regulation (EU) 2016/679)", registry=reg,
                        required_rooms=["Art. 33(1)"], answer="documented")
    out = pk.gate_case("Ship?", [sub], registry=reg, document="d.md")
    f = out["case"].facts[0]
    assert "case record" in f.source                         # evidenced by the file
    assert "DETERMINATE" in f.text


def test_r5_unresolvable_citation_escalates_never_passes_silently(tmp_path):
    from rvnd import problem_kg as pk
    reg = _ship_registry(tmp_path)
    case = pk.build_case("Notify? (Regulation (EU) 2016/679)", registry=reg,
                         required_rooms=["Art. 33(1)"], answer="72h",
                         actions=[{"obligation": "consult the authority",
                                   "actor": "controller", "deadline": "",
                                   "source_norm": "Art. 36 GDPR"}])   # not held
    assert case.contract["ok"]                               # well-formed…
    assert any(f["code"] == "RC-5" and f["level"] == "escalate"
               for f in case.contract["findings"])           # …but human-verify


def test_walker_fetches_public_law_instead_of_dumping_the_gap(registry):
    """'Responsibility doesn't mean the user is the janitor' — a cited public
    provision the corpus lacks is fetched (host-policy-bound), ingested and
    receipted; only un-fetchable gaps reach the user."""
    art34 = """REGULATION (EU) 2016/679 (General Data Protection Regulation)
Article 34
1. When the personal data breach is likely to result in a high risk to the rights and freedoms of natural persons, the controller shall communicate the personal data breach to the data subject without undue delay."""
    model = _scripted_model({
        "NORMS": {"selected": ["Art. 33(1)"], "gaps": ["Art. 34"]},
        "RESOLUTION": {"readings": []},
    })
    fetch = lambda inst, cite: ({"text": art34, "url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj"}
                                if "34" in cite else None)
    out = rw.walk("breach duties incl. Art. 34? (Regulation (EU) 2016/679)",
                  registry=registry, model_fn=model, fetch_fn=fetch)
    case = out["case"]
    assert "Art. 34" not in case.gaps                   # fetched, not dumped
    assert any("34" in g.pinpoint for g in case.grounds)
    assert any("closed by fetch" in str(t.get("note", "")) for t in out["transcript"])
