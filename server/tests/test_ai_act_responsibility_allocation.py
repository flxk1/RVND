# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""AI Act: allocate responsibility per ROLE and per AGENT-STEP — capability probe.

The AI Act's duties are conditional on *who you are in the value chain* (provider /
deployer / importer / distributor) and *what your system is* (risk tier). This probe feeds
real Article-level obligations through the existing machinery and asks two questions:

  A1  ROLE allocation — does each obligation land on the role that bears it? (applicability
      reads the bearer prose → role facet; group by role = the responsibility matrix.)
  A2  PER-AGENT-STEP allocation — as one AI system moves through the value chain, does each
      step's agent (in its role, with its tier) get exactly the duties it carries, and not
      another actor's? (subject_card per step + matcher → APPLIES / NOT_TRIGGERED.)
  A3  TIER discrimination — a provider of a *GPAI* model carries Art 53, NOT the high-risk
      Art 9–14 duties, and vice-versa (same role, different system → different duties).
  A4  ESCALATION / org-context — when the agent has NOT declared its risk tier, the tier-
      scoped duties are MAY_APPLY (surfaced for the human), never silently in or out. This
      is the "needs organisational context" seam: missing facet → escalate, not guess.
  A5  ROLE-AGNOSTIC prohibition — Art 5 (a prohibited practice) carries no role facet, so it
      binds every agent at every step.

