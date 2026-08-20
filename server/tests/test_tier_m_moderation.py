# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tier M — policy-driven moderation detective beside Tier B/C in lock_text.

Opt-in via the folder policy's `moderation_rules` (no rules → no-op). Deterministic
banned_terms/banned_patterns always enforce; declared `categories` REQUIRE a
classifier backend and FAIL CLOSED when it is unavailable (the D8 contract). Every
Tier-M finding is high severity → lock_text refuses → the gate routes that refuse to
a person under oversight. Findings never carry the matched/scanned text. Lock/Shield
panel."""
from __future__ import annotations

import pytest

from workspaces.lock import tier_m as TM
from workspaces.lock.core import lock_text
from workspaces.lock.gate import gate_for_cloud
from workspaces.lock.l0_bridge import PolicySnapshot, _load_policy_inprocess
from workspaces.lock.oversight import OversightLevel
from workspaces.policy import FolderPolicy, InvalidPolicy, save_policy, load_policy


# ── no-op when unconfigured (opt-in; legacy folders unaffected) ───────────────

@pytest.mark.parametrize("rules", [None, {}])
def test_no_rules_is_a_noop(rules):
    assert TM.tier_m_check_moderation("anything at all", rules=rules) == []
    # lock_text without rules behaves exactly as before (no Tier-M findings).
    dec = lock_text("perfectly benign words", moderation_rules=rules)
    assert dec.action == "allow"


def test_requires_real_backend_only_for_categories():
    assert TM.tier_m_requires_real_backend({"banned_terms": ["x"]}) is False
    assert TM.tier_m_requires_real_backend({"categories": ["hate"]}) is True
    assert TM.tier_m_requires_real_backend(None) is False


# ── deterministic layer (no backend) ──────────────────────────────────────────

def test_banned_term_match_refuses():
    rules = {"banned_terms": ["forbidden-token"]}
    findings = TM.tier_m_check_moderation("see the forbidden-token here", rules=rules)
    assert len(findings) == 1 and findings[0].severity == "high"
    assert findings[0].type == "moderation_match" and findings[0].tier == "M"
    assert lock_text("see the forbidden-token here", moderation_rules=rules).action == "refuse"


def test_banned_term_is_case_insensitive():
    rules = {"banned_terms": ["SECRETWORD"]}
    assert TM.tier_m_check_moderation("a secretword slipped in", rules=rules)


def test_clean_text_with_rules_is_allowed():
    rules = {"banned_terms": ["nope"]}
    assert TM.tier_m_check_moderation("nothing matches here", rules=rules) == []
    assert lock_text("nothing matches here", moderation_rules=rules).action == "allow"


def test_banned_pattern_match_refuses():
    rules = {"banned_patterns": [r"\bproject[- ]nimbus\b"]}
    assert TM.tier_m_check_moderation("re: project-nimbus rollout", rules=rules)
    assert lock_text("re: project-nimbus rollout", moderation_rules=rules).action == "refuse"


@pytest.mark.parametrize("rules", [
    {"banned_terms": "not-a-list"},
    {"banned_patterns": {"oops": 1}},
])
def test_malformed_rule_shape_fails_closed(rules):
    # A present-but-malformed rule is one we cannot enforce → fail closed, never
    # silently skipped (that would fail open against the policy's intent).
    findings = TM.tier_m_check_moderation("benign", rules=rules)
    assert any(f.type == "tier_m_unavailable" for f in findings)
    assert lock_text("benign", moderation_rules=rules).action == "refuse"


def test_tier_m_composes_with_tier_b_strictest_wins():
    # A clean-of-PII text carrying only a banned term still refuses (Tier M high),
    # and a text with BOTH a Tier-B email and a Tier-M term refuses (not minimise).
    rules = {"banned_terms": ["contraband"]}
    assert lock_text("ship the contraband", moderation_rules=rules).action == "refuse"
    both = lock_text("mail ceo\x40corp.example about the contraband", moderation_rules=rules)
    assert both.action == "refuse"


def test_invalid_regex_fails_closed():
    # A rule we cannot compile must NOT be silently skipped (that would fail open).
    rules = {"banned_patterns": ["("]}
    findings = TM.tier_m_check_moderation("benign", rules=rules)
    assert len(findings) == 1 and findings[0].type == "tier_m_unavailable"
    assert findings[0].severity == "high"
    assert lock_text("benign", moderation_rules=rules).action == "refuse"


# ── semantic categories: REQUIRE a backend, FAIL CLOSED when unavailable (D8) ──

class _Flags:
    def __init__(self, flagged): self._f = flagged
    def is_available(self): return True
    def classify(self, text, categories): return {"flagged": self._f}


class _Unavail:
    def is_available(self): return False
    def classify(self, text, categories): return {"flagged": []}


class _Boom:
    def is_available(self): return True
    def classify(self, text, categories): raise RuntimeError("classifier crashed")


def test_categories_with_no_backend_fail_closed():
    # Default make_moderation_backend raises (no classifier ships) → fail closed.
    rules = {"categories": ["hate"]}
    findings = TM.tier_m_check_moderation("some text", rules=rules)
    assert any(f.type == "tier_m_unavailable" for f in findings)
    assert lock_text("some text", moderation_rules=rules).action == "refuse"


def test_categories_backend_unavailable_fails_closed(monkeypatch):
    monkeypatch.setattr(TM, "make_moderation_backend", lambda spec: _Unavail())
    rules = {"categories": ["hate"], "backend": "classifier:v2"}
    findings = TM.tier_m_check_moderation("text", rules=rules)
    assert [f.type for f in findings] == ["tier_m_unavailable"]


def test_categories_backend_crash_fails_closed(monkeypatch):
    monkeypatch.setattr(TM, "make_moderation_backend", lambda spec: _Boom())
    rules = {"categories": ["hate"], "backend": "classifier:v2"}
    findings = TM.tier_m_check_moderation("text", rules=rules)
    assert [f.type for f in findings] == ["tier_m_unavailable"]


def test_categories_malformed_result_fails_closed(monkeypatch):
    class _Malformed:
        def is_available(self): return True
        def classify(self, text, categories): return {"oops": True}
    monkeypatch.setattr(TM, "make_moderation_backend", lambda spec: _Malformed())
    rules = {"categories": ["hate"], "backend": "classifier:v2"}
    findings = TM.tier_m_check_moderation("text", rules=rules)
    assert [f.type for f in findings] == ["tier_m_unavailable"]


def test_categories_backend_flags_category_refuses(monkeypatch):
    monkeypatch.setattr(TM, "make_moderation_backend", lambda spec: _Flags(["hate"]))
    rules = {"categories": ["hate", "violence"], "backend": "classifier:v2"}
    findings = TM.tier_m_check_moderation("text", rules=rules)
    assert len(findings) == 1 and findings[0].type == "moderation_match"
    assert lock_text("text", moderation_rules=rules).action == "refuse"


def test_categories_backend_clean_allows(monkeypatch):
    monkeypatch.setattr(TM, "make_moderation_backend", lambda spec: _Flags([]))
    rules = {"categories": ["hate"], "backend": "classifier:v2"}
    assert TM.tier_m_check_moderation("text", rules=rules) == []
    assert lock_text("text", moderation_rules=rules).action == "allow"


def test_backend_flagging_unwanted_category_is_ignored(monkeypatch):
    # A backend that flags a category the policy did not ask for must not refuse.
    monkeypatch.setattr(TM, "make_moderation_backend", lambda spec: _Flags(["spam"]))
    rules = {"categories": ["hate"], "backend": "classifier:v2"}
    assert TM.tier_m_check_moderation("text", rules=rules) == []


# ── persistence: the finding never leaks the scanned text ─────────────────────

def test_finding_detail_never_leaks_scanned_text():
    secret = "alice\x40example.com plans for project-nimbus"
    rules = {"banned_terms": ["project-nimbus"]}
    findings = TM.tier_m_check_moderation(f"memo: {secret}", rules=rules)
    assert findings and all(secret not in f.detail and "alice\x40example.com" not in f.detail
                            for f in findings)


def test_classify_exception_detail_carries_no_message(monkeypatch):
    secret = "/secret/model.bin and ceo\x40corp.example"

    class _Leaky:
        def is_available(self): return True
        def classify(self, text, categories): raise RuntimeError(secret)
    monkeypatch.setattr(TM, "make_moderation_backend", lambda spec: _Leaky())
    findings = TM.tier_m_check_moderation("hello", rules={"categories": ["x"], "backend": "c:1"})
    assert findings and secret not in findings[0].detail
    assert "RuntimeError" in findings[0].detail


def test_backend_spec_never_appears_in_finding_detail():
    # The backend spec is policy config that may carry a URL/credential; it must
    # not be persisted into a Finding (default hook raises → unavailable finding).
    spec = "https://mod.example/v1?key=SUPERSECRET"
    findings = TM.tier_m_check_moderation("text", rules={"categories": ["x"], "backend": spec})
    assert findings and all("SUPERSECRET" not in f.detail and spec not in f.detail
                            for f in findings)


# ── policy persistence + snapshot wiring ──────────────────────────────────────

def test_policy_roundtrips_moderation_rules(tmp_path):
    rules = {"banned_terms": ["x"], "categories": ["hate"]}
    p = FolderPolicy(moderation_rules=rules)
    assert p.to_dict()["moderation_rules"] == rules
    assert FolderPolicy.from_dict(p.to_dict()).moderation_rules == rules
    save_policy(tmp_path, p)
    assert load_policy(tmp_path).moderation_rules == rules


def test_legacy_policy_has_no_moderation_key():
    assert "moderation_rules" not in FolderPolicy().to_dict()


def test_non_object_moderation_rules_is_a_policy_error():
    with pytest.raises(InvalidPolicy):
        FolderPolicy.from_dict({"moderation_rules": "not-an-object"})


def test_inprocess_snapshot_carries_rules(tmp_path):
    rules = {"banned_terms": ["x"]}
    save_policy(tmp_path, FolderPolicy(moderation_rules=rules))
    snap = _load_policy_inprocess(tmp_path)
    assert snap.moderation_rules == rules


def test_mcp_snapshot_payload_carries_rules(tmp_path):
    # Server side: the policy_snapshot payload (what the MCP client receives) must
    # include moderation_rules, else the cross-process gate silently no-ops Tier M.
    from workspaces.mcp_impl import policy_snapshot
    save_policy(tmp_path, FolderPolicy(moderation_rules={"banned_terms": ["x"]}))
    assert policy_snapshot(str(tmp_path))["moderation_rules"] == {"banned_terms": ["x"]}


def test_mcp_transport_propagates_rules_to_snapshot(monkeypatch):
    # Client side: _load_policy_via_mcp must lift moderation_rules off the payload.
    from workspaces.lock import l0_mcp_client as MC
    from workspaces.lock import l0_bridge as LB
    rules = {"banned_terms": ["x"]}
    monkeypatch.setattr(MC, "mcp_try_load_policy", lambda fc: MC.MCPClientResult(
        success=True, payload={"lock_is_active": True, "oversight_is_active": True,
                               "moderation_rules": rules}))
    assert LB._load_policy_via_mcp("/x").moderation_rules == rules


# ── end-to-end: the gate consults the snapshot's rules ────────────────────────

def test_gate_for_cloud_enforces_moderation(monkeypatch):
    snap = PolicySnapshot(lock_is_active=True, oversight_is_active=True,
                          moderation_rules={"banned_terms": ["contraband"]})
    monkeypatch.setattr("workspaces.lock.gate.try_load_policy", lambda fc: snap)
    hit = gate_for_cloud("shipment of contraband tonight",
                         oversight=OversightLevel.AUTONOMOUS, folder_context="/x")
    assert hit.action == "refuse"
    clean = gate_for_cloud("shipment of fresh produce tonight",
                           oversight=OversightLevel.AUTONOMOUS, folder_context="/x")
    assert clean.action == "allow"
