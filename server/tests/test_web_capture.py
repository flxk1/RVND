# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for web-capture (B3)."""

from __future__ import annotations

import pytest

from rvnd import (
    IngestMode,
    WorkspaceMemory,
    OversightLevel,
    VerbosityLevel,
    WebSearchExchange,
    WebSearchResult,
    capture_web_search,
    disable_oversight_for_deployment,
    decide_verbosity,
)


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "vault"
    f.mkdir()
    return f


@pytest.fixture
def log_root(tmp_path):
    return tmp_path / "logs"


def _exchange(query: str = "test query", engine: str = "ddg",
              results: int = 3) -> WebSearchExchange:
    return WebSearchExchange(
        query=query,
        engine=engine,
        results=[
            WebSearchResult(
                url=f"https://example.com/{i}",
                title=f"Title {i}",
                snippet=f"Snippet for result {i}",
                full_text=f"Full body of result {i}",
                rank=i + 1,
            )
            for i in range(results)
        ],
        cost_estimate_cents=0.5,
        request_id="req-test-1",
    )


# ===========================================================================
# Verbosity matrix — agentic mode
# ===========================================================================


def test_agentic_autonomous_captures_metadata_only(folder, log_root):
    result = capture_web_search(
        _exchange(),
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.AUTONOMOUS,
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is True
    assert result.verbosity == VerbosityLevel.METADATA

    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(result.pair_id)
    assert pair is not None
    # METADATA: no query string, no URLs in cited_sources.
    assert "query" not in pair["problem"]["facets"]
    assert pair["solution"]["cited_sources"] == []
    assert pair["solution"]["body"] == ""


def test_agentic_notify_captures_query_and_urls(folder, log_root):
    result = capture_web_search(
        _exchange(),
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.NOTIFY,
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is True
    assert result.verbosity == VerbosityLevel.PREVIEW

    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(result.pair_id)
    assert pair["problem"]["facets"]["query"] == "test query"
    # PREVIEW: URLs in cited_sources, but no snippets in body.
    assert len(pair["solution"]["cited_sources"]) == 3
    assert "Snippet for result 0" not in pair["solution"]["body"]


def test_agentic_review_captures_snippets(folder, log_root):
    result = capture_web_search(
        _exchange(),
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.REVIEW,
        folder_context=folder,
        log_root=log_root,
    )
    assert result.verbosity == VerbosityLevel.PREVIEW_PLUS_CITATIONS
    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(result.pair_id)
    assert "Snippet for result" in pair["solution"]["body"]
    # FULL bodies NOT yet included at PREVIEW_PLUS_CITATIONS.
    assert "Full body of result 0" not in pair["solution"]["body"]


def test_agentic_approve_captures_full(folder, log_root):
    result = capture_web_search(
        _exchange(),
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.APPROVE,
        folder_context=folder,
        log_root=log_root,
    )
    assert result.verbosity == VerbosityLevel.FULL
    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(result.pair_id)
    assert "Full body of result 0" in pair["solution"]["body"]


def test_agentic_supervised_includes_trace(folder, log_root):
    exchange = _exchange()
    exchange.trace = [
        {"step": "fetch", "attempt": 1, "status": 200},
        {"step": "dedupe", "removed": 0},
    ]
    result = capture_web_search(
        exchange,
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.SUPERVISED,
        folder_context=folder,
        log_root=log_root,
    )
    assert result.verbosity == VerbosityLevel.FULL_PLUS_TRACE
    mem = WorkspaceMemory(folder, log_root=log_root)
    pair = mem.by_id(result.pair_id)
    assert pair["solution"].get("trace") == exchange.trace


# ===========================================================================
# Verbosity matrix — interactive mode
# ===========================================================================


def test_interactive_autonomous_does_not_capture(folder, log_root):
    """At low oversight in interactive mode, captures are silent → no capture."""
    result = capture_web_search(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight=OversightLevel.AUTONOMOUS,
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is False
    assert result.verbosity == VerbosityLevel.NONE
    assert result.prompted_user is False


def test_interactive_notify_does_not_capture(folder, log_root):
    result = capture_web_search(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight=OversightLevel.NOTIFY,
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is False


def test_interactive_approve_prompts_and_captures_if_yes(folder, log_root):
    captured_args: dict = {}

    def callback(exchange, level, verbosity):
        captured_args["query"] = exchange.query
        captured_args["level"] = level
        captured_args["verbosity"] = verbosity
        return True

    result = capture_web_search(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight=OversightLevel.APPROVE,
        folder_context=folder,
        log_root=log_root,
        user_decision_callback=callback,
    )
    assert result.prompted_user is True
    assert result.captured is True
    assert captured_args["query"] == "test query"


def test_interactive_approve_user_declines(folder, log_root):
    result = capture_web_search(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight=OversightLevel.APPROVE,
        folder_context=folder,
        log_root=log_root,
        user_decision_callback=lambda *_: False,
    )
    assert result.prompted_user is True
    assert result.captured is False
    assert result.skipped_reason == "user_declined"


# ===========================================================================
# Policy interaction — oversight disabled
# ===========================================================================


def test_agentic_with_oversight_disabled_max_verbosity(folder, log_root):
    disable_oversight_for_deployment(accepted_by="alex", log_root=log_root)
    result = capture_web_search(
        _exchange(),
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.AUTONOMOUS,   # would be METADATA, but oversight off → FULL_PLUS_TRACE
        folder_context=folder,
        log_root=log_root,
    )
    assert result.captured is True
    assert result.verbosity == VerbosityLevel.FULL_PLUS_TRACE
    assert result.oversight_bypassed is True


def test_interactive_with_oversight_disabled_skips(folder, log_root):
    disable_oversight_for_deployment(accepted_by="alex", log_root=log_root)
    result = capture_web_search(
        _exchange(),
        mode=IngestMode.INTERACTIVE,
        oversight=OversightLevel.APPROVE,
        folder_context=folder,
        log_root=log_root,
        user_decision_callback=lambda *_: True,
    )
    # Interactive + oversight off → user wanted silence → no capture.
    assert result.captured is False
    assert result.oversight_bypassed is True


# ===========================================================================
# Determinism + folder scope
# ===========================================================================


def test_same_query_same_pair_id(folder, log_root):
    """Two captures of the same query+engine produce the same pair_id."""
    r1 = capture_web_search(
        _exchange("identical query"),
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.NOTIFY,
        folder_context=folder,
        log_root=log_root,
    )
    r2 = capture_web_search(
        _exchange("identical query"),
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.NOTIFY,
        folder_context=folder,
        log_root=log_root,
    )
    assert r1.pair_id == r2.pair_id


def test_capture_respects_folder_scope(tmp_path, log_root):
    """A capture in /HR/ is visible only to HR + ancestors, not to siblings."""
    hr = tmp_path / "HR"
    eng = tmp_path / "Engineering"
    hr.mkdir()
    eng.mkdir()

    capture_web_search(
        _exchange("hr-private search"),
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.NOTIFY,
        folder_context=hr,
        log_root=log_root,
    )

    eng_mem = WorkspaceMemory(eng, log_root=log_root)
    eng_queries = {p["problem"]["facets"].get("query")
                   for p in eng_mem.all_pairs()
                   if "facets" in p.get("problem", {})}
    assert "hr-private search" not in eng_queries


# ===========================================================================
# decide_verbosity is shared
# ===========================================================================


def test_decide_verbosity_shared_with_llm_capture():
    """The same decide_verbosity() function backs both web and LLM capture."""
    # Agentic at AUTONOMOUS → METADATA, no prompt.
    v, prompt = decide_verbosity(IngestMode.AGENTIC, OversightLevel.AUTONOMOUS)
    assert v == VerbosityLevel.METADATA
    assert prompt is False

    # Interactive at APPROVE → FULL, prompt.
    v, prompt = decide_verbosity(IngestMode.INTERACTIVE, OversightLevel.APPROVE)
    assert v == VerbosityLevel.FULL
    assert prompt is True


# ===========================================================================
# Audit trail
# ===========================================================================


def test_capture_writes_websearch_channel_event(folder, log_root):
    from rvnd import MutationLog
    capture_web_search(
        _exchange(),
        mode=IngestMode.AGENTIC,
        oversight=OversightLevel.APPROVE,
        folder_context=folder,
        log_root=log_root,
    )
    log = MutationLog(folder, log_root=log_root)
    websearch_events = [e for e in log.replay() if e.channel == "websearch"]
    assert len(websearch_events) >= 1
