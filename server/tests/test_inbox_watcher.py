# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the inbox watcher (B1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rvnd import (
    DefaultExtractor,
    ExtractedFile,
    INBOX_SUBDIR,
    InboxWatcher,
    WorkspaceMemory,
    ingest_file,
)


@pytest.fixture
def log_root(tmp_path):
    return tmp_path / "logs"


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "vault"
    f.mkdir()
    return f


def _drop(folder: Path, name: str, content: bytes | str = b"hello") -> Path:
    inbox = folder / INBOX_SUBDIR
    inbox.mkdir(parents=True, exist_ok=True)
    p = inbox / name
    if isinstance(content, str):
        p.write_text(content, encoding="utf-8")
    else:
        p.write_bytes(content)
    return p


# ===========================================================================
# DefaultExtractor — file → pair
# ===========================================================================


def test_default_extractor_produces_one_pair_per_file(folder):
    p = _drop(folder, "test.txt", "hello world")
    ext = DefaultExtractor()
    result = ext.extract(str(p), str(folder))

    assert isinstance(result, ExtractedFile)
    assert result.file_size == len("hello world")
    assert result.mime_type == "text/plain"
    assert len(result.pairs) == 1

    pair = result.pairs[0]
    assert pair["problem"]["summary"] == "test.txt"
    assert pair["problem"]["facets"]["filename"] == "test.txt"
    assert pair["problem"]["source_document"] == str(p.resolve())


def test_default_extractor_pair_id_is_file_hash(folder):
    p = _drop(folder, "a.txt", "content A")
    pair1 = DefaultExtractor().extract(str(p), str(folder)).pairs[0]
    pair2 = DefaultExtractor().extract(str(p), str(folder)).pairs[0]
    # Same bytes → same pair_id.
    assert pair1["id"] == pair2["id"]


def test_default_extractor_different_files_different_pair_ids(folder):
    p1 = _drop(folder, "a.txt", "content A")
    p2 = _drop(folder, "b.txt", "content B")
    id1 = DefaultExtractor().extract(str(p1), str(folder)).pairs[0]["id"]
    id2 = DefaultExtractor().extract(str(p2), str(folder)).pairs[0]["id"]
    assert id1 != id2


def test_default_extractor_binary_file(folder):
    p = _drop(folder, "blob.bin", b"\x00\x01\x02\x03\x04")
    result = DefaultExtractor().extract(str(p), str(folder))
    pair = result.pairs[0]
    # Binary files don't get a text preview as the solution body.
    assert "(binary file" in pair["solution"]["body"]
    assert pair["solution"]["body_format"] == "metadata"


# ===========================================================================
# InboxWatcher — basic behaviour
# ===========================================================================


def test_watcher_run_once_ingests_new_files(folder, log_root):
    _drop(folder, "a.txt", "alpha")
    _drop(folder, "b.txt", "beta")
    watcher = InboxWatcher(folder, log_root=log_root)
    new_ids = watcher.run_once()
    assert len(new_ids) == 2


def test_watcher_run_once_no_inbox_scans_workspace_root(folder, log_root):
    """When Inbox/ doesn't exist, the watcher scans the workspace root
    directly. Behaviour changed from auto-creating Inbox/ so users don't
    need to organise drops into a special subfolder.
    """
    watcher = InboxWatcher(folder, log_root=log_root)
    assert not (folder / INBOX_SUBDIR).exists()
    new_ids = watcher.run_once()
    # No files in the workspace root → nothing ingests.
    assert new_ids == []
    # The Inbox/ should NOT have been auto-created.
    assert not (folder / INBOX_SUBDIR).exists()
    # scan_path resolves to the workspace root in this case.
    assert watcher.scan_path == folder


def test_watcher_idempotent_on_rescan(folder, log_root):
    """Re-running run_once on the same files produces zero new pairs."""
    _drop(folder, "a.txt", "alpha")
    watcher = InboxWatcher(folder, log_root=log_root)
    first = watcher.run_once()
    second = watcher.run_once()
    assert len(first) == 1
    assert second == []


