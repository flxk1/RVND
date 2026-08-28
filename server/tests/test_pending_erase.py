# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Pending-erasure markers (``rvnd.pending_erase``) — GDPR Art. 17 erasure
against a SEALED folder.

Feature-flagged: every test in this module sets ``WORKSPACE_PENDING_ERASE=1``
explicitly (default OFF elsewhere — see ``test_erasure_cards.py::
test_execute_on_sealed_workspace_destroys_nothing`` for the flag-off,
unchanged-default proof).

The top security probe here is erase-injection: a filesystem attacker who can
write into the sealed-blob directory must not be able to make ``unseal``
delete data nobody asked to erase. Every "bad marker" test below asserts BOTH
halves of fail-closed: the unseal raises, AND nothing was restored/deleted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rvnd import erasure, pending_erase, seal, signing
from rvnd.adapters.versum import iter_records
from rvnd.memory import WorkspaceMemory
from rvnd.mutation_log import MutationLog, SealedWriteError
from rvnd.registry import add_known_workspace


# ---------------------------------------------------------------------------
# Fixtures + small helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Feature ON, both keys initialised (two-key / controller-present path)."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_PENDING_ERASE", "1")
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    return {"log_root": tmp_path / "logs"}


@pytest.fixture
def env_operator_only(tmp_path, monkeypatch):
    """Feature ON, only the operator key exists — no controller key ever
    initialised. Exercises the L0 single-key fallback (decision 1)."""
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACE_PENDING_ERASE", "1")
    signing.ensure_keypair()
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
    store = Path(folder) / ".versum"
    if not store.is_dir():
        return set()
    out: set[str] = set()
    for rec in iter_records(store, exclude_erased=False):
        props = rec.get("properties") if isinstance(rec, dict) else None
        body = props.get("record") if isinstance(props, dict) else None
        if isinstance(body, dict) and body.get("id"):
            out.add(str(body["id"]))
    return out


def _chain_pair_ids(folder, log_root) -> set[str]:
    return {evt.pair_id for evt in MutationLog(str(folder), log_root=log_root).replay()}


def _ledger_path(folder, log_root):
    return pending_erase._marker_ledger_path(str(folder), log_root)


def _read_ledger(folder, log_root) -> list[dict]:
    p = _ledger_path(folder, log_root)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _write_ledger(folder, log_root, markers: list[dict]) -> None:
    p = _ledger_path(folder, log_root)
    p.write_text("".join(json.dumps(m) + "\n" for m in markers))


def _execute(env, folder, subject, **kw):
    return erasure.execute(
        str(folder), subject, legal_basis="art_17_1_a",
        requester_ref="ticket-1", reason="test",
        log_root=env["log_root"], **kw)


# ---------------------------------------------------------------------------
# 1) arm -> seal -> unseal APPLIES the erasure
# ---------------------------------------------------------------------------


def test_arm_seal_unseal_applies_erasure(env, tmp_path):
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)

    jane_id = "sha256:jane-1"
    other_id = "sha256:other-1"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(jane_id, "Notes about JaneUniqueSubj42",
                        "JaneUniqueSubj42's file, later sealed at rest."),
                 channel="document")
    mem.remember(_pair(other_id, "Notes about someone else",
                        "totally unrelated body"),
                 channel="document")

    assert jane_id in _versum_ids(folder)
    assert other_id in _versum_ids(folder)

    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    assert seal.is_sealed(folder, log_root=log_root)

    report = _execute(env, folder, "JaneUniqueSubj42", queue_if_sealed=True)
    assert report.pending_erase_queued == [str(folder.resolve())]
    assert len(report.pending_markers) == 1
    assert report.pending_markers[0]["controller_keyid"] is not None
    assert report.composite_tombstone_id == ""   # deferred — root is sealed

    ledger_before = _read_ledger(folder, log_root)
    assert len(ledger_before) == 1

    result = seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert result["unsealed"] is True
    applied = result["pending_erase_applied"]
    assert applied["purged_pair_count"] == 1
    assert applied["purged_event_count"] >= 1
    assert applied["errors"] == []

    # subject's pair is gone from the restored chain AND the versum mirror;
    # the unrelated pair survives untouched.
    assert jane_id not in _chain_pair_ids(folder, log_root)
    assert jane_id not in _versum_ids(folder)
    assert other_id in _chain_pair_ids(folder, log_root)
    assert other_id in _versum_ids(folder)

    # the composite audit breadcrumb landed on the now-restored log.
    kinds = [evt.extra.get("kind") for evt in
             MutationLog(str(folder), log_root=log_root).replay()]
    assert "erasure_pending_applied" in kinds

    # marker consumed — nothing left to reapply.
    assert _read_ledger(folder, log_root) == []
    assert not seal.is_sealed(folder, log_root=log_root)


