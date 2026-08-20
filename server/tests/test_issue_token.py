# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Issue-token schema for the problem-solution graph: the keystone unit
of the problem-solving computer. Any input (text/image/audio) lifts to a
typed surface carrying spans; a domain ND detector emits n issue tokens over
that surface; each token's method is a DETERMINISTIC lookup from its issue
type, never chosen per instance.

Invariants (written BEFORE the logic):
  K1  a token always cites a modality-typed span — char range / bbox /
      timecode — and round-trips through to_dict
  K2  method is a deterministic function of issue_type, validated against
      the reasoning profiles; an unknown type degrades to 'generic'
  K3  the same issue_type ALWAYS yields the same method (no per-instance
      discretion)
  K4  detection is registry-driven per domain (mirrors applicability's
      register_trigger_reader); an unknown domain yields zero tokens, never
      an error
  K5  a token carries a retrieval fingerprint built from its own fields
  K6  the reference text detector spots >=2 distinct issue types in a small
      multi-issue contract snippet, each with a cited char span
  K7  tokens project into a problem set (case_set_to_cytoscape input shape)
      — the keystone feeds the existing visualiser unchanged
"""
from __future__ import annotations


from workspaces.issue_token import (
    IssueToken, Span, assign_method, detect_issues, register_detector,
    token_to_subpayload,
)


# ── K1: span + round-trip ─────────────────────────────────────────────────────

def test_token_cites_typed_span_and_roundtrips():
    for span in (Span("text", start=10, end=42),
                 Span("image", bbox=(0.1, 0.2, 0.5, 0.6)),
                 Span("audio", t_start=12.0, t_end=18.5)):
        tok = IssueToken(issue_id="i1", issue_type="liability_cap",
                         modality=span.modality, span=span,
                         norm_anchors=["§ 309 Nr. 7 BGB"], source="c.txt")
        d = tok.to_dict()
        assert d["span"]["modality"] == span.modality
        assert d["modality"] == span.modality
        assert d["issue_type"] == "liability_cap"
        assert IssueToken.from_dict(d).to_dict() == d


# ── K2 + K3: deterministic method from type ───────────────────────────────────

def test_method_is_deterministic_from_type():
    assert assign_method("liability_cap") == "legal-de"        # subsumption
    assert assign_method("good_faith_balancing") == "generic"  # residual-heavy
    # stable: many calls, one answer
    assert len({assign_method("liability_cap") for _ in range(50)}) == 1


def test_unknown_issue_type_degrades_to_generic():
    assert assign_method("not-a-known-issue") == "generic"


def test_assigned_method_is_a_real_profile():
    from workspaces.reasoning_contract import PROFILES
    for itype in ("liability_cap", "data_processing", "ip_assignment",
                  "good_faith_balancing"):
        assert assign_method(itype) in PROFILES or assign_method(itype) == "generic"


# ── K4: per-domain detector registry ──────────────────────────────────────────

def test_unknown_domain_yields_no_tokens_not_error():
    assert detect_issues("any text", domain="no-such-domain") == []


def test_detector_is_registry_driven():
    def _toy(surface):
        return [IssueToken(issue_id="t0", issue_type="liability_cap",
                           modality="text", span=Span("text", start=0, end=4),
                           norm_anchors=[], source="")]
    register_detector("toy", _toy)
    toks = detect_issues("frob", domain="toy")
    assert len(toks) == 1 and toks[0].issue_type == "liability_cap"


# ── K5: fingerprint ───────────────────────────────────────────────────────────

def test_token_carries_retrieval_fingerprint():
    tok = IssueToken(issue_id="i", issue_type="data_processing",
                     modality="text", span=Span("text", start=0, end=9),
                     norm_anchors=["Art. 28(3)"], source="dpa.txt")
    fp = tok.fingerprint()
    assert fp["issue_type"] == "data_processing"
    assert fp["rooms"] == ["Art. 28(3)"]
    assert fp["profile"] == "legal-de"          # method carried into fp


# ── K6: reference text detector, multi-issue ──────────────────────────────────

SNIPPET = (
    "5. Liability. The Provider's total liability shall be capped at the fees "
    "paid in the preceding 12 months.\n"
    "8. Data protection. The Processor shall process personal data only on "
    "documented instructions of the Controller (Art. 28 GDPR).\n"
    "11. Intellectual property. All work product is hereby assigned to the "
    "Customer.\n")


def test_reference_detector_spots_multiple_issue_types():
    toks = detect_issues(SNIPPET, domain="contract-de")
    types = {t.issue_type for t in toks}
    assert len(types) >= 2
    assert "liability_cap" in types
    for t in toks:
        assert t.span.modality == "text"
        assert t.span.start is not None and t.span.end > t.span.start
        assert SNIPPET[t.span.start:t.span.end]      # span resolves to real text


def test_one_clause_one_issue_type_is_one_token():
    # the data-protection clause matches TWO data_processing rules (the
    # 'personal data' phrase and the 'Art. 28' reference); it must yield a
    # single token, with both anchors merged, not a duplicate per rule.
    toks = detect_issues(SNIPPET, domain="contract-de")
    keys = [(t.issue_type, t.span.start) for t in toks]
    assert len(keys) == len(set(keys))               # no duplicate clause+type
    dp = [t for t in toks if t.issue_type == "data_processing"]
    assert len(dp) == 1


# ── K7: tokens feed the existing visualiser ───────────────────────────────────

def test_tokens_project_into_a_problem_set():
    from workspaces.kg_export import case_set_to_cytoscape, validate_graph
    toks = detect_issues(SNIPPET, domain="contract-de")
    parent = {"case": {"problem": {"text": "Review this services contract"},
                       "resolution": {"type": "residual"}, "profile": "generic"},
              "inputs": {"question": "Review this services contract"}}
    subs = [token_to_subpayload(t) for t in toks]
    g = case_set_to_cytoscape(parent, subs)
    v = validate_graph(g)
    assert v["ok"], v["findings"]
    deco = [e for e in g["edges"] if e["data"]["rel_label"] == "decomposes to"]
    assert len(deco) == len(toks) >= 2
