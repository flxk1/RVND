# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""LLM is allowed BY CHOICE — the opt-in, fenced enrichment path.

The deterministic extractor (LLM off) mis-reads a passive prohibition
("Candidate photos must not be shared externally." -> kind 'be_shared_externally').
This is the residual the phrasing-reliability matrix leaves red on purpose.

When a caller OPTS IN (use_llm=True) and a proposer is wired, the proposer recovers the
correct primitive ('share_candidate_photo') — but only through two fail-closed gates:
  * GROUNDED  — the proposal's quote must occur verbatim in the policy (no hallucination);
  * WELL-FORMED + Loomground-validated like every other primitive.
So this proves: (1) LLM off is unchanged/deterministic; (2) LLM on recovers what cues miss;
(3) an ungrounded proposal is refused even with the LLM on.
"""
from __future__ import annotations

from workspaces import policy_ingest as PI

PHOTO = "Candidate photos must not be shared externally."


def _prohibit_kinds(twin):
    return {p["kind"] for p in (twin.get("patch") or {}).get("prohibitions", [])}


def test_llm_off_is_deterministic_and_misses_the_passive_prohibition():
    twin = PI.ingest(PHOTO)                      # default: LLM off
    assert twin["ok"] and twin["llm_used"] is False
    assert "share_candidate_photo" not in _prohibit_kinds(twin)   # the documented gap


def test_llm_by_choice_recovers_the_correct_primitive_when_grounded():
    def proposer(text, ctx):
        # a local model would return this; the quote is copied verbatim from the policy
        return [{"declaration": "prohibit", "kind": "share candidate photo",
                 "quote": "Candidate photos must not be shared externally"}]

    twin = PI.ingest(PHOTO, use_llm=True, llm_proposer=proposer)
    assert twin["ok"] and twin["llm_used"] is True
    assert "share_candidate_photo" in _prohibit_kinds(twin)        # recovered by choice
    # SUPERSEDE: the grounded proposal REPLACES the same-sentence deterministic mis-read,
    # it does not sit beside it — so the wrong 'be_shared_externally' is gone.
    assert _prohibit_kinds(twin) == {"share_candidate_photo"}
    # provenance: the recovered primitive is tagged as LLM-origin, not silently merged
    llm_prohibitions = [p for p in twin["patch"]["prohibitions"] if p.get("origin") == "llm"]
    assert any(p["kind"] == "share_candidate_photo" for p in llm_prohibitions)
    # internal provenance is not leaked into the twin
    assert all("_src" not in p for p in twin["patch"]["prohibitions"])


def test_ungrounded_llm_proposal_is_refused_even_when_opted_in():
    def hallucinating_proposer(text, ctx):
        return [{"declaration": "prohibit", "kind": "appoint blockchain officer",
                 "quote": "the company shall appoint a blockchain officer"}]  # NOT in the policy

    twin = PI.ingest(PHOTO, use_llm=True, llm_proposer=hallucinating_proposer)
    assert twin["ok"] and twin["llm_used"] is True
    assert "appoint_blockchain_officer" not in _prohibit_kinds(twin)  # grounding fence held


def test_local_model_route_recovers_via_complete(monkeypatch):
    """use_llm=True with NOTHING wired routes to the built-in local-model proposer
    (local_llm.complete). Here we stub the local model's reply (no live endpoint needed)
    and confirm it flows through the same fence + supersede as any proposer."""
    from workspaces import local_llm
    from workspaces import models_registry as MR
    from workspaces.models_registry import ModelEntry
    # the ambient local route is capability-gated → register a capable model so the gate passes
    monkeypatch.setattr(MR, "list_models", lambda: [ModelEntry(id="qwen-32b")])

    reply = ('[{"declaration":"prohibit","kind":"share candidate photo",'
             '"quote":"Candidate photos must not be shared externally"}]')
    monkeypatch.setattr(local_llm, "complete",
                        lambda *a, **k: {"ok": True, "response": reply})

    twin = PI.ingest(PHOTO, use_llm=True)          # no explicit/default proposer → local route
    assert twin["ok"] and twin["llm_used"] is True
    assert _prohibit_kinds(twin) == {"share_candidate_photo"}   # recovered + superseded


def test_no_local_endpoint_degrades_to_deterministic(monkeypatch):
    """No endpoint configured → local_llm.complete returns ok=False → proposer yields [] →
    the run is exactly the deterministic result. Opting in never breaks the offline path."""
    from workspaces import local_llm
    from workspaces import models_registry as MR
    from workspaces.models_registry import ModelEntry
    monkeypatch.setattr(MR, "list_models", lambda: [ModelEntry(id="qwen-32b")])  # capable → reaches endpoint check
    monkeypatch.setattr(local_llm, "complete",
                        lambda *a, **k: {"ok": False, "error": "no endpoint"})

    twin = PI.ingest(PHOTO, use_llm=True)
    assert twin["ok"]
    assert _prohibit_kinds(twin) == {"be_shared_externally"}    # deterministic, no crash


def test_default_proposer_registration_is_opt_in():
    captured = {}

    def proposer(text, ctx):
        captured["called"] = True
        return []

    PI.set_default_proposer(proposer)
    try:
        PI.ingest(PHOTO)                          # use_llm=False -> proposer NOT consulted
        assert "called" not in captured
        PI.ingest(PHOTO, use_llm=True)            # opt-in -> default proposer consulted
        assert captured.get("called") is True
    finally:
        PI.set_default_proposer(None)


# ── capability gate: the AMBIENT local-model route runs only if a capable model is registered ──
def test_ambient_local_model_is_gated_off_when_no_capable_model(monkeypatch):
    # no explicit proposer → the ambient local_llm route; with nothing capable pulled, the gate
    # degrades to deterministic and REPORTS it (never silently pretends the LLM ran).
    from workspaces import models_registry as MR
    monkeypatch.setattr(PI, "_DEFAULT_PROPOSER", None)
    monkeypatch.setattr(MR, "list_models", lambda: [])           # nothing pulled → not capable
    twin = PI.ingest(PHOTO, use_llm=True)
    assert twin["ok"] and twin["llm_used"] is False              # gated off → deterministic
    assert twin["capability"] and twin["capability"]["capable"] is False
    assert twin["capability"]["action"] == "deterministic"       # the honest degrade, reported


def test_explicit_proposer_is_not_gated_by_capability(monkeypatch):
    # an explicitly-injected proposer is the caller's choice — the capability gate does NOT
    # second-guess it (so the opt-in llm_by_choice behaviour above is unchanged).
    from workspaces import models_registry as MR
    monkeypatch.setattr(MR, "list_models", lambda: [])           # no capable model, yet…
    def proposer(text, ctx):
        return [{"declaration": "prohibit", "kind": "share candidate photo",
                 "quote": "Candidate photos must not be shared externally"}]
    twin = PI.ingest(PHOTO, use_llm=True, llm_proposer=proposer)  # …explicit proposer still runs
    assert twin["llm_used"] is True and "share_candidate_photo" in _prohibit_kinds(twin)
    assert twin["capability"] is None                            # gate not consulted for an explicit proposer


def test_ambiguous_short_quote_cannot_wipe_unrelated_rules():
    # a short grounded quote ("approved by") substring-matches MANY sentences; supersede is a
    # DELETION of genuine rules, so an ambiguous quote must evict NOTHING (fail-closed).
    policy = ("Offer letters must be approved by the hr manager. "
              "Terminations must be approved by legal counsel.")

    def proposer(text, ctx):
        return [{"declaration": "reserve", "kind": "coffee order", "by": "barista",
                 "quote": "approved by"}]           # verbatim, grounded — but ambiguous

    base = PI.ingest(policy)                        # deterministic rules to protect
    kinds0 = {r["kind"] for r in base["patch"]["reservations"]}
    assert len(kinds0) >= 2

    twin = PI.ingest(policy, use_llm=True, llm_proposer=proposer)
    kinds = {r["kind"] for r in twin["patch"]["reservations"]}
    assert kinds0 <= kinds                          # NO genuine rule was evicted


def test_unambiguous_supersede_still_works():
    # the legit case — a quote identifying exactly ONE provision still replaces that
    # provision's deterministic mis-read (pinned above in the recovery test too).
    def proposer(text, ctx):
        return [{"declaration": "prohibit", "kind": "share candidate photo",
                 "quote": "Candidate photos must not be shared externally"}]
    twin = PI.ingest(PHOTO, use_llm=True, llm_proposer=proposer)
    assert {p["kind"] for p in twin["patch"]["prohibitions"]} == {"share_candidate_photo"}
