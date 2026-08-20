# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fingerprint-driven prompt planning: an LLM-usage optimiser that
reads problem-solution fingerprints to decide, per issue token, whether to
call a model at all and which tier — then assembles the MINIMAL prompt.

Three compounding savings, all from the fingerprint:
  * recall hit  → reuse the prior solution, ZERO model tokens
  * method      → route cheap (local) vs expensive (hosted) by issue type
  * rooms       → send only the token's norm anchors, never the corpus

Invariants (written BEFORE the logic):
  Q1  a token with a high-evidence recall → action 'reuse', 0 est tokens,
      and it names the prior solver
  Q2  a cold token with rooms + a subsumption method → 'local' when a local
      tier is available
  Q3  a judgment method (generic / residual-heavy) → 'hosted' even when local
      is available — cheap models don't do open balancing
  Q4  the assembled prompt carries ONLY the token's rooms, never the corpus,
      and only the per-phase briefs (no curriculum dump)
  Q5  local tier unavailable → local-routed tokens fall back to 'hosted',
      never invent a tier
  Q6  the plan reports a token budget and a POSITIVE saving vs the naive
      single-prompt-with-whole-corpus baseline when reuse/local are chosen
  Q7  deterministic — same inputs, same plan
  Q8  end to end over real detected tokens
"""
from __future__ import annotations


from workspaces.issue_token import IssueToken, Span, detect_issues
from workspaces.prompt_plan import plan_prompts


def _tok(itype="liability_cap", rooms=("§ 309 Nr. 7 BGB",)):
    return IssueToken(issue_id=f"{itype}@0", issue_type=itype, modality="text",
                      span=Span("text", start=0, end=10),
                      norm_anchors=list(rooms), source="", text="clause")


def _no_recall(_tok):
    return []


def test_recall_hit_reuses_for_zero_tokens():                      # Q1
    def recall(t):
        return [{"solver": "skill:liability-nd", "evidence": 3, "receipts": []}]
    plan = plan_prompts([_tok()], recall_fn=recall,
                        tiers={"local": True, "hosted": True})
    e = plan["entries"][0]
    assert e["action"] == "reuse"
    assert e["est_tokens"] == 0
    assert e["reuse_solver"] == "skill:liability-nd"


def test_cold_subsumption_token_routes_local():                   # Q2
    plan = plan_prompts([_tok(itype="liability_cap")], recall_fn=_no_recall,
                        tiers={"local": True, "hosted": True})
    assert plan["entries"][0]["action"] == "local"


def test_judgment_method_routes_hosted_even_with_local():         # Q3
    plan = plan_prompts([_tok(itype="good_faith_balancing", rooms=())],
                        recall_fn=_no_recall,
                        tiers={"local": True, "hosted": True})
    assert plan["entries"][0]["action"] == "hosted"


def test_prompt_carries_only_token_rooms_and_phase_briefs():      # Q4
    plan = plan_prompts([_tok(rooms=("§ 309 Nr. 7 BGB",))],
                        recall_fn=_no_recall,
                        tiers={"local": True, "hosted": True},
                        corpus_rooms=["Art. 5", "Art. 6", "§ 280 BGB", "x"])
    spec = plan["entries"][0]["prompt"]
    assert spec["rooms"] == ["§ 309 Nr. 7 BGB"]            # not the corpus
    assert "Art. 5" not in spec["rooms"]
    assert spec["phases"]                                   # per-phase briefs
    assert "curriculum" not in spec


def test_local_unavailable_falls_back_to_hosted():               # Q5
    plan = plan_prompts([_tok(itype="liability_cap")], recall_fn=_no_recall,
                        tiers={"local": False, "hosted": True})
    assert plan["entries"][0]["action"] == "hosted"
    assert plan["entries"][0]["fallback"] == "local→hosted"


def test_plan_reports_positive_saving_vs_naive():               # Q6
    def recall(t):
        if t.issue_type == "liability_cap":
            return [{"solver": "skill:x", "evidence": 5, "receipts": []}]
        return []
    toks = [_tok("liability_cap"), _tok("data_processing", ("Art. 28(3)",))]
    plan = plan_prompts(toks, recall_fn=recall,
                        tiers={"local": True, "hosted": True},
                        corpus_rooms=["r%d" % i for i in range(400)])
    assert plan["naive_tokens"] > plan["planned_tokens"]
    assert plan["saving_pct"] > 0
    assert plan["summary"]["reuse"] == 1


def test_plan_is_deterministic():                                # Q7
    a = plan_prompts([_tok()], recall_fn=_no_recall,
                     tiers={"local": True, "hosted": True})
    b = plan_prompts([_tok()], recall_fn=_no_recall,
                     tiers={"local": True, "hosted": True})
    assert a == b


def test_end_to_end_over_detected_tokens():                      # Q8
    snippet = ("5. Liability capped at fees.\n"
               "8. Personal data processed on instructions (Art. 28 GDPR).\n"
               "20. Governing law is Germany.\n")
    toks = detect_issues(snippet, domain="contract-de")
    assert len(toks) >= 2
    plan = plan_prompts(toks, recall_fn=_no_recall,
                        tiers={"local": True, "hosted": True})
    assert len(plan["entries"]) == len(toks)
    assert all(e["action"] in ("reuse", "local", "hosted")
               for e in plan["entries"])
    assert plan["planned_tokens"] > 0