# ---------------------------------------------------------------------------
# 2) FORGED marker => unseal fails closed, restores nothing, deletes nothing
# ---------------------------------------------------------------------------


def _seal_and_arm(env, tmp_path, subject="ForgedSubjXyz", *, controller=True):
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)
    pid = "sha256:target-1"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(pid, f"Notes about {subject}", f"{subject}'s file"),
                 channel="document")
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    _execute(env, folder, subject, queue_if_sealed=True)
    return folder, log_root, pid


def test_forged_body_field_fails_closed(env, tmp_path):
    folder, log_root, pid = _seal_and_arm(env, tmp_path)
    markers = _read_ledger(folder, log_root)
    assert len(markers) == 1
    # Tamper with a bound body field WITHOUT re-signing — this is exactly
    # what an attacker who can write the marker file but not the signing
    # keys can do.
    markers[0]["subject_hash"] = "0" * 64
    _write_ledger(folder, log_root, markers)

    with pytest.raises(seal.SealError):
        seal.unseal_folder(folder, passphrase="pw", log_root=log_root)

    # nothing restored, nothing deleted — the folder is still sealed and
    # the (bad) marker is left exactly where it was for inspection.
    assert seal.is_sealed(folder, log_root=log_root)
    assert len(_read_ledger(folder, log_root)) == 1


def test_forged_operator_signature_fails_closed(env, tmp_path):
    folder, log_root, pid = _seal_and_arm(env, tmp_path)
    markers = _read_ledger(folder, log_root)
    markers[0]["operator_sig"] = "ff" * 64
    _write_ledger(folder, log_root, markers)

    with pytest.raises(seal.SealError):
        seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert seal.is_sealed(folder, log_root=log_root)


def test_missing_controller_signature_when_claimed_fails_closed(env, tmp_path):
    """The marker claims a controller co-signature (controller_keyid set)
    but the signature itself has been stripped — must NOT be silently
    downgraded to operator-only acceptance."""
    folder, log_root, pid = _seal_and_arm(env, tmp_path)
    markers = _read_ledger(folder, log_root)
    assert markers[0]["controller_keyid"] is not None
    markers[0]["controller_sig"] = ""
    _write_ledger(folder, log_root, markers)

    with pytest.raises(seal.SealError):
        seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert seal.is_sealed(folder, log_root=log_root)


def test_forged_controller_signature_fails_closed(env, tmp_path):
    folder, log_root, pid = _seal_and_arm(env, tmp_path)
    markers = _read_ledger(folder, log_root)
    markers[0]["controller_sig"] = "ab" * 64
    _write_ledger(folder, log_root, markers)

    with pytest.raises(seal.SealError):
        seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert seal.is_sealed(folder, log_root=log_root)


def test_forged_subject_token_count_fails_closed(env, tmp_path):
    """subject_token_count is INSIDE the signed body (it governs the n-gram
    window width at apply time), so widening/narrowing it without
    re-signing must be caught exactly like tampering any other bound
    field — an attacker who could freely change the window width could
    otherwise widen a narrow subject into a broad substring-like match, or
    narrow a phrase down to a single shared token and over-erase."""
    folder, log_root, pid = _seal_and_arm(env, tmp_path, subject="TokCountSubjKlm")
    markers = _read_ledger(folder, log_root)
    assert markers[0]["subject_token_count"] == 1
    markers[0]["subject_token_count"] = 99
    _write_ledger(folder, log_root, markers)

    with pytest.raises(seal.SealError):
        seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert seal.is_sealed(folder, log_root=log_root)
    assert len(_read_ledger(folder, log_root)) == 1


# ---------------------------------------------------------------------------
# 3) STRIPPED marker => data intact, no deletion, safe-by-omission
# ---------------------------------------------------------------------------


