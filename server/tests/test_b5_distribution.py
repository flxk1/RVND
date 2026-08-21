# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for B5 — top-down distributed memory.

The dual-channel model:
- PRIVATE memory (default) flows UP only (sub-folders → parents).
- DISTRIBUTED memory (publish) flows DOWN to descendants — and only down.

These tests cover the load-bearing properties of the new channel without
violating the asymmetric rule for private memory.
"""

from __future__ import annotations

import io

import pytest

from rvnd import (
    WorkspaceMemory,
    discover_ancestors,
)


@pytest.fixture
def log_root(tmp_path):
    return tmp_path / "logs"


@pytest.fixture
def vault(tmp_path):
    paths = {
        "root":         tmp_path,
        "acme":         tmp_path / "companies" / "acme",
        "hr":           tmp_path / "companies" / "acme" / "HR",
        "onboarding":   tmp_path / "companies" / "acme" / "HR" / "onboarding",
        "compensation": tmp_path / "companies" / "acme" / "HR" / "compensation",
        "eng":          tmp_path / "companies" / "acme" / "Engineering",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def _sample(suffix: str, *, summary: str) -> dict:
    pid = f"sha256:problem-{suffix}"
    sid = f"sha256:solution-{suffix}"
    return {
        "id": sid,
        "problem": {"id": pid, "scope": "test", "type": "test",
                    "summary": summary, "facets": {}},
        "solution": {"id": sid, "problem_id": pid, "body": "x",
                     "body_format": "prose", "authority_tier": 3,
                     "confidence": 0.9},
    }


# ===========================================================================
# discover_ancestors
# ===========================================================================


def test_discover_ancestors_empty_when_no_logs(vault, log_root):
    assert discover_ancestors(vault["hr"], log_root=log_root) == []


def test_discover_ancestors_returns_path_prefix_only(vault, log_root):
    # Seed logs at acme + hr + eng.
    for key in ("acme", "hr", "eng"):
        WorkspaceMemory(vault[key], log_root=log_root).remember(_sample(key, summary=key))

    # From /acme/HR/, the ancestors are /acme/ (and tmp_path's auto-resolved
    # parents, but only those with logs).
    anc = discover_ancestors(vault["hr"], log_root=log_root)
    assert str(vault["acme"].resolve()) in anc
    # Engineering is a sibling, NOT an ancestor.
    assert str(vault["eng"].resolve()) not in anc
    # HR itself is excluded (strict ancestors only).
    assert str(vault["hr"].resolve()) not in anc


def test_discover_ancestors_orders_shallowest_first(vault, log_root):
    # Seed at acme + hr + onboarding so the chain is acme → hr → onboarding.
    WorkspaceMemory(vault["acme"], log_root=log_root).remember(_sample("a", summary="a"))
    WorkspaceMemory(vault["hr"], log_root=log_root).remember(_sample("b", summary="b"))
    WorkspaceMemory(vault["onboarding"], log_root=log_root).remember(_sample("c", summary="c"))

    anc = discover_ancestors(vault["onboarding"], log_root=log_root)
    # /acme/ should come before /acme/HR/ (shallowest first).
    acme_idx = anc.index(str(vault["acme"].resolve()))
    hr_idx = anc.index(str(vault["hr"].resolve()))
    assert acme_idx < hr_idx


# ===========================================================================
# LOAD-BEARING — publish flows DOWN
# ===========================================================================


def test_published_pair_visible_to_descendants(vault, log_root):
    """A pair published from /acme/ IS visible to /acme/HR/."""
    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    acme.publish(_sample("p1", summary="company-wide policy"))

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    summaries = {p["problem"]["summary"] for p in hr.all_pairs()}
    assert "company-wide policy" in summaries


def test_published_pair_visible_two_levels_down(vault, log_root):
    """Publish at /acme/ reaches /acme/HR/onboarding/."""
    WorkspaceMemory(vault["acme"], log_root=log_root).publish(
        _sample("p1", summary="org handbook"))

    onb = WorkspaceMemory(vault["onboarding"], log_root=log_root)
    summaries = {p["problem"]["summary"] for p in onb.all_pairs()}
    assert "org handbook" in summaries


def test_published_pair_visible_via_search(vault, log_root):
    WorkspaceMemory(vault["acme"], log_root=log_root).publish(
        _sample("p1", summary="travel expense policy"))

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    results = hr.search("travel expense")
    assert len(results) >= 1
    assert "travel expense" in results[0]["problem"]["summary"]


def test_published_pair_visible_via_by_id(vault, log_root):
    pid = WorkspaceMemory(vault["acme"], log_root=log_root).publish(
        _sample("p1", summary="x"))

    onb = WorkspaceMemory(vault["onboarding"], log_root=log_root)
    fetched = onb.by_id(pid)
    assert fetched is not None
    assert fetched["problem"]["summary"] == "x"


# ===========================================================================
# LOAD-BEARING — private memory still flows UP only
# ===========================================================================


def test_private_pair_still_NOT_visible_to_descendants(vault, log_root):
    """remember() is unchanged — descendants don't see private parent memory."""
    WorkspaceMemory(vault["acme"], log_root=log_root).remember(
        _sample("p1", summary="ceo private notes"))

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    summaries = {p["problem"]["summary"] for p in hr.all_pairs()}
    assert "ceo private notes" not in summaries


