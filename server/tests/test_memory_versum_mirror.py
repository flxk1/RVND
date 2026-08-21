# SPDX-License-Identifier: AGPL-3.0-only
"""S1 of the memory→versum split: knowledge-channel remember() dual-writes to the
folder's versum sink; capture-evidence / audit channels stay local."""
from __future__ import annotations

from pathlib import Path

import versum
from rvnd.memory import WorkspaceMemory

_PAIR = {
    "id": "sha256:abc123",
    "problem": {"id": "p", "scope": "gdpr",
                "summary": "right to erasure under gdpr article 17",
                "facets": {"gdpr": {"article": "17", "tags": ["erasure"]}}},
    "solution": {"id": "s", "body": "the controller must erase without undue delay"},
}


def _mem(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    logroot = tmp_path / "logs"
    logroot.mkdir()
    return WorkspaceMemory(str(folder), log_root=str(logroot), actor="tester")


def test_knowledge_channel_mirrors_to_versum(tmp_path):
    mem = _mem(tmp_path)
    mem.remember(_PAIR, channel="document")
    store = Path(mem.folder_context) / ".versum"
    assert store.is_dir(), "a knowledge remember must create/populate the versum sink"
    hits = versum.search_records(store, "right to erasure", k=5)
    assert hits, "the remembered knowledge pair must be findable in versum"
    # dual-write: the log still holds it too, so by_id resolves it in S1/S2
    assert mem.by_id(_PAIR["id"]) is not None


def test_capture_channel_does_not_mirror(tmp_path):
    mem = _mem(tmp_path)
    mem.remember({**_PAIR, "id": "sha256:def456"}, channel="llm_answer")
    store = Path(mem.folder_context) / ".versum"
    assert not store.exists(), "capture-evidence stays local — no versum mirror"


def test_read_union_serves_versum_only_pair(tmp_path):
    """S2: a knowledge pair that lives ONLY in versum (never remembered to the
    log) is served by search / by_id / all_pairs — the read path now sources
    knowledge from versum, not just the log."""
    mem = _mem(tmp_path)
    store = Path(mem.folder_context) / ".versum"
    store.mkdir(parents=True, exist_ok=True)
    versum.append_record(
        str(store),
        record={"id": "sha256:vonly",
                "problem": {"summary": "unique versum-only marker phrase",
                            "facets": {}},
                "solution": {"body": "the answer body"}},
        dimension="relational", actor="tester")
    hits = mem.search("unique versum-only marker phrase", k=5)
    assert any(h.get("id") == "sha256:vonly" for h in hits), "search must union versum"
    assert mem.by_id("sha256:vonly") is not None, "by_id must fall through to versum"
    assert any(p.get("id") == "sha256:vonly" for p in mem.all_pairs()), \
        "all_pairs must include versum-only knowledge"


def test_purge_erases_the_versum_mirror(tmp_path):
    """A purge must hide the pair from the whole read union — including its versum
    mirror. Without erasure sync the union would resurface a purged pair (the log
    event is physically gone, but versum still held the body)."""
    mem = _mem(tmp_path)
    mem.remember(_PAIR, channel="document")  # dual-written: log + versum
    assert mem.by_id(_PAIR["id"]) is not None
    mem.purge_pair(_PAIR["id"], legal_basis="art_17_1_a", requester_ref="r", reason="t")
    assert mem.by_id(_PAIR["id"]) is None, "purged pair must be gone from by_id"
    assert not any(p.get("id") == _PAIR["id"] for p in mem.all_pairs()), \
        "purged pair must be gone from all_pairs"
    assert not any(h.get("id") == _PAIR["id"]
                   for h in mem.search("right to erasure", k=5)), \
        "purged pair must be gone from search"