All deterministic, LLM-free: applicability + subject_card + matcher only.
"""
from __future__ import annotations

import os

from rvnd import applicability as A
from rvnd import subject_card as SC
from rvnd import matcher as MT
from rvnd.matcher import Match

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

VOCAB = SC.get_vocabulary("ai-act")


def _ob(oid: str, article: str, bearer: str, condition: str, action: str, op: str = "O") -> dict:
    """A deontic-ND obligation pair in the shape applicability/matcher consume."""
    return {
        "id": oid,
        "problem": {"source_document": article, "summary": f"{bearer} {action}"},
        "solution": {"bearer": bearer, "condition": condition, "action": action,
                     "operator": op, "confidence": 1.0},
    }


# Real AI Act obligations (paraphrased), each with its bearer role + risk-tier scope.
OBLIGATIONS = [
    _ob("art5",    "Art. 5",     "", "AI systems for social scoring of natural persons",
        "shall not be placed on the market", op="F"),
    _ob("art9",    "Art. 9",     "Providers of high-risk AI systems", "",
        "establish a risk management system"),
    _ob("art11",   "Art. 11",    "Providers of high-risk AI systems", "",
        "draw up the technical documentation"),
    _ob("art14",   "Art. 14",    "Providers of high-risk AI systems", "",
        "design the system to allow human oversight"),
    _ob("art43",   "Art. 43",    "Providers of high-risk AI systems", "",
        "carry out the conformity assessment"),
    _ob("art50",   "Art. 50",    "Providers", "AI systems intended to interact with natural persons",
        "inform persons that they are interacting with an AI system"),
    _ob("art53",   "Art. 53",    "Providers of general-purpose AI models", "",
        "draw up technical documentation for the GPAI model"),
    _ob("art26",   "Art. 26",    "Deployers of high-risk AI systems", "",
        "use the system in accordance with the instructions for use"),
    _ob("art26_5", "Art. 26(5)", "Deployers of high-risk AI systems", "",
        "ensure human oversight by natural persons"),
    _ob("art23",   "Art. 23",    "Importers of high-risk AI systems", "",
        "verify the provider drew up the technical documentation"),
    _ob("art24",   "Art. 24",    "Distributors of high-risk AI systems", "",
        "verify the CE marking is affixed"),
]

ENRICHED = A.enrich_pairs([dict(o) for o in OBLIGATIONS], "ai-act")
BY_ID = {p["id"]: p for p in ENRICHED}


def _card(role=None, tier=None, areas=None) -> SC.SubjectCard:
    facets = {}
    if role:
        facets["role"] = role
    if tier:
        facets["risk_tier"] = tier
    if areas:
        facets["annex_iii_area"] = areas
    return SC.SubjectCard(domain="ai-act", facets=facets, subject_id=f"{role}-{tier}")


def _applies(card) -> set[str]:
    return {p["id"] for p in ENRICHED
            if MT.match_obligation(p, card, VOCAB).result is Match.APPLIES}


def _grouped(card) -> dict[str, set[str]]:
    out = {Match.APPLIES: set(), Match.MAY_APPLY: set(), Match.NOT_TRIGGERED: set()}
    for p in ENRICHED:
        out[MT.match_obligation(p, card, VOCAB).result].add(p["id"])
    return {k.value: v for k, v in out.items()}


# ── A1 — role allocation (the responsibility matrix) ─────────────────────────────────────
def test_role_allocation_matrix():
    by_role: dict[str, set[str]] = {}
    for p in ENRICHED:
        role = p["applicability"].get("role", "(any)")
        by_role.setdefault(role, set()).add(p["id"])
    assert by_role["provider"] == {"art9", "art11", "art14", "art43", "art50", "art53"}
    assert by_role["deployer"] == {"art26", "art26_5"}
    assert by_role["importer"] == {"art23"}
    assert by_role["distributor"] == {"art24"}
    assert by_role["(any)"] == {"art5"}          # Art 5 bears no role


# ── A2 — per agent-step allocation across the value chain ────────────────────────────────
def test_per_agent_step_allocation():
    # one high-risk AI system, five stages, each performed by an agent in its role
    provider = _card("provider", "high-risk", ["employment"])
    importer = _card("importer", "high-risk")
    distributor = _card("distributor", "high-risk")
    deployer = _card("deployer", "high-risk")

    # the provider's build/document/conformity steps carry the provider high-risk duties …
    assert _applies(provider) == {"art9", "art11", "art14", "art43", "art50", "art5"}
    # … and NOT another actor's duties
    g = _grouped(provider)
    assert {"art26", "art26_5", "art23", "art24"} <= g["not-triggered"]

    assert _applies(importer) == {"art23", "art5"}
    assert _applies(distributor) == {"art24", "art5"}
    assert _applies(deployer) == {"art26", "art26_5", "art5"}
    # the deployer is not on the hook for the provider's documentation duty
    assert "art11" in _grouped(deployer)["not-triggered"]


# ── A3 — same role, different system tier → different duties ─────────────────────────────
def test_tier_discriminates_gpai_from_high_risk():
    provider_hr = _card("provider", "high-risk")
    provider_gpai = _card("provider", "gpai")
    assert "art53" not in _applies(provider_hr)      # a high-risk-system provider: not GPAI duties
    assert "art9" in _applies(provider_hr)
    assert "art53" in _applies(provider_gpai)        # a GPAI provider: the GPAI duty
    assert "art9" not in _applies(provider_gpai)     # … but not the high-risk-system duties


# ── A4 — missing org context → escalation, never a silent guess ──────────────────────────
def test_unknown_tier_escalates_to_may_apply():
    provider_unknown = _card("provider", tier=None)   # role declared, tier NOT declared
    g = _grouped(provider_unknown)
    # the provider's OWN tier-scoped duties cannot be decided → surfaced for the human
    assert {"art9", "art11", "art14", "art43", "art53"} <= g["may-apply"]
    # role-only / role-agnostic duties are still decided
    assert {"art50", "art5"} <= g["applies"]
    # escalation is SCOPED: a known role still excludes other actors' duties — an undeclared
    # TIER does not make deployer/importer/distributor duties "maybe apply" to a provider.
    assert {"art26", "art26_5", "art23", "art24"} <= g["not-triggered"]


# ── A5 — a role-agnostic prohibition binds every agent ───────────────────────────────────
def test_prohibition_binds_every_step():
    for card in (_card("provider", "high-risk"), _card("deployer", "high-risk"),
                 _card("importer", "high-risk"), _card("distributor", "gpai"),
                 _card(role=None, tier=None)):
        assert "art5" in _applies(card)


# ── readable matrix (printed under -s) ───────────────────────────────────────────────────
def test_print_allocation_matrix():
    print("\n=== AI Act — responsibility per role ===")
    by_role: dict[str, list[str]] = {}
    for p in ENRICHED:
        by_role.setdefault(p["applicability"].get("role", "(any)"), []).append(
            p["problem"]["source_document"])
    for role, arts in by_role.items():
        print(f"  {role:<14} {', '.join(sorted(arts))}")

    print("\n=== per agent-step (one high-risk system through the value chain) ===")
    steps = [("design+build / document / conformity", _card("provider", "high-risk", ["employment"])),
             ("import into EU", _card("importer", "high-risk")),
             ("distribute", _card("distributor", "high-risk")),
             ("deploy + operate", _card("deployer", "high-risk")),
             ("provider, tier UNDECLARED", _card("provider", tier=None))]
    for label, card in steps:
        g = _grouped(card)
        ap = ", ".join(sorted(BY_ID[i]["problem"]["source_document"] for i in g["applies"]))
        may = ", ".join(sorted(BY_ID[i]["problem"]["source_document"] for i in g["may-apply"]))
        print(f"  [{card.facets.get('role'):<11} {str(card.facets.get('risk_tier')):<9}] {label}")
        print(f"       APPLIES   : {ap}")
        if may:
            print(f"       MAY-APPLY : {may}   ← needs org context (escalate)")
    assert True