def test_stripped_marker_is_safe_by_omission(env, tmp_path):
    folder, log_root, pid = _seal_and_arm(env, tmp_path, subject="StrippedSubjQrs")
    _write_ledger(folder, log_root, [])   # marker file wiped

    result = seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert result["unsealed"] is True
    assert "pending_erase_applied" not in result

    # the erasure that was PENDING never applies — the subject's pair is
    # still there. Nothing was WRONGLY deleted; this is the accepted
    # trade-off of "absent marker = no escalation".
    assert pid in _chain_pair_ids(folder, log_root)
    assert not seal.is_sealed(folder, log_root=log_root)


# ---------------------------------------------------------------------------
# 4) MOVED/REPLAYED marker => rejected
# ---------------------------------------------------------------------------


def test_moved_marker_wrong_folder_hash_is_rejected(env, tmp_path):
    folder, log_root, pid = _seal_and_arm(env, tmp_path, subject="MovedSubjLmn")
    markers = _read_ledger(folder, log_root)
    markers[0]["folder_hash"] = "not-this-folder-0123456789abcdef01234567"
    _write_ledger(folder, log_root, markers)

    with pytest.raises(seal.SealError):
        seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert seal.is_sealed(folder, log_root=log_root)


def test_replayed_marker_stale_blob_fingerprint_is_rejected(env, tmp_path):
    folder, log_root, pid = _seal_and_arm(env, tmp_path, subject="ReplaySubjOpq")
    markers = _read_ledger(folder, log_root)
    markers[0]["sealed_blob_fingerprint"] = "0" * 64
    _write_ledger(folder, log_root, markers)

    with pytest.raises(seal.SealError):
        seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert seal.is_sealed(folder, log_root=log_root)


# ---------------------------------------------------------------------------
# 5) IDEMPOTENT re-apply — converges, no double effect
# ---------------------------------------------------------------------------


def test_apply_markers_converges_on_replay(env, tmp_path):
    folder, log_root, pid = _seal_and_arm(env, tmp_path, subject="IdemSubjTuv")
    marker = _read_ledger(folder, log_root)[0]

    result = seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    first = result["pending_erase_applied"]
    assert first["purged_pair_count"] == 1
    assert pid not in _chain_pair_ids(folder, log_root)

    # Re-apply the SAME (already-consumed) marker directly — simulates a
    # marker that survives to a second apply (e.g. a crash between a
    # successful apply and the ledger rewrite). Must converge to zero
    # additional effect, not error, not double-purge.
    second = pending_erase.apply_markers(str(folder), [marker], log_root=log_root)
    assert second["purged_pair_count"] == 0
    assert second["purged_event_count"] == 0
    assert second["errors"] == []
    assert pid not in _chain_pair_ids(folder, log_root)


# ---------------------------------------------------------------------------
# 6) Recall: single-word standalone token caught; mid-word embedding missed
#    (unchanged by the n-gram upgrade — n=1 degrades to the old token path).
# ---------------------------------------------------------------------------


def test_recall_gap_token_caught_embedded_missed(env, tmp_path):
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)

    standalone_id = "sha256:standalone-1"
    embedded_id = "sha256:embedded-1"
    subject = "Doe123uniquemarker"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    # subject appears as its OWN token, bounded by whitespace/punctuation.
    mem.remember(_pair(standalone_id, "case notes",
                        f"Notes about {subject} are attached."),
                 channel="document")
    # subject appears MID-WORD, concatenated with other characters — no
    # word-boundary around just the subject text.
    mem.remember(_pair(embedded_id, "unrelated ticket",
                        f"See ticket prefix{subject}suffix for details."),
                 channel="document")

    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    _execute(env, folder, subject, queue_if_sealed=True)
    seal.unseal_folder(folder, passphrase="pw", log_root=log_root)

    remaining = _chain_pair_ids(folder, log_root)
    assert standalone_id not in remaining, \
        "a standalone-token subject mention must be recalled and erased"
    assert embedded_id in remaining, \
        "DOCUMENTED recall gap: a subject embedded mid-word/mid-sentence " \
        "is NOT recalled by the matcher — this is the known trade-off, " \
        "not a bug to silently fix here"


# ---------------------------------------------------------------------------
# 6b) N-GRAM upgrade: multi-word subjects ("Jane Doe") ARE now recalled —
#     start of text, end of text, multiple occurrences in one pair — and
#     the two negatives an n-gram matcher must get right: non-adjacent
#     tokens are NOT the phrase, and a single shared token is NOT the
#     multi-word subject. This closes the gap the verifier found in the
#     prior full_norm-only matcher (a phrase only matched when it was the
#     ENTIRE per-event haystack, essentially never in realistic prose).
# ---------------------------------------------------------------------------


