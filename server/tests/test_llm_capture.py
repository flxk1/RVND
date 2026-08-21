# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for B4 — LLM exchange capture in two modes."""

from __future__ import annotations

import pytest

from rvnd import (
    IngestMode,
    WorkspaceMemory,
    LLMExchange,
    OversightLevel,
    VerbosityLevel,
    capture_llm_exchange,
    decide_verbosity,
    disable_oversight,
)


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "vault"
    f.mkdir()
    return f


@pytest.fixture
def log_root(tmp_path):
    return tmp_path / "logs"


def _exchange(*, model: str = "claude-opus-4-6",
              prompt: str = "What does GDPR Art. 32 require?",
              response: str = "Art. 32 requires TOMs appropriate to risk.",
              cited: list[str] | None = None,
              cost: float | None = None,
              trace: list[dict] | None = None) -> LLMExchange:
    return LLMExchange(
        model=model,
        prompt_context=prompt,
        response=response,
        cited_sources=cited or [],
        cost_estimate_cents=cost,
        tool_call_trace=trace or [],
    )


# ===========================================================================
# decide_verbosity matrix — pure function tests
# ===========================================================================


def test_agentic_matrix_metadata_at_autonomous():
    v, prompts = decide_verbosity(IngestMode.AGENTIC, OversightLevel.AUTONOMOUS)
    assert v == VerbosityLevel.METADATA
    assert prompts is False


def test_agentic_matrix_preview_at_notify():
    v, _ = decide_verbosity(IngestMode.AGENTIC, OversightLevel.NOTIFY)
    assert v == VerbosityLevel.PREVIEW


def test_agentic_matrix_preview_plus_citations_at_review():
    v, _ = decide_verbosity(IngestMode.AGENTIC, OversightLevel.REVIEW)
    assert v == VerbosityLevel.PREVIEW_PLUS_CITATIONS


def test_agentic_matrix_full_at_approve():
    v, _ = decide_verbosity(IngestMode.AGENTIC, OversightLevel.APPROVE)
    assert v == VerbosityLevel.FULL


def test_agentic_matrix_full_plus_trace_at_supervised():
    v, _ = decide_verbosity(IngestMode.AGENTIC, OversightLevel.SUPERVISED)
    assert v == VerbosityLevel.FULL_PLUS_TRACE


def test_agentic_matrix_full_plus_trace_at_manual():
    v, _ = decide_verbosity(IngestMode.AGENTIC, OversightLevel.MANUAL)
    assert v == VerbosityLevel.FULL_PLUS_TRACE


def test_interactive_matrix_none_at_autonomous():
    v, prompts = decide_verbosity(IngestMode.INTERACTIVE, OversightLevel.AUTONOMOUS)
    assert v == VerbosityLevel.NONE
    assert prompts is False


def test_interactive_matrix_none_at_notify():
    v, prompts = decide_verbosity(IngestMode.INTERACTIVE, OversightLevel.NOTIFY)
    assert v == VerbosityLevel.NONE
    assert prompts is False


def test_interactive_matrix_prompts_at_review():
    v, prompts = decide_verbosity(IngestMode.INTERACTIVE, OversightLevel.REVIEW)
    assert v == VerbosityLevel.FULL
    assert prompts is True


def test_interactive_matrix_prompts_at_approve():
    v, prompts = decide_verbosity(IngestMode.INTERACTIVE, OversightLevel.APPROVE)
    assert v == VerbosityLevel.FULL
    assert prompts is True


# ===========================================================================
# Oversight-disabled folder
# ===========================================================================


def test_oversight_disabled_agentic_captures_max_verbosity():
    v, prompts = decide_verbosity(
        IngestMode.AGENTIC, OversightLevel.AUTONOMOUS, oversight_active=False,
    )
    assert v == VerbosityLevel.FULL_PLUS_TRACE
    assert prompts is False


def test_oversight_disabled_interactive_silently_skips():
    v, prompts = decide_verbosity(
        IngestMode.INTERACTIVE, OversightLevel.AUTONOMOUS, oversight_active=False,
    )
    assert v == VerbosityLevel.NONE
    assert prompts is False


# ===========================================================================
# capture_llm_exchange — end-to-end
# ===========================================================================


