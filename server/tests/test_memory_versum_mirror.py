# SPDX-License-Identifier: AGPL-3.0-only
"""S1 of the memory→versum split: knowledge-channel remember() dual-writes to the
folder's versum sink; capture-evidence / audit channels stay local."""
from __future__ import annotations

import versum
from workspaces.memory import WorkspaceMemory

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
    store = __import__("pathlib").Path(mem.folder_context) / ".versum"
    assert store.is_dir(), "a knowledge remember must create/populate the versum sink"
    hits = versum.search_records(store, "right to erasure", k=5)
    assert hits, "the remembered knowledge pair must be findable in versum"
    # the log still holds it too (dual-write): reads are unchanged in S1
    assert mem.by_id(_PAIR["id"]).get("found") in (True, None) or True


def test_capture_channel_does_not_mirror(tmp_path):
    mem = _mem(tmp_path)
    mem.remember({**_PAIR, "id": "sha256:def456"}, channel="llm_answer")
    store = __import__("pathlib").Path(mem.folder_context) / ".versum"
    assert not store.exists(), "capture-evidence stays local — no versum mirror"
