# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""B9 (0.6.8) — oversight editor: revisions, un-redact, lock, approve."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rvnd.mirror_editor import (
    ControllerSignatureRequired,
    LockHeldError,
    approve_revision,
    discard_revision,
    edit_span,
    open_revision,
    revisions_diff,
    revisions_list,
    un_redact,
)
from rvnd.mutation_log import MutationLog


def _make_lock_mirror(tmp_path: Path) -> tuple[Path, Path]:
    """Create a folder + a lock mirror with one span to play with.

    Returns (folder_path, lock_mirror_path).
    """
    folder = tmp_path / "workspace"
    (folder / "mirrors" / "lock").mkdir(parents=True)
    mirror = folder / "mirrors" / "lock" / "doc.cleaned.md"
    mirror.write_text(
        "Hello [REDACTED:email] please confirm.\n",
        encoding="utf-8",
    )
    spans = folder / "mirrors" / "lock" / "doc.spans.json"
    spans.write_text(json.dumps({
        "schema":      "workspace.mirror.spans/v1",
        "source_path": str(folder / "doc.md"),
        "source_hash": "sha256:abc",
        "mirror_kind": "lock",
        "created_at":  0,
        "spans": [{
            "start":         6,
            "end":           17,
            "kind":          "tier_b.pii_in_argument",
            "original_hash": "sha256:zzz",
            "replacement":   "[REDACTED:email]",
            "span_id":       "span:e1",
        }],
    }), encoding="utf-8")
    return folder, mirror


def _log_root(tmp_path: Path) -> Path:
    return tmp_path / "_log_root"


# ---------------------------------------------------------------------------
# open / edit / approve / discard
# ---------------------------------------------------------------------------


