# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""``erasure.execute`` must purge the versum mirror, not just the chain.

After the memory->versum body-drop, a knowledge-channel pair's body (the
default ``document`` channel, and every channel in
``memory._KNOWLEDGE_CHANNELS``) lives ONLY in the folder's ``.versum`` sink
-- never in the log event (``WorkspaceMemory.remember`` writes a body-less
event for these channels; see ``memory.py``). ``erasure.execute``'s
per-folder purge loop calls ``MutationLog.purge`` directly, which removes
the chain event but does not touch ``.versum`` -- so, pre-fix, a GDPR-erased
subject's knowledge survives physically on disk and re-reads as LIVE through
the memory read union (``WorkspaceMemory.by_id`` / ``search``), in both the
erased folder and every descendant.
"""

from __future__ import annotations

import pytest

from rvnd import erasure
from rvnd.adapters.versum import iter_records
from rvnd.memory import WorkspaceMemory


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Per-test log root + key dir; both keypairs initialised so purges
    don't fail for lack of a controller key.
    """
    log_root = tmp_path / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    from rvnd import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()

    parent = tmp_path / "ws"
    parent.mkdir(parents=True, exist_ok=True)
    child = parent / "child"
    child.mkdir(parents=True, exist_ok=True)
    return {"log_root": log_root, "parent": parent, "child": child}


def _pair(pid: str, summary: str, body: str) -> dict:
    return {
        "id": pid,
        "problem": {"id": "sha256:problem-" + pid[-8:],
                    "scope": "gdpr", "type": "case",
                    "summary": summary, "facets": {}},
        "solution": {"id": pid, "problem_id": "sha256:problem-" + pid[-8:],
                     "body": body, "body_format": "prose",
                     "authority_tier": 5, "confidence": 0.5,
                     "cited_sources": [], "extractor_chain": ["test:seed"]},
    }


def _versum_ids(folder) -> set[str]:
    """Every pair id physically present in one folder's versum sink
    (``exclude_erased=False`` so a lingering tombstone still counts as
    "not physically gone" -- the regression this test guards against)."""
    store = folder / ".versum"
    if not store.is_dir():
        return set()
    out: set[str] = set()
    for rec in iter_records(store, exclude_erased=False):
        props = rec.get("properties") if isinstance(rec, dict) else None
        body = props.get("record") if isinstance(props, dict) else None
        if isinstance(body, dict) and body.get("id"):
            out.add(str(body["id"]))
    return out


def test_execute_purges_versum_mirror_in_folder_and_descendant(isolated_env):
    log_root = isolated_env["log_root"]
    parent, child = isolated_env["parent"], isolated_env["child"]

    mem_parent = WorkspaceMemory(str(parent), log_root=str(log_root), actor="test")
    mem_child = WorkspaceMemory(str(child), log_root=str(log_root), actor="test")

    jane_parent_id = "sha256:jane-parent"
    jane_child_id = "sha256:jane-child"
    control_id = "sha256:john-control"

    mem_parent.remember(
        _pair(jane_parent_id, "Notes about Jane Doe's contract",
              "The agreement with Jane Doe covers 2025."),
        channel="document",
    )
    mem_child.remember(
        _pair(jane_child_id, "Follow-up regarding Jane Doe",
              "Jane Doe's renewal is due in Q2."),
        channel="document",
    )
    # Control: a DIFFERENT subject's knowledge in the SAME (parent) folder —
    # erasure must be targeted and leave this alone.
    mem_parent.remember(
        _pair(control_id, "Notes about John Smith's contract",
              "The agreement with John Smith covers 2025."),
        channel="document",
    )

    # --- sanity: bodies are physically present in .versum BEFORE erasure ---
    assert jane_parent_id in _versum_ids(parent), \
        "sanity: Jane's parent-folder body must be in .versum before erasure"
    assert jane_child_id in _versum_ids(child), \
        "sanity: Jane's child-folder body must be in .versum before erasure"
    assert control_id in _versum_ids(parent), \
        "sanity: John's body must be in .versum before erasure"
    assert mem_parent.by_id(jane_parent_id) is not None
    assert mem_child.by_id(jane_child_id) is not None

    # --- run the real erasure.execute for "Jane Doe", cascading to the child ---
    report = erasure.execute(
        str(parent), "Jane Doe",
        legal_basis="art_17_1_a",
        requester_ref="req:versum-purge",
        reason="erase Jane Doe per DSAR",
        cascade=True,
        log_root=log_root,
        actor="test",
    )
    assert not report.dry_run
    assert report.purged_event_count > 0, "the sweep must have found Jane's pairs"

    # --- Jane's knowledge must be PHYSICALLY GONE from .versum, both folders ---
    assert jane_parent_id not in _versum_ids(parent), (
        "erasure.execute must physically purge the versum mirror in the "
        "erased folder — knowledge-channel bodies live only in .versum "
        "post body-drop, so a chain-only purge leaves them recoverable"
    )
    assert jane_child_id not in _versum_ids(child), (
        "erasure.execute must physically purge the versum mirror in "
        "descendant folders too (cascade=True)"
    )

    # --- and must not resurface through the memory read union ---
    assert mem_parent.by_id(jane_parent_id) is None, \
        "purged pair must be gone from by_id (parent)"
    assert mem_child.by_id(jane_child_id) is None, \
        "purged pair must be gone from by_id (child)"
    assert not any(p.get("id") == jane_parent_id for p in mem_parent.all_pairs()), \
        "purged pair must be gone from all_pairs (parent)"
    assert not any(h.get("id") == jane_child_id
                   for h in mem_child.search("Jane Doe renewal", k=5)), \
        "purged pair must be gone from search (child)"

    # --- control: a different subject's knowledge in the same folder survives ---
    assert control_id in _versum_ids(parent), \
        "erasure must be targeted — an unrelated subject's knowledge must survive"
    assert mem_parent.by_id(control_id) is not None, \
        "control pair must still resolve via by_id after Jane's erasure"
