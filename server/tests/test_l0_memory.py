# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for WorkspaceMemory — the folder-scoped read/write interface.

Focus: the asymmetric hierarchical rule (sub-folders flow up; parents do NOT
leak down). That's the load-bearing property the rest of the L0 stack depends
on.
"""

from __future__ import annotations


import pytest

from rvnd import (
    WorkspaceMemory,
    MutationLog,
    WebResult,
    discover_descendants,
    discover_folders,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def vault(tmp_path):
    """A synthetic department tree for testing the asymmetric rule."""
    paths = {
        "acme":         tmp_path / "companies" / "acme",
        "hr":           tmp_path / "companies" / "acme" / "HR",
        "onboarding":   tmp_path / "companies" / "acme" / "HR" / "onboarding",
        "compensation": tmp_path / "companies" / "acme" / "HR" / "compensation",
        "eng":          tmp_path / "companies" / "acme" / "Engineering",
        "platform":     tmp_path / "companies" / "acme" / "Engineering" / "platform",
        "legal":        tmp_path / "companies" / "acme" / "Legal",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


@pytest.fixture
def log_root(tmp_path):
    return tmp_path / "logs"


def _sample_pair(pair_id_suffix: str, *, summary: str, facets: dict | None = None,
                 source_document: str | None = None) -> dict:
    """Build a minimal pair dict for tests."""
    pid = f"sha256:problem-{pair_id_suffix}"
    sid = f"sha256:solution-{pair_id_suffix}"
    return {
        "id": sid,
        "problem": {
            "id": pid,
            "scope": "test",
            "type": "test",
            "summary": summary,
            "facets": facets or {},
            "source_document": source_document,
        },
        "solution": {
            "id": sid,
            "problem_id": pid,
            "body": f"solution body for {summary}",
            "body_format": "prose",
            "authority_tier": 3,
            "confidence": 0.9,
            "cited_sources": ["test://ref/1"],
            "extractor_chain": ["rules"],
            "extractor_version": "0.1.0",
        },
    }


# ===========================================================================
# Discovery — finds folders that have logs
# ===========================================================================


def test_discover_folders_empty_root(tmp_path):
    assert discover_folders(log_root=tmp_path / "nonexistent") == {}


def test_discover_folders_finds_logged_folders(vault, log_root):
    WorkspaceMemory(vault["hr"], log_root=log_root).remember(
        _sample_pair("hr1", summary="hr pair"))
    WorkspaceMemory(vault["eng"], log_root=log_root).remember(
        _sample_pair("eng1", summary="eng pair"))

    folders = discover_folders(log_root=log_root)
    assert str(vault["hr"].resolve()) in folders
    assert str(vault["eng"].resolve()) in folders


def test_discover_descendants_filters_by_prefix(vault, log_root):
    # Seed three folders.
    for key in ("hr", "onboarding", "eng"):
        WorkspaceMemory(vault[key], log_root=log_root).remember(
            _sample_pair(key, summary=f"{key} pair"))

    descendants_of_hr = set(discover_descendants(vault["hr"], log_root=log_root))
    assert str(vault["hr"].resolve()) in descendants_of_hr
    assert str(vault["onboarding"].resolve()) in descendants_of_hr
    # Engineering is NOT under HR.
    assert str(vault["eng"].resolve()) not in descendants_of_hr


# ===========================================================================
# THE LOAD-BEARING TESTS — asymmetric hierarchical rule
# ===========================================================================


def test_asymmetric_rule_sub_to_top_visible(vault, log_root):
    """A pair remembered in /acme/HR/ IS visible to WorkspaceMemory(/acme/)."""
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    hr.remember(_sample_pair("hr1", summary="HR confidential policy"))

    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    results = acme.search("HR confidential policy")
    assert len(results) == 1
    assert "HR confidential policy" in results[0]["problem"]["summary"]


def test_asymmetric_rule_grandchild_visible_to_grandparent(vault, log_root):
    """A pair in /acme/HR/onboarding/ IS visible to WorkspaceMemory(/acme/)."""
    onb = WorkspaceMemory(vault["onboarding"], log_root=log_root)
    onb.remember(_sample_pair("onb1", summary="onboarding checklist v3"))

    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    results = acme.search("onboarding checklist")
    assert len(results) >= 1


def test_asymmetric_rule_grandchild_visible_to_parent(vault, log_root):
    """A pair in /acme/HR/onboarding/ IS visible to WorkspaceMemory(/acme/HR/)."""
    onb = WorkspaceMemory(vault["onboarding"], log_root=log_root)
    onb.remember(_sample_pair("onb1", summary="onboarding checklist"))

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    results = hr.search("onboarding")
    assert len(results) >= 1


def test_asymmetric_rule_sibling_NOT_visible(vault, log_root):
    """HR cannot see Engineering. Engineering cannot see HR."""
    WorkspaceMemory(vault["hr"], log_root=log_root).remember(
        _sample_pair("hr-private", summary="HR salary band data"))
    WorkspaceMemory(vault["eng"], log_root=log_root).remember(
        _sample_pair("eng-private", summary="Engineering on-call rota"))

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    eng = WorkspaceMemory(vault["eng"], log_root=log_root)

    # HR cannot see Engineering's data.
    hr_results = hr.search("on-call rota")
    assert all("on-call rota" not in r["problem"]["summary"] for r in hr_results)

    # Engineering cannot see HR's data.
    eng_results = eng.search("salary band")
    assert all("salary band" not in r["problem"]["summary"] for r in eng_results)


def test_asymmetric_rule_parent_NOT_visible_to_child(vault, log_root):
    """A pair in /acme/HR/ is NOT visible to WorkspaceMemory(/acme/HR/onboarding/).

    This is the load-bearing asymmetry — sub-folders cannot read up.
    """
    WorkspaceMemory(vault["hr"], log_root=log_root).remember(
        _sample_pair("hr-general", summary="HR general policy doc"))

    onb = WorkspaceMemory(vault["onboarding"], log_root=log_root)
    results = onb.search("HR general policy")
    assert all("HR general" not in r["problem"]["summary"] for r in results), \
        "ASYMMETRIC RULE VIOLATED: child folder saw its parent's memory"


def test_asymmetric_rule_cousin_NOT_visible(vault, log_root):
    """A pair in /acme/HR/compensation/ is NOT visible to /acme/HR/onboarding/.

    Siblings within the same department still don't see each other.
    """
    WorkspaceMemory(vault["compensation"], log_root=log_root).remember(
        _sample_pair("comp-private", summary="compensation review template"))

    onb = WorkspaceMemory(vault["onboarding"], log_root=log_root)
    results = onb.search("compensation review")
    assert all("compensation" not in r["problem"]["summary"] for r in results)


def test_asymmetric_rule_top_sees_union(vault, log_root):
    """WorkspaceMemory(/acme/) sees the union of every descendant department."""
    WorkspaceMemory(vault["hr"], log_root=log_root).remember(
        _sample_pair("hr1", summary="hr alpha note"))
    WorkspaceMemory(vault["eng"], log_root=log_root).remember(
        _sample_pair("eng1", summary="engineering beta note"))
    WorkspaceMemory(vault["legal"], log_root=log_root).remember(
        _sample_pair("legal1", summary="legal gamma note"))

    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    all_summaries = {p["problem"]["summary"] for p in acme.all_pairs()}
    assert "hr alpha note" in all_summaries
    assert "engineering beta note" in all_summaries
    assert "legal gamma note" in all_summaries


# ===========================================================================
# Write / Read round-trips
# ===========================================================================


def test_remember_and_by_id(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    pair = _sample_pair("hr1", summary="the pair")
    pid = hr.remember(pair)
    assert pid == pair["id"]

    fetched = hr.by_id(pid)
    assert fetched is not None
    assert fetched["problem"]["summary"] == "the pair"


def test_by_id_returns_none_for_unknown(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert hr.by_id("sha256:does-not-exist") is None


def test_search_returns_most_similar(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    hr.remember(_sample_pair("a", summary="onboarding checklist for new engineers"))
    hr.remember(_sample_pair("b", summary="performance review templates"))
    hr.remember(_sample_pair("c", summary="payroll year-end procedures"))

    results = hr.search("onboarding new engineer")
    assert len(results) >= 1
    assert "onboarding" in results[0]["problem"]["summary"]


def test_search_respects_k(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    for i in range(10):
        hr.remember(_sample_pair(f"p{i}", summary=f"onboarding tip number {i}"))
    results = hr.search("onboarding tip", k=3)
    assert len(results) == 3


def test_search_handles_string_query(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    hr.remember(_sample_pair("p1", summary="employee handbook updates"))
    results = hr.search("employee handbook")
    assert len(results) >= 1


# ===========================================================================
# Delete + delete_document
# ===========================================================================


def test_delete_hides_pair_from_reads(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    pair = _sample_pair("doomed", summary="this pair will be deleted")
    pid = hr.remember(pair)

    assert hr.by_id(pid) is not None
    assert hr.delete(pid) is True
    assert hr.by_id(pid) is None


def test_delete_unknown_returns_false(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert hr.delete("sha256:never-existed") is False


def test_delete_document_cascades(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    hr.remember(_sample_pair("p1", summary="pair 1", source_document="/inbox/contract.pdf"))
    hr.remember(_sample_pair("p2", summary="pair 2", source_document="/inbox/contract.pdf"))
    hr.remember(_sample_pair("p3", summary="pair 3", source_document="/inbox/other.pdf"))

    n = hr.delete_document("/inbox/contract.pdf")
    assert n == 2

    remaining = {p["problem"]["summary"] for p in hr.all_pairs()}
    assert remaining == {"pair 3"}


def test_deleted_pair_not_returned_by_search(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    pid = hr.remember(_sample_pair("p1", summary="findable pair"))
    assert len(hr.search("findable pair")) == 1
    hr.delete(pid)
    assert len(hr.search("findable pair")) == 0


def test_delete_in_descendant_visible_to_parent_view(vault, log_root):
    """Deletion in /onboarding/ propagates up: /HR/ also doesn't see the pair."""
    onb = WorkspaceMemory(vault["onboarding"], log_root=log_root)
    pid = onb.remember(_sample_pair("p1", summary="onboarding secret"))

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert len(hr.search("onboarding secret")) == 1

    onb.delete(pid)
    assert len(hr.search("onboarding secret")) == 0