def test_multiword_subject_ngram_recalled_start_end_and_multiple_occurrences(env, tmp_path):
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)

    subject = "Jane Doe"
    start_id = "sha256:ngram-start"
    end_id = "sha256:ngram-end"
    multi_id = "sha256:ngram-multi"

    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(start_id, "case notes",
                        "Jane Doe called about the case this morning."),
                 channel="document")
    mem.remember(_pair(end_id, "case notes",
                        "Please follow up with Jane Doe"),
                 channel="document")
    mem.remember(_pair(multi_id, "case notes",
                        "Jane Doe emailed early. Later, Jane Doe called again."),
                 channel="document")

    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    report = _execute(env, folder, subject, queue_if_sealed=True)
    assert report.pending_erase_queued == [str(folder.resolve())]
    seal.unseal_folder(folder, passphrase="pw", log_root=log_root)

    remaining = _chain_pair_ids(folder, log_root)
    assert start_id not in remaining, "phrase at the START of the text must be recalled"
    assert end_id not in remaining, "phrase at the END of the text must be recalled"
    assert multi_id not in remaining, \
        "multiple occurrences in one pair still resolve to erasing that pair"


def test_multiword_subject_ngram_does_not_over_erase(env, tmp_path):
    """The two negatives an n-gram matcher must get right: non-adjacent
    tokens are not the phrase, and a single shared token is not the
    multi-word subject — matching on a bare partial token would risk
    erasing an unrelated record that never mentions the actual subject."""
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)

    subject = "Jane Doe"
    non_adjacent_id = "sha256:ngram-non-adjacent"
    partial_id = "sha256:ngram-partial"

    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(non_adjacent_id, "case notes",
                        "Jane will call, and separately Doe will follow up."),
                 channel="document")
    mem.remember(_pair(partial_id, "case notes",
                        "Jane went to the store this afternoon."),
                 channel="document")

    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    _execute(env, folder, subject, queue_if_sealed=True)
    seal.unseal_folder(folder, passphrase="pw", log_root=log_root)

    remaining = _chain_pair_ids(folder, log_root)
    assert non_adjacent_id in remaining, \
        "'Jane ... Doe' with unrelated tokens between them is NOT the " \
        "phrase 'Jane Doe' — a contiguous-window matcher must not erase it"
    assert partial_id in remaining, \
        "a bare 'Jane' with no adjacent 'Doe' is NOT the multi-word " \
        "subject — must not be erased on a shared partial token"


# ---------------------------------------------------------------------------
# 6d) Punctuation-recall fix: a subject entered WITH intra-token/adjacent
#     punctuation ("O'Brien", "Smith, John") must still be recalled from
#     plain prose containing that punctuation-free phrase — the marker's
#     subject_hash is now derived from the SAME tokenised (punctuation-
#     stripped) canonical form the unseal-time matcher re-derives from a
#     haystack, not from the punctuation-kept forgotten-subjects ledger
#     hash. The over-deletion guards from 6b/6c must still hold with a
#     punctuated subject: no bare-partial-token match, no non-adjacent
#     match, no unrelated-record match.
# ---------------------------------------------------------------------------


def test_punctuated_subject_apostrophe_is_recalled(env, tmp_path):
    """"O'Brien" tokenises to ["o", "brien"] (2 tokens) — a 2-token window
    of plain "O'Brien" prose must be recalled, closing the gap where the
    marker's subject_hash was computed over the punctuation-KEPT subject
    while the matcher only ever produces punctuation-stripped windows."""
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)

    subject = "O'Brien"
    hit_id = "sha256:apos-hit"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(hit_id, "case notes",
                        "I spoke with O'Brien yesterday about the matter."),
                 channel="document")

    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    report = _execute(env, folder, subject, queue_if_sealed=True)
    assert report.pending_erase_queued == [str(folder.resolve())]
    seal.unseal_folder(folder, passphrase="pw", log_root=log_root)

    remaining = _chain_pair_ids(folder, log_root)
    assert hit_id not in remaining, \
        "a punctuated subject ('O'Brien') must be recalled from plain " \
        "prose containing the same punctuation-free phrase"


