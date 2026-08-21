# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for case-insensitive folder_hash (#162).

On macOS APFS, /Users/x/Workspaces and /Users/x/workspaces refer to the same
physical folder. Pre-#162, folder_hash hashed the input string and
produced two different identities. Post-#162, both should hash to the
same value.

These tests are filesystem-aware: they probe the actual filesystem at
the test tmp dir and adapt expectations.
"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from rvnd.mutation_log import (
    folder_hash,
    legacy_folder_hash,
    _filesystem_is_case_insensitive,
)


def test_case_insensitive_filesystem_detection_home():
    """Home directory detection should always return SOMETHING (True/False)
    without raising."""
    result = _filesystem_is_case_insensitive(Path.home())
    assert isinstance(result, bool)


@pytest.mark.skipif(platform.system() not in ("Darwin", "Windows"),
                     reason="Case-insensitive normalisation only happens "
                            "on APFS/NTFS; Linux test boxes are case-sensitive.")
def test_folder_hash_case_insensitive_on_macos_or_windows(tmp_path):
    """When the filesystem is case-insensitive, the two case variants
    of the SAME real folder hash to the same identity."""
    # Create a folder with a specific case
    real_folder = tmp_path / "Workspaces"
    real_folder.mkdir()
    # Access via the OTHER case
    lower_path = tmp_path / "workspaces"

    h_upper = folder_hash(str(real_folder))
    h_lower = folder_hash(str(lower_path))
    assert h_upper == h_lower, (
        f"case-insensitive filesystem should produce same hash; "
        f"upper={h_upper} lower={h_lower}"
    )


def test_folder_hash_normalised_path_lowercase_when_insensitive():
    """The normalised hash should be of the LOWER-CASE absolute path
    when the filesystem is case-insensitive."""
    p = Path.home()
    if not _filesystem_is_case_insensitive(p):
        pytest.skip("filesystem is case-sensitive; no normalisation expected")
    h_actual = folder_hash(str(p))
    # The hash should equal hash of the lower-cased absolute path
    import hashlib
    expected = hashlib.sha256(str(p).lower().encode()).hexdigest()[:32]
    assert h_actual == expected


def test_legacy_folder_hash_preserves_case():
    """The legacy fallback function MUST keep the old case-sensitive
    behaviour so it can read pre-#162 logs."""
    p = Path.home()
    h_legacy = legacy_folder_hash(str(p))
    import hashlib
    expected = hashlib.sha256(str(p).encode()).hexdigest()[:32]
    assert h_legacy == expected


def test_folder_hash_stable_under_relative_path_input():
    """Same physical folder via different input forms should produce same hash."""
    abs_path = str(Path.home())
    rel_path = "~"

    h_abs = folder_hash(abs_path)
    h_rel = folder_hash(rel_path)
    assert h_abs == h_rel, "expanduser + resolve should normalise input forms"


def test_folder_hash_different_folders_different_hashes(tmp_path):
    """Two genuinely different folders must hash differently."""
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    a.mkdir(); b.mkdir()
    assert folder_hash(str(a)) != folder_hash(str(b))


def test_folder_hash_idempotent():
    """Calling folder_hash twice with the same input yields the same hash."""
    p = str(Path.home())
    assert folder_hash(p) == folder_hash(p)


# ---------------------------------------------------------------------------
# The retirement onto loomground-workspace (#…): identity is CONSUMED, and the
# legacy fallback still resolves an old log.
# ---------------------------------------------------------------------------
# ``legacy_folder_hash`` has only three live references, none of them loud, so
# it is the easiest thing in this change to drop by accident. Dropping it does
# not fail: ``seal._resolve_log_dir`` silently stops finding a pre-#162 log
# directory, and ``MutationLog._is_sealed`` silently returns False for a
# workspace sealed under the legacy hash — after which ``__init__`` recreates a
# plaintext log dir beside the ciphertext and ``unseal`` refuses. These pin the
# fallback end-to-end rather than pinning the function in isolation.


def test_identity_functions_are_the_consumed_plane_not_a_local_copy():
    """The three identity functions ARE loomground-workspace's, by object
    identity — not a re-implementation that happens to agree today."""
    import loomground_workspace as lw
    from workspaces import seal
    from workspaces.adapters import workspace as seam

    assert folder_hash is lw.folder_hash
    assert legacy_folder_hash is lw.legacy_folder_hash
    assert _filesystem_is_case_insensitive is lw.identity._filesystem_is_case_insensitive
    # the two live consumers of the legacy hash reach the same object
    assert seal.legacy_folder_hash is lw.legacy_folder_hash
    assert seam.legacy_folder_hash is lw.legacy_folder_hash


def _legacy_differs(folder) -> bool:
    """True when the two hashes actually differ for this path — i.e. the
    fallback is exercisable here. They coincide on case-sensitive filesystems
    (Linux ext4/xfs), where a pre-#162 log lives under the same name and the
    fallback is a no-op by construction."""
    return folder_hash(folder) != legacy_folder_hash(folder)


def test_legacy_hashed_log_dir_still_resolves(tmp_path):
    """A log written under the PRE-#162 hash is still found.

    This is the data-availability property: the fresh-hash directory does not
    exist, only the legacy one does, and ``seal._resolve_log_dir`` must return
    the legacy directory — otherwise a seal/unseal operates on an empty path
    while the real history sits under the other name.
    """
    from workspaces.seal import _resolve_log_dir

    folder = tmp_path / "Acme"          # mixed case: the whole point of #162
    folder.mkdir()
    if not _legacy_differs(folder):
        pytest.skip("case-sensitive filesystem: the two hashes coincide, so "
                    "there is no legacy directory to fall back to")

    root = tmp_path / "logroot"
    legacy_dir = root / legacy_folder_hash(folder)
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "events.jsonl").write_text('{"event":"pre-162"}\n',
                                             encoding="utf-8")

    assert not (root / folder_hash(folder)).exists()
    resolved = _resolve_log_dir(folder, root)
    assert resolved == legacy_dir, (
        "the legacy fallback stopped resolving a pre-#162 log directory — "
        "history is now invisible, silently")
    assert (resolved / "events.jsonl").read_text(encoding="utf-8").strip() == \
        '{"event":"pre-162"}'


def test_workspace_sealed_under_the_legacy_hash_is_still_detected(tmp_path,
                                                                  monkeypatch):
    """``_is_sealed`` must see a ``<legacy_folder_hash>.sealed`` blob.

    If it does not, ``MutationLog.__init__`` recreates a plaintext log
    directory beside the ciphertext, which by its own comment makes ``unseal``
    refuse — a data-availability failure on encrypted operator data.
    """
    from workspaces.mutation_log import MutationLog

    folder = tmp_path / "Acme"
    folder.mkdir()
    if not _legacy_differs(folder):
        pytest.skip("case-sensitive filesystem: the two hashes coincide")

    root = tmp_path / "logroot"
    root.mkdir()
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    (root / (legacy_folder_hash(folder) + ".sealed")).write_bytes(b"ciphertext")

    log = MutationLog(folder, log_root=root)
    assert log._is_sealed() is True, (
        "a workspace sealed under the legacy hash is no longer detected as "
        "sealed; __init__ will recreate plaintext beside the ciphertext")
    # and the guard did its job: no plaintext store dir beside the blob
    assert not (root / folder_hash(folder)).exists()