# ===========================================================================
# web_capture + llm_capture
# ===========================================================================


def test_web_capture_creates_one_pair_per_result(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    pids = hr.web_capture(
        "german wage transparency directive",
        [
            WebResult(url="https://example.com/a", title="A", snippet="snippet A"),
            WebResult(url="https://example.com/b", title="B", snippet="snippet B"),
        ],
    )
    assert len(pids) == 2

    all_pairs = hr.all_pairs()
    summaries = {p["problem"]["summary"] for p in all_pairs}
    assert "german wage transparency directive" in summaries

    # URL is captured as cited_source.
    cited = {tuple(p["solution"]["cited_sources"]) for p in all_pairs}
    assert ("https://example.com/a",) in cited
    assert ("https://example.com/b",) in cited


def test_web_capture_accepts_dicts(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    pids = hr.web_capture(
        "test query",
        [{"url": "https://x.example/", "title": "X", "snippet": "snip"}],
    )
    assert len(pids) == 1


def test_llm_capture_stores_provenance(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    pid = hr.llm_capture(
        prompt_context="What does Art. 32 GDPR require for cloud LLM use?",
        response="Article 32 requires technical and organisational measures appropriate to risk.",
        model="claude-opus-4-6",
    )
    fetched = hr.by_id(pid)
    assert fetched is not None
    assert "Art. 32" in fetched["problem"]["summary"]
    assert fetched["problem"]["facets"]["model"] == "claude-opus-4-6"


# ===========================================================================
# Channel + lifecycle audit
# ===========================================================================


def test_remember_records_correct_channel(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    hr.remember(_sample_pair("doc1", summary="a doc"), channel="document")
    hr.web_capture("a query", [WebResult(url="https://x/", snippet="x")])
    hr.llm_capture(prompt_context="prompt", response="resp", model="m")

    # Pull the raw log to check channels.
    log = MutationLog(vault["hr"], log_root=log_root)
    channels = {e.channel for e in log.replay()}
    assert "document" in channels
    assert "websearch" in channels
    assert "llm_answer" in channels


def test_delete_writes_system_channel_event(vault, log_root):
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    pid = hr.remember(_sample_pair("p1", summary="x"))
    hr.delete(pid)
    log = MutationLog(vault["hr"], log_root=log_root)
    delete_events = [e for e in log.replay() if e.event == "delete"]
    assert len(delete_events) == 1
    assert delete_events[0].channel == "system"
    assert delete_events[0].lifecycle_state == "deleted"


# ===========================================================================
# purge_pair (physical erasure)
# ===========================================================================


def test_purge_pair_physically_removes_from_log(vault, log_root, tmp_path, monkeypatch):
    # B1: purge() now requires controller key + GDPR-grounds args.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd import signing
    signing.ensure_controller_keypair()

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    pid = hr.remember(_sample_pair("p1", summary="purged content"))
    n = hr.purge_pair(
        pid,
        legal_basis="art_17_1_a",
        requester_ref="test-001",
        reason="test purge",
    )
    assert n >= 1

    log = MutationLog(vault["hr"], log_root=log_root)
    # The original events are gone; only a 'purge' tombstone remains, and
    # it names the pair through the opaque folder-salted ref, never the
    # raw id.
    matching = [e for e in log.replay() if e.pair_id == pid]
    assert matching == []
    from rvnd.forgotten_subjects import purged_pair_ref
    ref = purged_pair_ref(log.folder_path, pid)
    tombstones = [e for e in log.replay() if e.event == "purge" and e.pair_id == ref]
    assert len(tombstones) == 1


def test_purge_unknown_pair_returns_zero(vault, log_root, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd import signing
    signing.ensure_controller_keypair()
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert hr.purge_pair(
        "sha256:does-not-exist",
        legal_basis="art_17_1_a",
        requester_ref="test-001",
        reason="not-found probe",
    ) == 0
