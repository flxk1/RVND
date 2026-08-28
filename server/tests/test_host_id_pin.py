# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""host_id pinning — regression tests for issue #122.

Before the pin, ``_host_id()`` derived ``sha256(hostname|machine_id)[:12]`` directly,
so it changed whenever the OS hostname tracked the network (macOS with ``HostName``
unset). A chain signed under one hostname then failed verification after the machine
moved networks. host_id must now be STABLE across hostname changes, overridable via
``WORKSPACE_HOST_ID``, and persisted at ``<key-root>/host-id``.
"""
from __future__ import annotations

import pytest

from rvnd import signing


@pytest.fixture
def isolated_key_root(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path))
    monkeypatch.delenv(signing.HOST_ID_ENV, raising=False)
    return tmp_path


def test_host_id_stable_across_hostname_change(isolated_key_root, monkeypatch):
    monkeypatch.setattr(signing.socket, "gethostname", lambda: "mac.netA")
    first = signing._host_id()
    assert (isolated_key_root / "host-id").is_file(), "host_id was not persisted"

    # machine moves networks -> hostname changes -> host_id MUST NOT change (the #122 fix)
    monkeypatch.setattr(signing.socket, "gethostname", lambda: "mac.netB")
    assert signing._host_id() == first


def test_env_override_wins(isolated_key_root, monkeypatch):
    monkeypatch.setenv(signing.HOST_ID_ENV, "945ddb2e7302")
    monkeypatch.setattr(signing.socket, "gethostname", lambda: "anything")
    assert signing._host_id() == "945ddb2e7302"


def test_existing_pin_read_verbatim(isolated_key_root, monkeypatch):
    (isolated_key_root / "host-id").write_text("deadbeef0001\n")
    # even a different live hostname does not override an existing pin
    monkeypatch.setattr(signing.socket, "gethostname", lambda: "mac.netC")
    assert signing._host_id() == "deadbeef0001"


def test_derivation_matches_legacy_when_unpinned(isolated_key_root, monkeypatch):
    import hashlib
    monkeypatch.setattr(signing.socket, "gethostname", lambda: "host.example")
    monkeypatch.setattr(signing, "_machine_id", lambda: "MID-123")
    expected = hashlib.sha256(b"host.example|MID-123").hexdigest()[:12]
    assert signing._host_id() == expected  # first derivation is unchanged; only persistence is new
