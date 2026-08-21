# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B8 (0.6.8) — lock Tier B regex extensions + Tier C ensemble.

B8.1: four new Tier B pattern groups commonly missed by the privacy bench.
B8.2: Tier C model ensemble (phi-3.5 + qwen-7B + mistral-7B) with INSUFFICIENT on
      disagreement.
B8.3: CoT-style prompt template applied before classification.

Local LLMs are NEVER invoked in these tests — local_llm_classify is
monkey-patched with predetermined returns.
"""

from __future__ import annotations


from rvnd.lock.core import (
    TierCEnsembleResult,
    tier_b_scan_text,
    tier_c_semantic_check,
)


# ---------------------------------------------------------------------------
# B8.1 — Tier B regex extensions
# ---------------------------------------------------------------------------


def _labels(findings) -> list[str]:
    out = []
    for f in findings:
        # detail is "regex matched pattern: <label>"
        det = f.detail or ""
        if "regex matched pattern: " in det:
            out.append(det.split("regex matched pattern: ", 1)[1].strip())
    return out


def test_tier_b_catches_name_possessive():
    text = "Please forward Mr. Smith's medical history to the new specialist."
    labels = _labels(tier_b_scan_text(text))
    assert "name_possessive" in labels


def test_tier_b_catches_name_possessive_multi_word():
    text = "Dr. Jane Doe's notes were filed yesterday."
    labels = _labels(tier_b_scan_text(text))
    assert "name_possessive" in labels


def test_tier_b_catches_german_steuer_id():
    # 11-digit Steuer-ID (with optional spacing).
    text = "Bitte die Steuer-ID 12 345 678 902 angeben."
    labels = _labels(tier_b_scan_text(text))
    assert "de_steuer_id" in labels


def test_tier_b_catches_medical_record_number():
    text = "Update MRN: AB1234567 in the chart."
    labels = _labels(tier_b_scan_text(text))
    assert "mrn" in labels


def test_tier_b_catches_patient_case_id():
    text = "Please reference patient #45821 in the discharge summary."
    labels = _labels(tier_b_scan_text(text))
    assert "patient_case_id" in labels


def test_tier_b_catches_iban_with_checksum():
    # German IBAN, properly structured: DE + 2 checksum digits + 18 digits.
    text = "Send the refund to DE89370400440532013000."
    labels = _labels(tier_b_scan_text(text))
    assert "iban_full" in labels


def test_tier_b_catches_spanish_dni():
    text = "DNI is 12345678Z for the application."
    labels = _labels(tier_b_scan_text(text))
    assert "es_dni_nie" in labels


def test_tier_b_catches_french_ssn():
    # 1 (sex) + 85 (year) + 12 (month) + 75 (department) + 116 + 001 + 42
    text = "Numéro de sécurité sociale 1 85 12 75 116 001 42"
    labels = _labels(tier_b_scan_text(text))
    assert "fr_ssn" in labels


# ---------------------------------------------------------------------------
# B8.2 + B8.3 — Tier C ensemble
# ---------------------------------------------------------------------------


def _make_classify_stub(per_model_label: dict[str, str], ok: bool = True):
    """Return a function that mimics local_llm_classify, picking its return
    value from per_model_label[model]."""
    def stub(text, categories, folder_context="", model=""):
        # Assert the CoT prompt template was used.
        assert "Apply these rules IN ORDER" in text, \
            "expected CoT prompt prefix in classifier input"
        label = per_model_label.get(model, "insufficient")
        return {"ok": ok, "category": label, "model_used": model,
                "raw_response": label, "latency_ms": 1}
    return stub


def _patch_classify(monkeypatch, fn):
    import rvnd.mcp_server as mcp_server
    monkeypatch.setattr(mcp_server, "local_llm_classify", fn, raising=True)


def test_tier_c_ensemble_agreement_returns_pii_yes(monkeypatch):
    stub = _make_classify_stub({
        "phi-3.5-mini-q4": "pii_yes",
        "qwen-2.5-coder-7b-q4": "pii_yes",
        "mistral-7b-instruct-q4": "pii_yes",
    })
    _patch_classify(monkeypatch, stub)
    r = tier_c_semantic_check("Anna Lee lives at 12 High Street, Bristol.")
    assert isinstance(r, TierCEnsembleResult)
    assert r.label == "pii_yes"
    assert r.confidence >= 0.85


def test_tier_c_ensemble_agreement_returns_pii_no(monkeypatch):
    stub = _make_classify_stub({
        "phi-3.5-mini-q4": "pii_no",
        "qwen-2.5-coder-7b-q4": "pii_no",
        "mistral-7b-instruct-q4": "pii_no",
    })
    _patch_classify(monkeypatch, stub)
    r = tier_c_semantic_check("The weather is fine today.")
    assert r.label == "pii_no"
    assert r.confidence >= 0.85


def test_tier_c_ensemble_disagreement_returns_insufficient(monkeypatch):
    stub = _make_classify_stub({
        "phi-3.5-mini-q4": "pii_yes",
        "qwen-2.5-coder-7b-q4": "pii_no",
        "mistral-7b-instruct-q4": "pii_no",
    })
    _patch_classify(monkeypatch, stub)
    r = tier_c_semantic_check("Anna is a common name.")
    assert r.label == "insufficient"


def test_tier_c_either_insufficient_returns_insufficient(monkeypatch):
    stub = _make_classify_stub({
        "phi-3.5-mini-q4": "pii_yes",
        "qwen-2.5-coder-7b-q4": "insufficient",
        "mistral-7b-instruct-q4": "pii_yes",
    })
    _patch_classify(monkeypatch, stub)
    r = tier_c_semantic_check("Anna says hi.")
    assert r.label == "insufficient"


def test_tier_c_no_local_models_falls_back_gracefully(monkeypatch):
    def stub(*a, **kw):
        raise RuntimeError("backend not configured")
    _patch_classify(monkeypatch, stub)
    r = tier_c_semantic_check("Anna says hi.")
    assert r is None


def test_tier_c_uses_cot_prompt(monkeypatch):
    seen_prompts = []

    def stub(text, categories, folder_context="", model=""):
        seen_prompts.append(text)
        return {"ok": True, "category": "pii_no", "model_used": model}

    _patch_classify(monkeypatch, stub)
    tier_c_semantic_check("hello world")
    assert seen_prompts, "ensemble must call the classifier"
    for prompt in seen_prompts:
        # The CoT template includes three numbered guard rails.
        assert "1. Does the text mention" in prompt
        assert "2. Does it reveal information" in prompt
        assert "3. Could the combination of details" in prompt
        assert "pii_yes / pii_no / insufficient" in prompt
