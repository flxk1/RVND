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