def test_punctuated_subject_comma_is_recalled(env, tmp_path):
    """"Smith, John" tokenises to ["smith", "john"] — a comma-separated
    subject must still recall a plain 2-token "Smith John" mention."""
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)

    subject = "Smith, John"
    hit_id = "sha256:comma-hit"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(hit_id, "intake",
                        "Received a file from Smith, John re: the claim."),
                 channel="document")

    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    _execute(env, folder, subject, queue_if_sealed=True)
    seal.unseal_folder(folder, passphrase="pw", log_root=log_root)

    remaining = _chain_pair_ids(folder, log_root)
    assert hit_id not in remaining, \
        "'Smith, John' must be recalled from prose containing 'Smith John'"


def test_punctuated_subject_does_not_over_erase(env, tmp_path):
    """Punctuation-recall must not loosen the CONTIGUOUS-window requirement:
    a record with only 'Smith' (no 'John'), a record with 'Smith' and
    'John' separated by unrelated tokens, and an unrelated subject entirely
    must all survive."""
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)

    subject = "Smith, John"
    partial_id = "sha256:punct-partial"
    non_adjacent_id = "sha256:punct-non-adjacent"
    unrelated_id = "sha256:punct-unrelated"

    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(partial_id, "unrelated",
                        "Smith called about a different matter entirely."),
                 channel="document")
    mem.remember(_pair(non_adjacent_id, "unrelated",
                        "Smith will follow up, and separately John will too."),
                 channel="document")
    mem.remember(_pair(unrelated_id, "unrelated",
                        "Totally unrelated record about Jane Doe's case."),
                 channel="document")

    seal.seal_folder(folder, passphrase="pw", log_root=log_root)
    _execute(env, folder, subject, queue_if_sealed=True)
    seal.unseal_folder(folder, passphrase="pw", log_root=log_root)

    remaining = _chain_pair_ids(folder, log_root)
    assert partial_id in remaining, \
        "a bare 'Smith' with no adjacent 'John' must not be erased"
    assert non_adjacent_id in remaining, \
        "'Smith ... John' separated by unrelated tokens is NOT the " \
        "contiguous phrase 'Smith John' and must not be erased"
    assert unrelated_id in remaining, \
        "an unrelated subject's record must never be touched"


# ---------------------------------------------------------------------------
# 6c) Marker-spam dedup: a repeated execute() against the same still-sealed
#     blob for the same subject arms exactly ONE ledger entry.
# ---------------------------------------------------------------------------


def test_repeated_execute_dedupes_to_one_marker(env, tmp_path):
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)
    pid = "sha256:dedup-1"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(pid, "Notes about DedupSubjGhi", "DedupSubjGhi's file."),
                 channel="document")
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)

    r1 = _execute(env, folder, "DedupSubjGhi", queue_if_sealed=True, request_id="req-a")
    r2 = _execute(env, folder, "DedupSubjGhi", queue_if_sealed=True, request_id="req-b")
    r3 = _execute(env, folder, "DedupSubjGhi", queue_if_sealed=True)

    ledger = _read_ledger(folder, log_root)
    assert len(ledger) == 1, "three execute() calls against the same sealed blob must arm ONE marker"
    assert (r1.pending_markers[0]["marker_id"]
            == r2.pending_markers[0]["marker_id"]
            == r3.pending_markers[0]["marker_id"])

    result = seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert result["pending_erase_applied"]["purged_pair_count"] == 1
    assert pid not in _chain_pair_ids(folder, log_root)


# ---------------------------------------------------------------------------
# 7) Sealed-DESCENDANT cascade end-to-end
# ---------------------------------------------------------------------------


def test_sealed_descendant_cascade_end_to_end(env, tmp_path):
    log_root = env["log_root"]
    root = tmp_path / "root"; root.mkdir()
    child = root / "child"; child.mkdir()
    add_known_workspace(str(root), log_root=log_root)
    add_known_workspace(str(child), log_root=log_root)

    pid = "sha256:child-jane"
    mem_child = WorkspaceMemory(str(child), log_root=str(log_root), actor="t")
    mem_child.remember(_pair(pid, "Notes about CascadeSubjIjk",
                              "CascadeSubjIjk's file lives in the child folder."),
                        channel="document")

    seal.seal_folder(child, passphrase="pw", log_root=log_root)
    assert seal.is_sealed(child, log_root=log_root)
    assert not seal.is_sealed(root, log_root=log_root)

    # execute against the UNSEALED root, cascading — root itself needs no
    # queue_if_sealed (it isn't sealed); the sealed descendant is ALWAYS
    # queued once discovered, regardless of that flag.
    report = _execute(env, root, "CascadeSubjIjk", cascade=True)
    assert report.pending_erase_queued == [str(child.resolve())]
    assert len(report.pending_markers) == 1
    # root itself was not sealed, so its own composite tombstone still
    # writes normally.
    assert report.composite_tombstone_id != ""

    result = seal.unseal_folder(child, passphrase="pw", log_root=log_root)
    applied = result["pending_erase_applied"]
    assert applied["purged_pair_count"] == 1
    assert pid not in _chain_pair_ids(child, log_root)