def test_watcher_detects_new_file_after_first_scan(folder, log_root):
    _drop(folder, "a.txt", "alpha")
    watcher = InboxWatcher(folder, log_root=log_root)
    assert len(watcher.run_once()) == 1

    _drop(folder, "b.txt", "beta")
    new_ids = watcher.run_once()
    assert len(new_ids) == 1


def test_watcher_skips_hidden_files(folder, log_root):
    _drop(folder, ".DS_Store", "hidden")
    _drop(folder, "a.txt", "alpha")
    watcher = InboxWatcher(folder, log_root=log_root)
    new_ids = watcher.run_once()
    assert len(new_ids) == 1


def test_watcher_skips_subdirectories(folder, log_root):
    (folder / INBOX_SUBDIR / "subfolder").mkdir(parents=True)
    _drop(folder, "a.txt", "alpha")
    watcher = InboxWatcher(folder, log_root=log_root)
    new_ids = watcher.run_once()
    assert len(new_ids) == 1


# ===========================================================================
# Watcher writes to correct folder + asymmetric rule preserved
# ===========================================================================


def test_watcher_writes_to_correct_folder(tmp_path, log_root):
    hr = tmp_path / "HR"
    eng = tmp_path / "Engineering"
    hr.mkdir()
    eng.mkdir()
    _drop(hr, "hr-file.txt", "hr content")
    _drop(eng, "eng-file.txt", "eng content")

    InboxWatcher(hr, log_root=log_root).run_once()
    InboxWatcher(eng, log_root=log_root).run_once()

    # Each folder sees only its own ingest.
    hr_mem = WorkspaceMemory(hr, log_root=log_root)
    eng_mem = WorkspaceMemory(eng, log_root=log_root)

    hr_filenames = {p["problem"]["facets"]["filename"] for p in hr_mem.all_pairs()}
    eng_filenames = {p["problem"]["facets"]["filename"] for p in eng_mem.all_pairs()}

    assert hr_filenames == {"hr-file.txt"}
    assert eng_filenames == {"eng-file.txt"}


def test_watcher_asymmetric_rule_holds(tmp_path, log_root):
    """Watcher ingest into a sub-folder is visible to the parent (per the rule)."""
    acme = tmp_path / "acme"
    hr = tmp_path / "acme" / "HR"
    eng = tmp_path / "acme" / "Engineering"
    for p in (acme, hr, eng):
        p.mkdir(parents=True)

    _drop(hr, "hr-policy.txt", "hr policy content")
    InboxWatcher(hr, log_root=log_root).run_once()

    # /acme/ sees the HR ingest.
    acme_mem = WorkspaceMemory(acme, log_root=log_root)
    acme_files = {p["problem"]["facets"]["filename"] for p in acme_mem.all_pairs()}
    assert "hr-policy.txt" in acme_files

    # /acme/Engineering/ does NOT see HR's ingest.
    eng_mem = WorkspaceMemory(eng, log_root=log_root)
    eng_files = {p["problem"]["facets"]["filename"] for p in eng_mem.all_pairs()}
    assert "hr-policy.txt" not in eng_files


# ===========================================================================
# Custom extractor
# ===========================================================================