def test_agentic_at_autonomous_writes_metadata_only(folder, log_root):
    """No prompt; pair written with body=METADATA."""
    result = capture_llm_exchange(
        _exchange(),
        mode=IngestMode.AGENTIC,
        oversight="autonomous",
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is True
    assert result.verbosity == VerbosityLevel.METADATA
    assert result.prompted_user is False

    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(result.pair_id)
    assert pair is not None
    # Body empty at METADATA verbosity.
    assert pair["solution"]["body"] == ""
    # Hashes still present.
    assert pair["problem"]["facets"]["model"] == "claude-opus-4-6"
    assert pair["problem"]["facets"]["prompt_context_hash"]
    assert pair["problem"]["facets"]["response_hash"]


def test_agentic_at_approve_writes_full(folder, log_root):
    result = capture_llm_exchange(
        _exchange(response="Article 32 requires TOMs."),
        mode=IngestMode.AGENTIC,
        oversight="approve",
        folder_context=folder,
        log_root=log_root,
    )
    assert result.verbosity == VerbosityLevel.FULL

    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(result.pair_id)
    assert pair["solution"]["body"] == "Article 32 requires TOMs."
    assert pair["problem"]["facets"]["prompt_context"] == "What does GDPR Art. 32 require?"


def test_agentic_at_supervised_includes_tool_trace(folder, log_root):
    trace = [{"step": 1, "tool": "search", "args": {"q": "GDPR"}}]
    result = capture_llm_exchange(
        _exchange(trace=trace),
        mode=IngestMode.AGENTIC,
        oversight="supervised",
        folder_context=folder,
        log_root=log_root,
    )
    assert result.verbosity == VerbosityLevel.FULL_PLUS_TRACE
    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(result.pair_id)
    assert pair["solution"]["tool_call_trace"] == trace


def test_agentic_at_review_includes_citations(folder, log_root):
    cited = ["https://eur-lex.europa.eu/...", "doi:10.1007/..."]
    result = capture_llm_exchange(
        _exchange(cited=cited),
        mode=IngestMode.AGENTIC,
        oversight="review",
        folder_context=folder,
        log_root=log_root,
    )
    assert result.verbosity == VerbosityLevel.PREVIEW_PLUS_CITATIONS
    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(result.pair_id)
    assert pair["solution"]["cited_sources"] == cited


def test_interactive_at_autonomous_skips(folder, log_root):
    """Interactive + AUTONOMOUS = silent, not captured."""
    result = capture_llm_exchange(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight="autonomous",
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is False
    assert result.verbosity == VerbosityLevel.NONE
    assert result.skipped_reason == "low_oversight_interactive"

    mem = WorkspaceMemory(folder, log_root=log_root)
    assert mem.all_pairs() == []


def test_interactive_at_notify_skips(folder, log_root):
    result = capture_llm_exchange(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight="notify",
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is False


def test_interactive_at_approve_with_no_callback_captures(folder, log_root):
    """No callback → assume user said yes (CLI-friendly default)."""
    result = capture_llm_exchange(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight="approve",
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is True
    assert result.prompted_user is True


def test_interactive_at_approve_with_callback_no(folder, log_root):
    """Callback returns False → not captured."""
    def says_no(*args, **kwargs):
        return False

    result = capture_llm_exchange(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight="approve",
        folder_context=folder,
        log_root=log_root,
        user_decision_callback=says_no,
    )
    assert result.captured is False
    assert result.prompted_user is True
    assert result.skipped_reason == "user_declined"


def test_interactive_at_approve_with_callback_yes(folder, log_root):
    def says_yes(*args, **kwargs):
        return True

    result = capture_llm_exchange(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight="approve",
        folder_context=folder,
        log_root=log_root,
        user_decision_callback=says_yes,
    )
    assert result.captured is True
    assert result.verbosity == VerbosityLevel.FULL


# ===========================================================================
# Policy interaction
# ===========================================================================


def test_oversight_disabled_agentic_captures_max(folder, log_root):
    """User disabled oversight in this folder → agentic still captures (audit floor),
    at MAX verbosity since we can't ask mid-flow."""
    disable_oversight(folder, accepted_by="alex", log_root=log_root)

    result = capture_llm_exchange(
        _exchange(),
        mode=IngestMode.AGENTIC,
        oversight="autonomous",   # would normally be METADATA-only
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is True
    assert result.verbosity == VerbosityLevel.FULL_PLUS_TRACE
    assert result.oversight_bypassed is True


def test_oversight_disabled_interactive_skips(folder, log_root):
    """User disabled oversight + interactive = no capture (treat as silence)."""
    disable_oversight(folder, accepted_by="alex", log_root=log_root)

    result = capture_llm_exchange(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight="approve",
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is False
    assert result.oversight_bypassed is True


# ===========================================================================
# Stable ids
# ===========================================================================


def test_same_exchange_same_pair_id(folder, log_root):
    """Same model + prompt + response → same pair_id across calls (idempotent in spirit)."""
    e1 = LLMExchange(model="m", prompt_context="q1", response="a1")
    e2 = LLMExchange(model="m", prompt_context="q1", response="a1")
    r1 = capture_llm_exchange(e1, mode=IngestMode.AGENTIC, oversight="approve",
                              folder_context=folder, log_root=log_root)
    r2 = capture_llm_exchange(e2, mode=IngestMode.AGENTIC, oversight="approve",
                              folder_context=folder, log_root=log_root)
    assert r1.pair_id == r2.pair_id


def test_different_response_different_pair_id(folder, log_root):
    r1 = capture_llm_exchange(LLMExchange(model="m", prompt_context="q", response="a"),
                              mode=IngestMode.AGENTIC, oversight="approve",
                              folder_context=folder, log_root=log_root)
    r2 = capture_llm_exchange(LLMExchange(model="m", prompt_context="q", response="b"),
                              mode=IngestMode.AGENTIC, oversight="approve",
                              folder_context=folder, log_root=log_root)
    assert r1.pair_id != r2.pair_id


# ===========================================================================
# Folder scoping preserved
# ===========================================================================


def test_capture_respects_folder_isolation(tmp_path, log_root):
    hr = tmp_path / "HR"
    eng = tmp_path / "Engineering"
    hr.mkdir()
    eng.mkdir()

    capture_llm_exchange(
        _exchange(response="hr-specific answer"),
        mode=IngestMode.AGENTIC, oversight="approve",
        folder_context=hr, log_root=log_root,
    )
    capture_llm_exchange(
        _exchange(response="eng-specific answer"),
        mode=IngestMode.AGENTIC, oversight="approve",
        folder_context=eng, log_root=log_root,
    )

    hr_responses = {p["solution"]["body"]
                    for p in WorkspaceMemory(hr, log_root=log_root).all_pairs()}
    eng_responses = {p["solution"]["body"]
                     for p in WorkspaceMemory(eng, log_root=log_root).all_pairs()}

    assert "hr-specific answer" in hr_responses
    assert "eng-specific answer" in eng_responses
    # Cross-folder leakage check.
    assert "hr-specific answer" not in eng_responses
    assert "eng-specific answer" not in hr_responses


# ===========================================================================
# Oversight value coercion
# ===========================================================================


def test_oversight_accepts_string(folder, log_root):
    r = capture_llm_exchange(_exchange(), mode=IngestMode.AGENTIC,
                             oversight="approve",
                             folder_context=folder, log_root=log_root)
    assert r.captured


def test_oversight_accepts_int(folder, log_root):
    r = capture_llm_exchange(_exchange(), mode=IngestMode.AGENTIC,
                             oversight=4,    # APPROVE
                             folder_context=folder, log_root=log_root)
    assert r.captured


def test_oversight_accepts_enum(folder, log_root):
    r = capture_llm_exchange(_exchange(), mode=IngestMode.AGENTIC,
                             oversight=OversightLevel.APPROVE,
                             folder_context=folder, log_root=log_root)
    assert r.captured


def test_oversight_rejects_garbage(folder, log_root):
    with pytest.raises(ValueError):
        capture_llm_exchange(_exchange(), mode=IngestMode.AGENTIC,
                             oversight="not-a-real-level",
                             folder_context=folder, log_root=log_root)


# ===========================================================================
# Cost + request_id are captured in facets
# ===========================================================================


def test_cost_estimate_captured(folder, log_root):
    r = capture_llm_exchange(_exchange(cost=12.5),
                             mode=IngestMode.AGENTIC, oversight="autonomous",
                             folder_context=folder, log_root=log_root)
    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(r.pair_id)
    assert pair["problem"]["facets"]["cost_estimate_cents"] == 12.5


def test_request_id_captured(folder, log_root):
    e = _exchange()
    e.request_id = "req-abc-123"
    r = capture_llm_exchange(e, mode=IngestMode.AGENTIC, oversight="autonomous",
                             folder_context=folder, log_root=log_root)
    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(r.pair_id)
    assert pair["problem"]["facets"]["request_id"] == "req-abc-123"
