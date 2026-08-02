# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""F1 (0.6.8): folder mirrors — Lock + Oversight outputs as files.

The mirrors feature gives the controller two on-disk views of every
source:

  - ``mirrors/lock/<orig>.cleaned.md`` — Lock-redacted text.
  - ``mirrors/oversight/<orig>.approved.md`` — controller-approved snapshot.

Both come with a ``.spans.json`` sidecar listing every redaction. Every
mirror operation is recorded in the folder's mutation log so the
controller can later prove which redactions were approved.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from workspaces.mirrors import (
    SPANS_SCHEMA,
    MirrorRedactionError,
    MirrorRecord,
    approve_lock_mirror,
    generate_lock_mirror,
    list_mirrors,
)
from workspaces.mutation_log import MutationLog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "workspace"
    f.mkdir(parents=True)
    return f


@pytest.fixture
def log_root(tmp_path):
    return tmp_path / "logs"


def _write_source(folder: Path, name: str, body: str) -> Path:
    p = folder / name
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# generate_lock_mirror
# ---------------------------------------------------------------------------


def test_generate_lock_mirror_creates_cleaned_file(folder, log_root):
    src = _write_source(
        folder, "note.md",
        "Contact me at jane.doe\x40example.com or call (555) 123-4567 for details.",
    )
    rec = generate_lock_mirror(folder, src, log_root=log_root)

    # MirrorRecord shape.
    assert isinstance(rec, MirrorRecord)
    assert rec.kind == "lock"
    assert rec.source_path == str(src)
    assert rec.audit_id, "every mirror generation must produce an audit_id"

    # Files on disk.
    mp = Path(rec.mirror_path)
    sp = Path(rec.spans_path)
    assert mp.exists(), "cleaned mirror file must exist"
    assert sp.exists(), "spans sidecar must exist"
    assert mp.parent.name == "lock"
    assert mp.parent.parent.name == "mirrors"
    # The cleaned text should not contain the email verbatim.
    cleaned = mp.read_text(encoding="utf-8")
    assert "jane.doe\x40example.com" not in cleaned, (
        "lock mirror must redact emails"
    )


def test_spans_sidecar_lists_redactions(folder, log_root):
    src = _write_source(
        folder, "contact.md",
        "Email me at first.last\x40example.org please.",
    )
    rec = generate_lock_mirror(folder, src, log_root=log_root)
    sp = Path(rec.spans_path)
    sidecar = json.loads(sp.read_text(encoding="utf-8"))
    assert sidecar["schema"] == SPANS_SCHEMA
    assert sidecar["source_path"] == str(src)
    assert sidecar["mirror_kind"] == "lock"
    assert "source_hash" in sidecar
    spans = sidecar["spans"]
    assert isinstance(spans, list)
    assert len(spans) >= 1, "an email source must produce at least one span"
    for s in spans:
        assert {"start", "end", "kind", "original_hash", "replacement"} <= set(s)
        assert s["start"] >= 0
        assert s["end"] > s["start"]
        assert s["original_hash"].startswith("sha256:")


def test_generate_lock_mirror_fails_closed_without_redacted_text(
    folder, log_root, monkeypatch,
):
    src = _write_source(folder, "unsafe.md", "Sensitive value: private-contact")
    decision = SimpleNamespace(
        action="refuse",
        findings=[SimpleNamespace(remediation_actions=[])],
    )
    monkeypatch.setattr("workspaces.lock.lock_text", lambda *a, **k: decision)

    with pytest.raises(MirrorRedactionError, match="safe redacted text"):
        generate_lock_mirror(folder, src, log_root=log_root)

    assert not (folder / "mirrors").exists()


def test_generate_lock_mirror_fails_closed_when_lock_scan_errors(
    folder, log_root, monkeypatch,
):
    src = _write_source(folder, "unsafe.md", "Sensitive value: private-contact")

    def fail_scan(*args, **kwargs):
        raise OSError("scanner unavailable")

    monkeypatch.setattr("workspaces.lock.lock_text", fail_scan)

    with pytest.raises(MirrorRedactionError, match="scan failed"):
        generate_lock_mirror(folder, src, log_root=log_root)

    assert not (folder / "mirrors").exists()