# ---------------------------------------------------------------------------
# 8) Operator-only fallback (no controller key) still arms + applies
# ---------------------------------------------------------------------------


def test_operator_only_fallback_arms_and_applies(env_operator_only, tmp_path):
    env = env_operator_only
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)
    assert signing.public_controller_key_fingerprint() is None

    pid = "sha256:l0-jane"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(pid, "Notes about L0SubjWxy", "L0SubjWxy's file."),
                 channel="document")
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)

    report = _execute(env, folder, "L0SubjWxy", queue_if_sealed=True)
    assert report.pending_erase_queued == [str(folder.resolve())]
    marker = report.pending_markers[0]
    assert marker["controller_keyid"] is None   # weaker, L0 tamper-evidence

    ledger = _read_ledger(folder, log_root)
    assert ledger[0]["controller_sig"] == ""
    assert ledger[0]["operator_sig"]   # non-empty — still signed

    result = seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert result["pending_erase_applied"]["purged_pair_count"] == 1
    assert pid not in _chain_pair_ids(folder, log_root)


# ---------------------------------------------------------------------------
# 9) queue_if_sealed on a sealed ROOT queues instead of raising;
#    default still raises SealedWriteError
# ---------------------------------------------------------------------------


def test_default_still_raises_sealed_write_error_on_root(env, tmp_path):
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)
    pid = "sha256:default-jane"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(pid, "Notes about DefaultSubjAbc", "DefaultSubjAbc's file."),
                 channel="document")
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)

    # queue_if_sealed defaults False — even with the feature flag ON, the
    # root's own composite write still raises, exactly as before this
    # feature existed.
    with pytest.raises(SealedWriteError):
        _execute(env, folder, "DefaultSubjAbc")

    assert _read_ledger(folder, log_root) == []
    assert seal.is_sealed(folder, log_root=log_root)


def test_queue_if_sealed_true_queues_instead_of_raising(env, tmp_path):
    log_root = env["log_root"]
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)
    pid = "sha256:queued-jane"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(pid, "Notes about QueuedSubjDef", "QueuedSubjDef's file."),
                 channel="document")
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)

    report = _execute(env, folder, "QueuedSubjDef", queue_if_sealed=True)
    assert report.pending_erase_queued == [str(folder.resolve())]
    assert report.composite_tombstone_id == ""

    result = seal.unseal_folder(folder, passphrase="pw", log_root=log_root)
    assert result["pending_erase_applied"]["purged_pair_count"] == 1
    assert pid not in _chain_pair_ids(folder, log_root)


# ---------------------------------------------------------------------------
# Feature-off: discovery is a no-op, execute() behaves exactly as before.
# ---------------------------------------------------------------------------


def test_feature_flag_off_arms_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.delenv("WORKSPACE_PENDING_ERASE", raising=False)
    signing.ensure_keypair()
    signing.ensure_controller_keypair()
    log_root = tmp_path / "logs"
    folder = tmp_path / "ws"; folder.mkdir()
    add_known_workspace(str(folder), log_root=log_root)
    pid = "sha256:flagoff-jane"
    mem = WorkspaceMemory(str(folder), log_root=str(log_root), actor="t")
    mem.remember(_pair(pid, "Notes about FlagOffSubj", "FlagOffSubj's file."),
                 channel="document")
    seal.seal_folder(folder, passphrase="pw", log_root=log_root)

    with pytest.raises(SealedWriteError):
        erasure.execute(
            str(folder), "FlagOffSubj", legal_basis="art_17_1_a",
            requester_ref="t1", reason="test", log_root=log_root,
            queue_if_sealed=True)   # even with the param True, flag OFF wins
    assert _read_ledger(folder, log_root) == []