def test_custom_extractor_honoured(folder, log_root):
    """An Extractor that returns 3 pairs per file is dispatched correctly."""
    class TripleExtractor:
        extractor_id = "triple"

        def extract(self, file_path, folder_context):
            # Use a unique hash per file so idempotency doesn't kick in.
            file_hash = "sha256:" + Path(file_path).name.encode().hex()[:32]
            pairs = []
            for i in range(3):
                pid = f"sha256:triple-{Path(file_path).name}-{i}"
                pairs.append({
                    "id": pid,
                    "problem": {"id": pid, "scope": "custom", "type": "t",
                                "summary": f"facet {i} of {Path(file_path).name}",
                                "facets": {},
                                "source_document": file_path},
                    "solution": {"id": pid, "problem_id": pid, "body": "x",
                                 "body_format": "prose",
                                 "authority_tier": 3, "confidence": 0.9},
                })
            return ExtractedFile(
                file_path=file_path,
                file_size=0,
                file_hash=file_hash,
                mime_type="text/plain",
                content_preview="",
                pairs=pairs,
            )

    _drop(folder, "a.txt", "alpha")
    watcher = InboxWatcher(folder, log_root=log_root, extractor=TripleExtractor())
    new_ids = watcher.run_once()
    assert len(new_ids) == 3


# ===========================================================================
# ingest_file (one-off)
# ===========================================================================


def test_ingest_file_one_off(folder, log_root, tmp_path):
    # Put the file OUTSIDE the Inbox — direct ingest path.
    p = tmp_path / "loose-file.txt"
    p.write_text("loose content")

    pair_ids = ingest_file(p, folder, log_root=log_root)
    assert len(pair_ids) == 1

    mem = WorkspaceMemory(folder, log_root=log_root)
    files = {pp["problem"]["facets"]["filename"] for pp in mem.all_pairs()}
    assert "loose-file.txt" in files


def test_ingest_file_idempotent(folder, log_root, tmp_path):
    p = tmp_path / "file.txt"
    p.write_text("content")
    first = ingest_file(p, folder, log_root=log_root)
    second = ingest_file(p, folder, log_root=log_root)
    assert len(first) == 1
    assert second == []   # already ingested


def test_ingest_file_missing(folder, log_root, tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_file(tmp_path / "does-not-exist.txt", folder, log_root=log_root)


# ===========================================================================
# Cascade delete works for watcher-ingested files
# ===========================================================================


def test_delete_document_cascades_for_watcher_ingest(folder, log_root):
    """A file dropped into Inbox + later deleted via delete_document should
    remove the ingested pair from reads."""
    p = _drop(folder, "doomed.txt", "doomed content")
    InboxWatcher(folder, log_root=log_root).run_once()

    mem = WorkspaceMemory(folder, log_root=log_root)
    assert len(mem.all_pairs()) == 1

    n = mem.delete_document(str(p.resolve()))
    assert n == 1
    assert mem.all_pairs() == []


# ===========================================================================
# CLI integration
# ===========================================================================


def test_cli_ingest_command(folder, log_root, capsys, tmp_path):
    from rvnd.cli import main
    p = folder / "f.txt"
    p.write_text("cli ingest")

    rc = main(["--log-root", str(log_root), "ingest",
               "--folder", str(folder), str(p)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ingested 1 new pair" in out


def test_cli_ingest_idempotent(folder, log_root, capsys, tmp_path):
    from rvnd.cli import main
    p = folder / "f.txt"
    p.write_text("x")
    main(["--log-root", str(log_root), "ingest",
          "--folder", str(folder), str(p)])
    capsys.readouterr()  # drain

    rc = main(["--log-root", str(log_root), "ingest",
               "--folder", str(folder), str(p)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "already ingested" in out


def test_cli_ingest_missing_file(folder, log_root, capsys, tmp_path):
    from rvnd.cli import main
    rc = main(["--log-root", str(log_root), "ingest",
               "--folder", str(folder), str(tmp_path / "missing.txt")])
    assert rc == 1


def test_cli_watch_once(folder, log_root, capsys):
    from rvnd.cli import main
    _drop(folder, "a.txt", "alpha")
    _drop(folder, "b.txt", "beta")

    rc = main(["--log-root", str(log_root), "watch",
               "--folder", str(folder), "--once"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ingested 2 new pair" in out


def test_cli_watch_once_empty_inbox(folder, log_root, capsys):
    from rvnd.cli import main
    rc = main(["--log-root", str(log_root), "watch",
               "--folder", str(folder), "--once"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no new files" in out
