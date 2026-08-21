# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Erase-guard fail-closed regression (2026-06-11 Mac flake).

Two silent failure modes removed from ``ingest_file``'s B5 guard:
the ImportError fall-through (guard now imported at module top — an
unavailable guard fails ingest loudly) and the swallowed audit-append
failure (refusal still applies, loss of evidence is logged at ERROR)."""
from __future__ import annotations

import logging

import pytest

from rvnd import forgotten_subjects
from rvnd.inbox_watcher import ingest_file
from rvnd.mutation_log import MutationLog


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = tmp_path / "ws"
    (ws / "Inbox").mkdir(parents=True)
    return {"ws": ws, "log_root": tmp_path / "logs"}


def test_md_is_text_regardless_of_host_mime_tables(tmp_path):
    """Root cause of the 2026-06-12 Mac flake: hosts whose mimetypes lack
    text/markdown turned .md into binary metadata stubs, blinding the
    guard. The module registers the mapping itself; extraction of .md
    must yield the file's text on every platform."""
    import mimetypes

    import rvnd.inbox_watcher as iw

    assert mimetypes.guess_type("x.md")[0] == "text/markdown"
    f = tmp_path / "note.md"
    f.write_text("plain words about acmecorp", encoding="utf-8")
    extracted = iw.DefaultExtractor().extract(str(f), str(tmp_path))
    body = (extracted.pairs[0].get("solution") or {}).get("body", "")
    assert "acmecorp" in body, f"extraction stubbed the text: {body!r}"


def test_guard_import_is_top_level_not_optional():
    """The guard must not be lazily/optionally imported: a module-level
    attribute on inbox_watcher proves the import happens at import time."""
    import rvnd.inbox_watcher as iw

    assert getattr(iw, "_fs", None) is forgotten_subjects


def test_refusal_survives_audit_append_failure(env, monkeypatch, caplog):
    """If the audit append fails, the guard must STILL refuse (fail closed)
    and must log the lost evidence at ERROR — never silently."""
    ws, log_root = env["ws"], env["log_root"]
    forgotten_subjects.add(ws, "acmecorp", request_id="erase-req:fc1")
    bad = ws / "Inbox" / "leak.md"
    bad.write_text("notes about acmecorp again", encoding="utf-8")

    def boom(self, event):
        raise OSError("simulated audit failure")

    monkeypatch.setattr(MutationLog, "append", boom)
    with caplog.at_level(logging.ERROR, logger="rvnd.inbox_watcher"):
        with pytest.raises(forgotten_subjects.EraseGuardHit):
            ingest_file(bad, ws, log_root=log_root, actor="test")
    assert any("could NOT be audited" in r.message for r in caplog.records)


def test_happy_path_still_raises_and_audits(env):
    """The original contract: refusal raises AND the chain carries the
    EraseGuardHit event."""
    ws, log_root = env["ws"], env["log_root"]
    forgotten_subjects.add(ws, "acmecorp", request_id="erase-req:fc2")
    bad = ws / "Inbox" / "leak2.md"
    bad.write_text("more notes about acmecorp", encoding="utf-8")

    with pytest.raises(forgotten_subjects.EraseGuardHit):
        ingest_file(bad, ws, log_root=log_root, actor="test")

    log = MutationLog(ws, log_root=log_root)
    assert any((e.extra or {}).get("kind") == "EraseGuardHit"
               for e in log.replay())