def test_remember_then_publish_makes_it_visible(vault, log_root):
    """remember (private) does NOT propagate; publish DOES."""
    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    acme.remember(_sample("private", summary="ceo private"))
    acme.publish(_sample("public", summary="company policy"))

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    summaries = {p["problem"]["summary"] for p in hr.all_pairs()}
    assert "company policy" in summaries
    assert "ceo private" not in summaries


def test_published_pair_NOT_visible_to_siblings(vault, log_root):
    """Publishing at /acme/HR/ reaches HR's descendants — NOT Engineering."""
    WorkspaceMemory(vault["hr"], log_root=log_root).publish(
        _sample("p1", summary="hr-specific guideline"))

    eng = WorkspaceMemory(vault["eng"], log_root=log_root)
    summaries = {p["problem"]["summary"] for p in eng.all_pairs()}
    assert "hr-specific guideline" not in summaries


def test_published_pair_NOT_visible_to_publishers_cousin(vault, log_root):
    """/acme/HR/onboarding/ publishing does NOT reach /acme/HR/compensation/."""
    WorkspaceMemory(vault["onboarding"], log_root=log_root).publish(
        _sample("p1", summary="onboarding-only memo"))

    comp = WorkspaceMemory(vault["compensation"], log_root=log_root)
    summaries = {p["problem"]["summary"] for p in comp.all_pairs()}
    assert "onboarding-only memo" not in summaries


# ===========================================================================
# LOAD-BEARING — unpublish revokes from descendants
# ===========================================================================


def test_unpublish_revokes_from_descendants(vault, log_root):
    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    pid = acme.publish(_sample("p1", summary="will be revoked"))

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert "will be revoked" in {p["problem"]["summary"] for p in hr.all_pairs()}

    assert acme.unpublish(pid) is True
    assert "will be revoked" not in {p["problem"]["summary"] for p in hr.all_pairs()}


def test_unpublish_only_works_on_published_pairs(vault, log_root):
    """Trying to unpublish a private pair returns False."""
    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    pid = acme.remember(_sample("private", summary="never published"))
    assert acme.unpublish(pid) is False


def test_unpublish_audit_trail_survives(vault, log_root):
    """After unpublish, the original publish event is still in the log."""
    from rvnd import MutationLog

    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    pid = acme.publish(_sample("p1", summary="x"))
    acme.unpublish(pid)

    log = MutationLog(vault["acme"], log_root=log_root)
    events_for_pair = [e for e in log.replay() if e.pair_id == pid]
    # Original publish + delete event = 2 events.
    assert len(events_for_pair) == 2
    states = [e.lifecycle_state for e in events_for_pair]
    assert "live" in states
    assert "deleted" in states


def test_published_then_deleted_invisible(vault, log_root):
    """Same as unpublish but using delete() directly."""
    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    pid = acme.publish(_sample("p1", summary="published then deleted"))
    acme.delete(pid)

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    summaries = {p["problem"]["summary"] for p in hr.all_pairs()}
    assert "published then deleted" not in summaries


# ===========================================================================
# Most-recent-state semantics across publish toggle
# ===========================================================================


def test_publish_then_remember_flips_to_private(vault, log_root):
    """If a pair is published then re-remembered (private), it's hidden again.

    The most-recent distribution_scope wins.
    """
    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    sample = _sample("p1", summary="toggle me")
    acme.publish(sample)
    # Descendants see it.
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert "toggle me" in {p["problem"]["summary"] for p in hr.all_pairs()}

    # Now re-remember the same pair as private.
    acme.remember(sample)
    # Descendants should no longer see it.
    assert "toggle me" not in {p["problem"]["summary"] for p in hr.all_pairs()}