# ---------------------------------------------------------------------------
# approve_lock_mirror
# ---------------------------------------------------------------------------


def test_approve_copies_to_oversight_dir(folder, log_root):
    src = _write_source(
        folder, "doc.md",
        "Owner: jane\x40example.com — sensitive details inside.",
    )
    lock = generate_lock_mirror(folder, src, log_root=log_root)
    oversight = approve_lock_mirror(
        folder, lock.mirror_path, approver="release-approver",
        log_root=log_root,
    )
    assert oversight.kind == "oversight"
    op = Path(oversight.mirror_path)
    assert op.exists()
    assert op.parent.name == "oversight"
    # The cleaned content should be byte-identical to the lock mirror.
    assert op.read_text(encoding="utf-8") == \
        Path(lock.mirror_path).read_text(encoding="utf-8")
    # The sidecar should now carry approver info.
    sp = Path(oversight.spans_path)
    sidecar = json.loads(sp.read_text(encoding="utf-8"))
    assert sidecar["mirror_kind"] == "oversight"
    assert sidecar["approver"] == "release-approver"
    assert sidecar["approved_at"] >= sidecar["created_at"]


def test_approve_requires_approver(folder, log_root):
    src = _write_source(folder, "x.md", "Email: someone\x40example.com")
    lock = generate_lock_mirror(folder, src, log_root=log_root)
    with pytest.raises(ValueError, match="approver"):
        approve_lock_mirror(folder, lock.mirror_path, approver="",
                              log_root=log_root)


# ---------------------------------------------------------------------------
# Audit chain integration
# ---------------------------------------------------------------------------


def test_mirror_created_event_in_audit_chain(folder, log_root):
    src = _write_source(folder, "audit.md", "PII: jane\x40example.com")
    rec = generate_lock_mirror(folder, src, log_root=log_root)

    log = MutationLog(folder, log_root=log_root)
    events = list(log.replay())
    mirror_events = [e for e in events
                     if e.extra.get("kind") == "mirror_lock"]
    assert len(mirror_events) == 1
    e = mirror_events[0]
    assert e.audit_id == rec.audit_id
    assert e.extra["mirror_path"] == rec.mirror_path
    assert e.extra["spans_path"] == rec.spans_path
    assert e.extra["span_count"] == rec.span_count

    # Approve, expect a second event.
    approve_lock_mirror(folder, rec.mirror_path, approver="alex",
                          log_root=log_root)
    events2 = list(log.replay())
    oversight_events = [e for e in events2
                        if e.extra.get("kind") == "mirror_oversight"]
    assert len(oversight_events) == 1
    assert oversight_events[0].extra["approver"] == "alex"
    # Chain should still verify.
    result = log.verify_chain()
    assert result.ok, (
        f"audit chain failed after mirror generate+approve: "
        f"broken={result.broken_links} sig_fail={result.signature_failures}"
    )


# ---------------------------------------------------------------------------
# list_mirrors
# ---------------------------------------------------------------------------


def test_list_mirrors_returns_both_kinds(folder, log_root):
    src1 = _write_source(folder, "a.md", "Email: a\x40example.com")
    src2 = _write_source(folder, "b.md", "Email: b\x40example.com")
    m1 = generate_lock_mirror(folder, src1, log_root=log_root)
    m2 = generate_lock_mirror(folder, src2, log_root=log_root)
    approve_lock_mirror(folder, m1.mirror_path, approver="alex",
                          log_root=log_root)

    all_records = list_mirrors(folder)
    kinds = sorted([r.kind for r in all_records])
    assert kinds == ["lock", "lock", "oversight"], (
        f"expected 2 lock + 1 oversight, got: {kinds}"
    )

    only_lock = list_mirrors(folder, kind="lock")
    assert all(r.kind == "lock" for r in only_lock)
    assert len(only_lock) == 2

    only_oversight = list_mirrors(folder, kind="oversight")
    assert len(only_oversight) == 1
    assert only_oversight[0].kind == "oversight"
