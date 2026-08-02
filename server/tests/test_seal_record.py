# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Transparent per-record encryption — data-in-use hardening (raw-file reads see ciphertext).

  R1  round-trips (encrypt → decrypt) and the on-disk bytes are NOT the plaintext;
  R2  a wrong passphrase fails (authenticated) — no silent garbage;
  R3  a record is folder-bound (AAD) — it can't be replayed into another folder.
"""
from __future__ import annotations

import os

import pytest

from workspaces import seal

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

PLAIN = b"controller: acme; special-category data: health records for 12,000 patients"


def test_round_trip_and_ciphertext_opaque():                           # R1
    blob = seal.encrypt_record(PLAIN, passphrase="correct horse", folder="/tmp/rvnd-folder-a")
    assert PLAIN not in blob                       # a raw read sees ciphertext, not content
    assert blob[:5] == b"RVEC1"
    assert seal.decrypt_record(blob, passphrase="correct horse", folder="/tmp/rvnd-folder-a") == PLAIN


def test_wrong_passphrase_fails():                                     # R2
    blob = seal.encrypt_record(PLAIN, passphrase="right", folder="/tmp/rvnd-folder-a")
    with pytest.raises(seal.SealError):
        seal.decrypt_record(blob, passphrase="wrong", folder="/tmp/rvnd-folder-a")


def test_folder_bound_no_replay():                                     # R3
    blob = seal.encrypt_record(PLAIN, passphrase="right", folder="/tmp/rvnd-folder-a")
    # same passphrase, different folder → the AAD authentication fails (can't move a record)
    with pytest.raises(seal.SealError):
        seal.decrypt_record(blob, passphrase="right", folder="/tmp/rvnd-folder-b")
