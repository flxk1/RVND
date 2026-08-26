# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""``erasure.execute`` must not silently report completion over a versum
mirror it could not reach because the workspace is SEALED.

``_erase_versum_mirror`` treats ``not (folder / ".versum").is_dir()`` as
"nothing to erase" -- true when the folder genuinely has no versum, but also
true when the folder is sealed: sealing packs ``.versum`` into the same
encrypted blob as the log (see ``seal.py``'s ``_FOLDER_MEMORY_SINKS``) and
removes the plaintext directory. Pre-fix, a sealed folder's knowledge body
survives physically (inside the seal) while the erasure report gives no
indication anything was left behind -- a GDPR "erase" that quietly leaves a
copy and does not say so.

Test design note -- why the sealed side uses ``dry_run=True``: a REAL
(non-dry-run) ``execute()`` against a folder whose OWN chain is sealed always
raises ``SealedWriteError`` on the composite-tombstone write (``execute()``
unconditionally appends the composite event to the target folder's own log --
see ``test_erasure_cards.py::test_execute_on_sealed_workspace_destroys_nothing``
for the pre-existing, unrelated proof of that refusal). That is a loud,
honest failure in its own right and is untouched by this fix. To exercise the
*silent* blind-spot this fix closes -- reaching the report without an
exception in the way -- the sealed side of this test previews with
``dry_run=True`` (still a real call into ``erasure.execute``); the control
side uses a real, non-dry-run execute on a separate unsealed folder in the
same test to prove the prior physical-erase fix is untouched.
"""

from __future__ import annotations

import pytest

from rvnd import erasure, seal
from rvnd.adapters.versum import iter_records
from rvnd.memory import WorkspaceMemory


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd import signing
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    return {"log_root": tmp_path / "logs"}


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
    """Every pair id physically present in one folder's versum sink."""
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


def test_execute_names_sealed_versum_blind_spot_and_still_purges_unsealed(env, tmp_path):
    log_root = env["log_root"]

    sealed_folder = tmp_path / "sealed_ws"; sealed_folder.mkdir()
    open_folder = tmp_path / "open_ws"; open_folder.mkdir()

    jane_sealed_id = "sha256:jane-sealed"
    jane_open_id = "sha256:jane-open"

    mem_sealed = WorkspaceMemory(str(sealed_folder), log_root=str(log_root), actor="t")
    mem_sealed.remember(
        _pair(jane_sealed_id, "Notes about Jane Doe (sealed)",
              "Jane Doe's file, later sealed at rest."),
        channel="document",
    )

    mem_open = WorkspaceMemory(str(open_folder), log_root=str(log_root), actor="t")
    mem_open.remember(
        _pair(jane_open_id, "Notes about Jane Doe (open)",
              "Jane Doe's file, stays unsealed."),
        channel="document",
    )

    # sanity: both folders physically hold Jane's body before erasure
    assert jane_sealed_id in _versum_ids(sealed_folder)
    assert jane_open_id in _versum_ids(open_folder)

    seal.seal_folder(sealed_folder, passphrase="pw", log_root=log_root)
    assert not (sealed_folder / ".versum").exists(), \
        "sanity: the plaintext versum dir is gone once sealed -- packed into the seal blob"

    # --- 1) sealed folder: the erasure report must NAME the blind spot ---
    report = erasure.execute(
        str(sealed_folder), "Jane Doe",
        legal_basis="art_17_1_a", requester_ref="req:sealed-versum",
        reason="erase Jane Doe per DSAR",
        dry_run=True, log_root=log_root, actor="test",
    )
    assert report.dry_run
    assert not report.purged_pairs, "a sealed chain contributes no purge hits"
    assert report.purged_event_count == 0
    # the blind spot is named, not silently absorbed into an empty/clean report
    assert report.versum_sealed == [report.folder_context], (
        "erasure must name the sealed folder as a versum blind spot -- it "
        "must never look indistinguishable from a folder with no versum at all"
    )
    assert report.sweep.versum_sealed == [report.folder_context]
    assert report.sweep.to_dict()["versum_sealed"] == [report.folder_context]

    # --- 2) control: an UNSEALED folder in the same test still gets its
    # versum body PHYSICALLY erased (the prior fix), and is not flagged. ---
    report2 = erasure.execute(
        str(open_folder), "Jane Doe",
        legal_basis="art_17_1_a", requester_ref="req:open-versum",
        reason="erase Jane Doe per DSAR",
        log_root=log_root, actor="test",
    )
    assert not report2.dry_run
    assert report2.purged_event_count > 0
    assert report2.versum_sealed == []
    assert jane_open_id not in _versum_ids(open_folder), \
        "the prior fix (physical versum purge for an unsealed folder) must still work"


def test_unsealed_folder_with_no_versum_at_all_is_not_flagged_sealed(env, tmp_path):
    """Case (a) from the docstring -- genuinely nothing to erase -- must stay
    a silent, correct no-op: ``versum_sealed`` names blind spots, not every
    folder that simply has no versum mirror."""
    folder = tmp_path / "ws"; folder.mkdir()
    log_root = env["log_root"]
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(
        _pair("sha256:no-jane", "Notes about someone else", "unrelated body"),
        channel="document",
    )
    report = erasure.execute(
        str(folder), "Jane Doe",
        legal_basis="art_17_1_a", requester_ref="req:no-hit",
        reason="erase Jane Doe per DSAR",
        log_root=log_root, actor="test",
    )
    assert report.versum_sealed == []
    assert report.sweep.versum_sealed == []


def test_queue_if_sealed_closes_the_versum_blind_spot_on_unseal(env, tmp_path, monkeypatch):
    """The blind spot this file is about (a sealed folder's versum mirror is
    unreachable at execute time) is CLOSED, not merely named, when
    WORKSPACE_PENDING_ERASE + queue_if_sealed are both on: a marker is
    armed instead of the sweep giving up, and once the folder unseals the
    knowledge body is physically erased -- the same outcome the open-folder
    control case already gets, just deferred to unseal time.

    Subject is a single TOKEN (not "Jane Doe") deliberately: the unseal-time
    matcher is token/full-text, not substring (see test_pending_erase.py's
    recall-gap test) -- a multi-word subject like "Jane Doe" only recalls
    when it is the WHOLE haystack text, which this test does not construct.
    """
    monkeypatch.setenv("WORKSPACE_PENDING_ERASE", "1")
    from rvnd import seal as seal_mod
    from rvnd.adapters.versum import iter_records
    from rvnd.registry import add_known_workspace

    log_root = env["log_root"]
    folder = tmp_path / "sealed_ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)

    subject = "JaneDoeQueued99"
    jane_id = "sha256:jane-queued"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(
        _pair(jane_id, f"Notes about {subject} (queued)",
              f"{subject}'s file, sealed then queued for erasure."),
        channel="document",
    )
    assert jane_id in _versum_ids(folder)

    seal_mod.seal_folder(folder, passphrase="pw", log_root=log_root)

    report = erasure.execute(
        str(folder), subject,
        legal_basis="art_17_1_a", requester_ref="req:queued-versum",
        reason=f"erase {subject} per DSAR",
        log_root=log_root, actor="test", queue_if_sealed=True,
    )
    assert report.pending_erase_queued == [str(folder.resolve())]
    # A marker WAS armed for it -- the versum body is not certified erased
    # yet (still inside the seal), but it is no longer an unresolved blind
    # spot either: it is a tracked, signed, pending action.
    assert report.composite_tombstone_id == ""

    seal_mod.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert jane_id not in {
        str((r.get("properties") or {}).get("record", {}).get("id"))
        for r in iter_records(folder / ".versum", exclude_erased=False)
        if isinstance(r, dict)
    }
