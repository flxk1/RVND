# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Art. 50 disclosure envelope (C2) — build, verify, swap point."""

import pytest

from rvnd import disclosure as dz
from rvnd.action_gate import ActionRequest, Verdict, gate


# ── the gate: external-publish must name affected parties ────────────────────

def test_external_publish_without_parties_is_no_go():
    d = gate(ActionRequest("a", "post", "L3", footprint=("external-publish",)))
    assert d.verdict is Verdict.NO_GO
    assert "Art. 50" in d.reason


def test_external_publish_with_parties_passes_the_art50_check():
    d = gate(ActionRequest("a", "post", "L3", footprint=("external-publish",),
                           affected_parties=("alice\x40example.com",)))
    # Not NO-GO for the art50 reason; flagged → CONDITIONAL (needs sign-off).
    assert d.verdict is Verdict.CONDITIONAL


def test_blank_party_strings_do_not_count():
    d = gate(ActionRequest("a", "post", "L3", footprint=("external-publish",),
                           affected_parties=("  ", "")))
    assert d.verdict is Verdict.NO_GO


def test_non_publish_footprints_are_unaffected():
    d = gate(ActionRequest("a", "export", "L2", footprint=("personal-data",)))
    assert d.verdict is Verdict.CONDITIONAL   # unchanged behaviour


# ── the envelope ──────────────────────────────────────────────────────────────

def test_make_envelope_requires_a_party():
    with pytest.raises(ValueError):
        dz.make_envelope("hello", affected_parties=[])
    with pytest.raises(ValueError):
        dz.make_envelope("hello", affected_parties=["  "])


def test_envelope_marks_ai_origin_and_does_not_store_content():
    env = dz.make_envelope("the secret body", affected_parties=["bob"])
    d = env.to_dict()
    assert d["marking"]["ai_generated"] is True
    assert d["marking"]["profile"] == dz.MARKING_PROFILE
    # Minimisation: the content is hashed, never copied into the envelope.
    assert "the secret body" not in repr(d)
    assert d["content_hash"].startswith("sha256:")


def test_envelope_verifies_and_detects_content_tamper():
    env = dz.make_envelope("approved text", affected_parties=["bob", "carol"])
    ok = dz.verify_envelope(env, content="approved text")
    assert ok["signature_ok"] and ok["content_ok"] and not ok["reasons"]
    bad = dz.verify_envelope(env, content="swapped text")
    assert bad["signature_ok"] and bad["content_ok"] is False


def test_signature_tamper_is_caught():
    env = dz.make_envelope("x", affected_parties=["bob"]).to_dict()
    env["affected_parties"] = ["mallory"]      # rewrite a signed field
    v = dz.verify_envelope(env, content="x")
    assert v["signature_ok"] is False


def test_verify_never_raises_on_garbage():
    assert dz.verify_envelope({}, content="x")["signature_ok"] is False
    assert dz.verify_envelope({"signature": "zz", "public_key_pem": "nope"})[
        "signature_ok"] is False


# ── the one swap point ────────────────────────────────────────────────────────

def test_stale_marking_profile_is_flagged():
    env = dz.make_envelope("x", affected_parties=["bob"]).to_dict()
    env["marking"]["profile"] = "some-old-profile"
    v = dz.verify_envelope(env, content="x")
    assert v["stale_profile"] is True
    assert any("not the current" in r for r in v["reasons"])


def test_current_profile_is_the_provisional_until_code_lands():
    # Documents the swap contract: when the Art. 50 Code format is adopted,
    # MARKING_PROFILE changes and this test is updated alongside _marker().
    assert dz.MARKING_PROFILE == dz.MARKING_PROFILE_PROVISIONAL