def test_remember_then_publish_makes_visible(vault, log_root):
    """Inverse: private first, then publish → descendants now see it."""
    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    sample = _sample("p1", summary="upgrade to public")
    acme.remember(sample)

    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert "upgrade to public" not in {p["problem"]["summary"] for p in hr.all_pairs()}

    acme.publish(sample)
    assert "upgrade to public" in {p["problem"]["summary"] for p in hr.all_pairs()}


# ===========================================================================
# Asymmetric rule preserved for private — publish doesn't change anything else
# ===========================================================================


def test_publish_at_sub_folder_does_not_leak_to_siblings(vault, log_root):
    """Publishing at /acme/HR/ reaches HR/onboarding/ and HR/compensation/
    but NOT /acme/Engineering/."""
    WorkspaceMemory(vault["hr"], log_root=log_root).publish(
        _sample("p1", summary="hr-distributed"))

    onb_sees = "hr-distributed" in {p["problem"]["summary"]
                                    for p in WorkspaceMemory(vault["onboarding"], log_root=log_root).all_pairs()}
    comp_sees = "hr-distributed" in {p["problem"]["summary"]
                                     for p in WorkspaceMemory(vault["compensation"], log_root=log_root).all_pairs()}
    eng_sees = "hr-distributed" in {p["problem"]["summary"]
                                    for p in WorkspaceMemory(vault["eng"], log_root=log_root).all_pairs()}

    assert onb_sees
    assert comp_sees
    assert not eng_sees


def test_two_published_pairs_visible_in_descendant(vault, log_root):
    """If a folder has two ancestors that both publish, the descendant sees both."""
    WorkspaceMemory(vault["acme"], log_root=log_root).publish(_sample("a", summary="acme policy"))
    WorkspaceMemory(vault["hr"], log_root=log_root).publish(_sample("b", summary="hr policy"))

    onb = WorkspaceMemory(vault["onboarding"], log_root=log_root)
    summaries = {p["problem"]["summary"] for p in onb.all_pairs()}
    assert "acme policy" in summaries
    assert "hr policy" in summaries


# ===========================================================================
# CLI
# ===========================================================================


def test_cli_publish_command(vault, log_root, capsys, monkeypatch):
    from rvnd.cli import main

    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    pid = acme.remember(_sample("p1", summary="to be published"))

    rc = main(["--log-root", str(log_root), "publish",
               "--folder", str(vault["acme"]),
               pid, "--yes"])
    assert rc == 0
    assert "published" in capsys.readouterr().out

    # Descendant now sees it.
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert "to be published" in {p["problem"]["summary"] for p in hr.all_pairs()}


def test_cli_publish_unknown_pair(vault, log_root, capsys):
    from rvnd.cli import main
    rc = main(["--log-root", str(log_root), "publish",
               "--folder", str(vault["acme"]),
               "sha256:nope", "--yes"])
    assert rc == 1


def test_cli_publish_user_aborts(vault, log_root, capsys, monkeypatch):
    from rvnd.cli import main
    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    pid = acme.publish(_sample("p1", summary="x"))
    capsys.readouterr()  # drain

    # Re-publish the original. Don't say yes.
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    rc = main(["--log-root", str(log_root), "publish",
               "--folder", str(vault["acme"]),
               pid])
    assert rc == 2


def test_cli_unpublish_command(vault, log_root, capsys):
    from rvnd.cli import main

    acme = WorkspaceMemory(vault["acme"], log_root=log_root)
    pid = acme.publish(_sample("p1", summary="will revoke"))

    # Descendant sees it.
    hr = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert "will revoke" in {p["problem"]["summary"] for p in hr.all_pairs()}

    rc = main(["--log-root", str(log_root), "unpublish",
               "--folder", str(vault["acme"]),
               pid, "--yes"])
    assert rc == 0
    assert "unpublished" in capsys.readouterr().out

    # Descendant no longer sees it.
    hr2 = WorkspaceMemory(vault["hr"], log_root=log_root)
    assert "will revoke" not in {p["problem"]["summary"] for p in hr2.all_pairs()}


def test_cli_unpublish_unknown(vault, log_root, capsys):
    from rvnd.cli import main
    rc = main(["--log-root", str(log_root), "unpublish",
               "--folder", str(vault["acme"]),
               "sha256:nope", "--yes"])
    assert rc == 1