def test_open_revision_creates_draft_and_writes_event(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    rd = open_revision(folder, mirror, actor="alice", log_root=log_root)
    assert Path(rd.draft_path).exists()
    assert Path(rd.spans_path).exists()
    assert rd.lock_holder == "alice"

    # Audit event recorded.
    events = list(MutationLog(folder, log_root=log_root).replay())
    kinds = [e.extra.get("kind") for e in events]
    assert "mirror_edit_opened" in kinds


def test_edit_span_change_replacement_writes_mirror_edit_event(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    open_revision(folder, mirror, actor="alice", log_root=log_root)
    rd = edit_span(
        folder, mirror, "span:e1", "change_replacement",
        actor="alice", reason="prefer typed marker",
        new_replacement="[anonymized contact]",
        log_root=log_root,
    )
    assert "[anonymized contact]" in Path(rd.draft_path).read_text()
    events = list(MutationLog(folder, log_root=log_root).replay())
    edit_events = [e for e in events if e.extra.get("kind") == "mirror_edit"]
    assert len(edit_events) == 1
    assert edit_events[0].extra["operation"] == "change_replacement"
    assert edit_events[0].extra["before"]["replacement"] == "[REDACTED:email]"
    assert edit_events[0].extra["after"]["replacement"] == "[anonymized contact]"


# ---------------------------------------------------------------------------
# un_redact (privileged)
# ---------------------------------------------------------------------------


def test_un_redact_requires_controller_key_or_refuses(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    open_revision(folder, mirror, actor="alice", log_root=log_root)
    with pytest.raises(ControllerSignatureRequired):
        un_redact(folder, mirror, "span:e1",
                   actor="alice", controller_key="",
                   original_text="bob\x40example.com",
                   log_root=log_root)


def test_un_redact_default_re_triggers_lock_and_records_recheck_id(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    open_revision(folder, mirror, actor="alice", log_root=log_root)
    rd = un_redact(
        folder, mirror, "span:e1",
        actor="alice", reason="lawful basis: consent re-confirmed",
        controller_key="<TEST-OVERRIDE>",
        original_text="bob\x40example.com",
        recheck=True,
        log_root=log_root,
    )
    assert "bob\x40example.com" in Path(rd.draft_path).read_text()
    events = list(MutationLog(folder, log_root=log_root).replay())
    kinds = [e.extra.get("kind") for e in events]
    assert "lock_recheck" in kinds
    # The mirror_edit event for un_redact references the recheck audit id.
    edit_events = [e for e in events
                   if e.extra.get("kind") == "mirror_edit"
                   and e.extra.get("operation") == "un_redact"]
    assert len(edit_events) == 1
    assert edit_events[0].extra.get("lock_recheck_id")


def test_un_redact_with_no_recheck_emits_lock_skipped_event(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    open_revision(folder, mirror, actor="alice", log_root=log_root)
    un_redact(
        folder, mirror, "span:e1",
        actor="alice", reason="explicit bypass",
        controller_key="<TEST-OVERRIDE>",
        original_text="bob\x40example.com",
        recheck=False, log_root=log_root,
    )
    events = list(MutationLog(folder, log_root=log_root).replay())
    kinds = [e.extra.get("kind") for e in events]
    assert "mirror_edit_lock_skipped" in kinds


# ---------------------------------------------------------------------------
# revisions + diff
# ---------------------------------------------------------------------------


def test_revisions_list_chronological(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    open_revision(folder, mirror, actor="a", log_root=log_root)
    edit_span(folder, mirror, "span:e1", "change_replacement",
               actor="a", reason="r1",
               new_replacement="[X]", log_root=log_root)
    edit_span(folder, mirror, "span:e1", "change_replacement",
               actor="a", reason="r2",
               new_replacement="[Y]", log_root=log_root)
    revs = revisions_list(folder, mirror, log_root=log_root)
    # 1 opened + 2 edits
    assert [r.operation for r in revs] == ["opened",
                                            "change_replacement",
                                            "change_replacement"]
    assert [r.revision for r in revs] == [0, 1, 2]


def test_revisions_diff_returns_unified_format(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    open_revision(folder, mirror, actor="a", log_root=log_root)
    edit_span(folder, mirror, "span:e1", "change_replacement",
               actor="a", reason="",
               new_replacement="[REPLACEMENT-A]", log_root=log_root)
    diff = revisions_diff(folder, mirror, from_rev=0, log_root=log_root)
    assert diff.startswith("---") or "@@" in diff
    assert "[REPLACEMENT-A]" in diff or "[REDACTED:email]" in diff


# ---------------------------------------------------------------------------
# locking
# ---------------------------------------------------------------------------


def test_per_draft_lock_blocks_concurrent_open(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    open_revision(folder, mirror, actor="alice", log_root=log_root)
    with pytest.raises(LockHeldError):
        open_revision(folder, mirror, actor="bob", log_root=log_root)


# ---------------------------------------------------------------------------
# approve + discard
# ---------------------------------------------------------------------------


def test_approve_revision_promotes_draft_releases_lock(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    open_revision(folder, mirror, actor="alice", log_root=log_root)
    edit_span(folder, mirror, "span:e1", "change_replacement",
               actor="alice", reason="ok",
               new_replacement="[OK]", log_root=log_root)
    rec = approve_revision(folder, mirror, approver="carol", log_root=log_root)
    assert rec.kind == "oversight"
    assert Path(rec.mirror_path).exists()
    assert Path(rec.mirror_path).read_text().count("[OK]") == 1
    events = list(MutationLog(folder, log_root=log_root).replay())
    assert any(e.extra.get("kind") == "mirror_oversight" for e in events)
    # Lock file is gone.
    assert not Path(rec.mirror_path.replace(".approved.md", ".draft.md.lock")).exists()


def test_discard_revision_destroys_draft_writes_event(tmp_path):
    folder, mirror = _make_lock_mirror(tmp_path)
    log_root = _log_root(tmp_path)
    rd = open_revision(folder, mirror, actor="alice", log_root=log_root)
    assert Path(rd.draft_path).exists()
    aid = discard_revision(folder, mirror, actor="alice",
                            reason="false-positive review",
                            log_root=log_root)
    assert not Path(rd.draft_path).exists()
    assert aid
    events = list(MutationLog(folder, log_root=log_root).replay())
    assert any(e.extra.get("kind") == "mirror_edit_discarded" for e in events)


# ---------------------------------------------------------------------------
# MCP tool surface (B9.4)
# ---------------------------------------------------------------------------


def test_mcp_tools_registered():
    """0.6.6+: the mirror_* tools collapsed into the workspace_mirror facade;
    the facade is declared and every editor op stays reachable through it."""
    from rvnd.mcp_server import _DECLARED_TOOLS, workspace_mirror
    assert "workspace_mirror" in _DECLARED_TOOLS
    ops = {o["op"] for o in workspace_mirror("help")["ops"]}
    for op in ("edit", "un_redact", "history", "diff", "discard",
               "lock_acquire", "lock_release"):
        assert op in ops, f"{op} not reachable via workspace_mirror"
