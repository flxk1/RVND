# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""End-to-end on REAL AI Act prose: identify roles/risks/duties, triage deterministic vs
interpreter, allocate, and close the interpreter loop.

The fixture is faithful operative text from Regulation (EU) 2024/1689 (EU law is public),
deliberately mixing the two phrasings the Act uses:
  * ACTIVE, explicit-role  — "Providers/Deployers/Importers/Distributors … shall …"
  * AGENTLESS PASSIVE      — "a risk management system shall be established", "… shall be
                             drawn up", "… shall be prohibited" (subject = patient, not addressee)

Claims:
  D1  the deterministic layer IDENTIFIES role · risk · operator · duty from the active,
      explicit-role articles (no hand-tagging — read from the prose);
  D2  the agentless-passive / unparsed articles are ROUTED TO THE INTERPRETER (role=None,
      a reason given) — never given a guessed role (transcribe, don't judge);
  D3  off-surface tier (the "high-risk" in Art 24's action) is flagged, not invented;
  D4  the resolved duties ALLOCATE per role / agent-step via the matcher (reuse);
  D5  the interpreter loop CLOSES — ratify supplies the role/operator the surface withheld
      (rationale required, origin=interpreter-ratified), and the duty then allocates too.
"""
from __future__ import annotations

import os

import pytest

from rvnd import duty_identification as DI
from rvnd import subject_card as SC
from rvnd import matcher as MT
from rvnd.matcher import Match

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
VOCAB = SC.get_vocabulary("ai-act")

AI_ACT = {
    "Art. 5":  "AI systems that deploy subliminal techniques beyond a person's consciousness "
               "in order to materially distort behaviour shall be prohibited.",
    "Art. 9":  "A risk management system shall be established, implemented, documented and "
               "maintained in relation to high-risk AI systems.",
    "Art. 11": "The technical documentation of a high-risk AI system shall be drawn up before "
               "that system is placed on the market.",
    "Art. 16": "Providers of high-risk AI systems shall ensure that their systems undergo the "
               "relevant conformity assessment procedure.",
    "Art. 23": "Importers of high-risk AI systems shall ensure that the provider has drawn up "
               "the technical documentation.",
    "Art. 24": "Distributors shall verify that the high-risk AI system bears the required CE marking.",
    "Art. 26": "Deployers of high-risk AI systems shall take appropriate technical and "
               "organisational measures to ensure they use such systems in accordance with the "
               "instructions for use.",
    "Art. 50": "Providers shall ensure that natural persons are informed that they are "
               "interacting with an AI system.",
    "Art. 53": "Providers of general-purpose AI models shall draw up and keep up to date the "
               "technical documentation of the model.",
}


def _all_duties() -> dict[str, DI.IdentifiedDuty]:
    out: dict[str, DI.IdentifiedDuty] = {}
    for art, text in AI_ACT.items():
        out[art] = DI.identify_duties(text, source=art)[0]
    return out


def test_deterministic_identifies_role_risk_duty():                    # D1
    d = _all_duties()
    assert (d["Art. 16"].role, d["Art. 16"].risk_tier, d["Art. 16"].operator) == ("provider", "high-risk", "O")
    assert (d["Art. 26"].role, d["Art. 26"].risk_tier) == ("deployer", "high-risk")
    assert (d["Art. 23"].role, d["Art. 23"].risk_tier) == ("importer", "high-risk")
    assert (d["Art. 53"].role, d["Art. 53"].risk_tier) == ("provider", "gpai")
    assert d["Art. 50"].role == "provider" and d["Art. 50"].risk_tier is None  # tier-agnostic
    for art in ("Art. 16", "Art. 26", "Art. 23", "Art. 53", "Art. 50"):
        assert d[art].action and not d[art].needs_interpreter        # duty read, no escalation


def test_passive_articles_route_to_interpreter():                      # D2
    d = _all_duties()
    # agentless passive / unparsed → interpreter queue, role NOT guessed
    for art in ("Art. 5", "Art. 9", "Art. 11"):
        assert d[art].needs_interpreter is True
        assert d[art].role is None
        assert d[art].interpreter_reason                              # an auditable why
    # Art 11 is the subtle one: the RISK is on the surface, the ROLE is not
    assert d["Art. 11"].risk_tier == "high-risk"
    assert "agentless passive" in d["Art. 11"].interpreter_reason


def test_off_surface_tier_flagged_not_invented():                      # D3
    d = _all_duties()
    assert d["Art. 24"].role == "distributor"        # role read from the surface
    assert d["Art. 24"].risk_tier is None            # tier sat in the action, not the bearer
    assert d["Art. 24"].tier_unresolved is True      # flagged for verification, not fabricated


def test_resolved_duties_allocate_per_role():                          # D4
    t = DI.triage([_all_duties()[a] for a in AI_ACT])
    resolved_pairs = [d.to_pair() for d in t.resolved]
    provider = SC.SubjectCard(domain="ai-act", facets={"role": "provider", "risk_tier": "high-risk"})
    deployer = SC.SubjectCard(domain="ai-act", facets={"role": "deployer", "risk_tier": "high-risk"})

    def applies(card):
        return {p["id"] for p in resolved_pairs
                if MT.match_obligation(p, card, VOCAB).result is Match.APPLIES}

    assert "Art. 16" in applies(provider) and "Art. 50" in applies(provider)
    assert "Art. 26" in applies(deployer)
    assert "Art. 26" not in applies(provider)        # cross-role exclusion holds
    assert "Art. 16" not in applies(deployer)


def test_interpreter_loop_closes():                                    # D5
    d = _all_duties()
    # the interpreter reads what the surface withheld, and must give a rationale
    with pytest.raises(ValueError):
        DI.ratify(d["Art. 9"], role="provider")      # no rationale → refused (no silent resolve)

    DI.ratify(d["Art. 9"], role="provider",
              rationale="Art. 9 RMS is the provider's duty for high-risk systems")
    DI.ratify(d["Art. 11"], role="provider",
              rationale="Art. 11 technical documentation is drawn up by the provider")
    DI.ratify(d["Art. 5"], operator="F",
              rationale="Art. 5 is a prohibited practice — role-agnostic, binds everyone")

    for art in ("Art. 9", "Art. 11", "Art. 5"):
        assert d[art].needs_interpreter is False
        assert d[art].origin == "interpreter-ratified"          # auditable, not merged silently

    # the ratified provider duties now allocate to a provider exactly like the deterministic ones
    provider = SC.SubjectCard(domain="ai-act", facets={"role": "provider", "risk_tier": "high-risk"})
    pairs = [d["Art. 9"].to_pair(), d["Art. 11"].to_pair()]
    assert all(MT.match_obligation(p, provider, VOCAB).result is Match.APPLIES for p in pairs)
    # the role-agnostic prohibition binds the provider too (no role trigger)
    assert MT.match_obligation(d["Art. 5"].to_pair(), provider, VOCAB).result is Match.APPLIES


def test_print_pipeline():
    print("\n=== AI Act → identify roles/risks/duties (deterministic) + interpreter triage ===")
    duties = [_all_duties()[a] for a in AI_ACT]
    t = DI.triage(duties)
    print("  RESOLVED by the deterministic layer (role · tier · op):")
    for x in t.resolved:
        tier = x.risk_tier or ("tier?" if x.tier_unresolved else "—")
        print(f"    {x.source:<8} {x.operator}  role={x.role:<11} tier={tier:<9} {x.action[:42]}")
    print("  → INTERPRETER QUEUE (role/risk/duty the surface withheld):")
    for x in t.interpreter_queue:
        print(f"    {x.source:<8} {x.interpreter_reason}")
    assert True
