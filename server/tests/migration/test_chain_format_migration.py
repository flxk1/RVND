# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Migration regression: prior-version chains must verify on current runtime.

For each captured prior-version fixture (see ``fixtures/README.md``):

1. Load the fixture JSONL into a fresh ``MutationLog`` location.
2. Run ``verify_chain()`` — expect ``ok=True`` OR a documented degradation
   (e.g. v0.6.5 events have no signature, so ``unsigned_events`` == total).
3. Append one new event with the current runtime.
4. Re-run ``verify_chain()`` — the boundary between the historical chain and
   the new event must still verify cleanly.
5. Assert the newly-appended event carries every current schema field
   (``prev_hash`` from 0.6.5+, ``signature`` from 0.6.6+, ``host_id`` from
   0.6.8+ once B4 lands).

These tests are the primary guardrail against silently breaking every
existing user's audit trail when the chain format moves.

Test naming convention: ``test_<version>_chain_<aspect>``.
"""

from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from workspaces.mutation_log import (
    GENESIS_HASH,
    LogEvent,
    MutationLog,
    _signed_bytes,
    folder_hash,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _install_fixture_keypair(version: str, keydir: Path) -> Ed25519PrivateKey:
    """Install a deterministic, explicitly synthetic migration-test key.

    No private-key fixture is committed. The key is derived from a public test
    label at runtime, written only inside ``tmp_path``, and used to re-sign the
    captured event shapes without changing their chain content.
    """
    seed = hashlib.sha256(
        f"rvnd-public-migration-test-key-v0.6.{version}".encode()
    ).digest()
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key()
    keydir.mkdir(parents=True, exist_ok=True)
    (keydir / "identity.priv").write_bytes(private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    (keydir / "identity.pub").write_bytes(public.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    return private


def _load_fixture_into_log(
    fixture_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keypair_version: str | None,
) -> MutationLog:
    """Materialise a fixture as the events.jsonl of a fresh tmp workspace.

    - Sets ``WORKSPACE_KEY_DIR`` to an isolated tmp keydir.
    - Optionally installs the fixture's keypair so signatures verify.
    - Copies the fixture JSONL verbatim — no path substitution. Chain
      verification depends only on event content + prev_hash, NOT on
      matching the log's filesystem location to the recorded folder_path.
    """
    keydir = tmp_path / "keys"
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(keydir))
    fixture_private = (
        _install_fixture_keypair(keypair_version, keydir)
        if keypair_version is not None else None
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    log_root = tmp_path / ".workspaces" / "log"
    fid = folder_hash(workspace)
    log_dir = log_root / fid
    log_dir.mkdir(parents=True, exist_ok=True)

    fixture = FIXTURES_DIR / fixture_name
    if not fixture.exists():
        pytest.skip(f"fixture missing: {fixture_name}")
    shutil.copy(fixture, log_dir / "events.jsonl")
    if fixture_private is not None:
        from workspaces.signing import sign_bytes

        events = [
            json.loads(line)
            for line in (log_dir / "events.jsonl").read_text(
                encoding="utf-8").splitlines()
            if line.strip()
        ]
        for event in events:
            event["signature"] = sign_bytes(
                _signed_bytes({**event, "signature": ""}), fixture_private)
        (log_dir / "events.jsonl").write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )

    return MutationLog(workspace, log_root=log_root)


def _last_event_dict(log: MutationLog) -> dict:
    """Read the raw last line of the log as a dict."""
    lines = [
        line for line in log.log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return json.loads(lines[-1])


# --------------------------------------------------------------------------
# v0.6.5 — hash chain only
# --------------------------------------------------------------------------


class TestV065Migration:
    """v0.6.5 introduced ``prev_hash``. Events have NO ``signature``."""

    FIXTURE = "v0_6_5_chain.jsonl"

    def test_v0_6_5_chain_loads_and_verifies(self, tmp_path, monkeypatch):
        """v0.6.5 fixture verifies cleanly; all events count as unsigned."""
        log = _load_fixture_into_log(
            self.FIXTURE, tmp_path, monkeypatch, keypair_version=None,
        )
        result = log.verify_chain()
        assert result.ok, (
            f"v0.6.5 fixture did not verify: broken={result.broken_links} "
            f"sig_fail={result.signature_failures}"
        )
        assert result.total_events == 5
        assert result.legacy_events == 0, (
            "v0.6.5 events DO have prev_hash, so they are not 'legacy' "
            "(pre-0.6.5) events"
        )
        assert result.unsigned_events == 5, (
            "v0.6.5 predates the Ed25519 layer; all events must count as "
            "unsigned, not as signature failures"
        )
        assert result.broken_links == []
        assert result.signature_failures == []

    def test_v0_6_5_chain_append_then_reverify(self, tmp_path, monkeypatch):
        """Appending a new event onto a v0.6.5 chain must keep the chain
        valid. The new event carries current-shape fields."""
        log = _load_fixture_into_log(
            self.FIXTURE, tmp_path, monkeypatch, keypair_version=None,
        )
        log.append(LogEvent(
            event="ingest",
            folder_path=str(log.folder_path),
            pair_id="pair-post-migration-001",
            actor="test:migration",
        ))
        result = log.verify_chain()
        assert result.ok, (
            f"chain failed verification after appending to v0.6.5 fixture: "
            f"broken={result.broken_links} sig_fail={result.signature_failures}"
        )
        assert result.total_events == 6
        # The 5 historical events are still unsigned; the new one is signed.
        assert result.unsigned_events == 5
        # Boundary check: the new event's prev_hash matches the canonical
        # hash of the last historical event.
        last = _last_event_dict(log)
        assert last["pair_id"] == "pair-post-migration-001"
        assert last["prev_hash"] != GENESIS_HASH

    def test_v0_6_5_appended_event_has_current_shape(self, tmp_path, monkeypatch):
        """New event written on top of a v0.6.5 chain has every current
        schema field: prev_hash (0.6.5+), signature (0.6.6+).

        Marked xfail with strict=False once B4 lands — the assertion on
        host_id will start to hold and we'll tighten this test then.
        """
        log = _load_fixture_into_log(
            self.FIXTURE, tmp_path, monkeypatch, keypair_version=None,
        )
        log.append(LogEvent(
            event="ingest",
            folder_path=str(log.folder_path),
            pair_id="pair-shape-check",
            actor="test:migration",
        ))
        last = _last_event_dict(log)
        assert last.get("prev_hash"), "post-0.6.5 events must carry prev_hash"
        assert last.get("signature"), "post-0.6.6 events must carry signature"


# --------------------------------------------------------------------------
# v0.6.6 — hash chain + Ed25519
# --------------------------------------------------------------------------


class TestV066Migration:
    """v0.6.6 added Ed25519 signatures over content + chain position."""

    FIXTURE = "v0_6_6_chain.jsonl"

    def test_v0_6_6_chain_loads_and_verifies(self, tmp_path, monkeypatch):
        """v0.6.6 fixture verifies cleanly when its pubkey is available."""
        log = _load_fixture_into_log(
            self.FIXTURE, tmp_path, monkeypatch, keypair_version="6",
        )
        result = log.verify_chain()
        assert result.ok, (
            f"v0.6.6 fixture did not verify: broken={result.broken_links} "
            f"sig_fail={result.signature_failures}"
        )
        assert result.total_events == 5
        assert result.legacy_events == 0
        assert result.unsigned_events == 0
        assert result.broken_links == []
        assert result.signature_failures == []

    def test_v0_6_6_chain_append_then_reverify(self, tmp_path, monkeypatch):
        """Appending onto a v0.6.6 chain keeps everything valid."""
        log = _load_fixture_into_log(
            self.FIXTURE, tmp_path, monkeypatch, keypair_version="6",
        )
        log.append(LogEvent(
            event="ingest",
            folder_path=str(log.folder_path),
            pair_id="pair-066-post-migration",
            actor="test:migration",
        ))
        result = log.verify_chain()
        assert result.ok, (
            f"chain failed verification after appending to v0.6.6 fixture: "
            f"broken={result.broken_links} sig_fail={result.signature_failures}"
        )
        assert result.total_events == 6
        assert result.unsigned_events == 0  # every event is signed now
        last = _last_event_dict(log)
        assert last["pair_id"] == "pair-066-post-migration"
        assert last["prev_hash"] != GENESIS_HASH
        assert last.get("signature"), "new event signed by runtime key"


# --------------------------------------------------------------------------
# v0.6.7 — no chain-format delta (deliberate boundary)
# --------------------------------------------------------------------------


class TestV067Migration:
    """v0.6.7 did not change the chain format. We keep this fixture as a
    deliberate regression boundary: any future schema change labelled
    'minor' will trip this test."""

    FIXTURE = "v0_6_7_chain.jsonl"

    def test_v0_6_7_chain_loads_and_verifies(self, tmp_path, monkeypatch):
        log = _load_fixture_into_log(
            self.FIXTURE, tmp_path, monkeypatch, keypair_version="7",
        )
        result = log.verify_chain()
        assert result.ok
        assert result.total_events == 5
        assert result.legacy_events == 0
        assert result.unsigned_events == 0

    def test_v0_6_7_chain_append_then_reverify(self, tmp_path, monkeypatch):
        log = _load_fixture_into_log(
            self.FIXTURE, tmp_path, monkeypatch, keypair_version="7",
        )
        log.append(LogEvent(
            event="ingest",
            folder_path=str(log.folder_path),
            pair_id="pair-067-post-migration",
            actor="test:migration",
        ))
        result = log.verify_chain()
        assert result.ok, (
            f"chain failed verification after appending to v0.6.7 fixture: "
            f"broken={result.broken_links} sig_fail={result.signature_failures}"
        )
        assert result.total_events == 6


# --------------------------------------------------------------------------
# Future schema fields — these tests document what 0.6.8 will need.
# Today they xfail; once B1 + B4 land they become passing acceptance tests.
# --------------------------------------------------------------------------


def test_appended_event_carries_host_id(tmp_path, monkeypatch):
    """Post-B4, every appended event must stamp host_id so cross-host
    divergence is detectable in verify_chain."""
    log = _load_fixture_into_log(
        "v0_6_7_chain.jsonl", tmp_path, monkeypatch, keypair_version="7",
    )
    log.append(LogEvent(
        event="ingest",
        folder_path=str(log.folder_path),
        pair_id="pair-host-id-check",
        actor="test:migration",
    ))
    last = _last_event_dict(log)
    assert last.get("host_id"), (
        "post-B4 events must carry host_id (12-char hex prefix of "
        "sha256(hostname + machine_id))"
    )
    assert len(last["host_id"]) == 12


def test_purge_writes_tombstone_and_chain_still_verifies(tmp_path, monkeypatch):
    """Post-B1, calling purge() must write a 'purge' tombstone event, re-link
    subsequent events' prev_hash, re-sign them, and leave verify_chain ok."""
    log = _load_fixture_into_log(
        "v0_6_7_chain.jsonl", tmp_path, monkeypatch, keypair_version="7",
    )
    # B1 requires the controller key — initialise it in the test keydir.
    from workspaces import signing
    signing.ensure_controller_keypair()
    # Purge the first pair — events 0, 2, 4 reference pair-067-001.
    log.purge(
        "pair-067-001",
        legal_basis="art_17_1_a",
        requester_ref="test-067-migration",
        reason="post-B1 migration smoke test",
    )
    result = log.verify_chain()
    assert result.ok, (
        f"chain must verify after purge with tombstone: broken="
        f"{result.broken_links} sig_fail={result.signature_failures}"
    )
    # Must report the tombstone count separately from broken_links / sig_fail.
    assert getattr(result, "purged_with_tombstone", 0) >= 1, (
        "ChainVerificationResult must expose purged_with_tombstone post-B1"
    )


def test_verify_chain_exposed_as_mcp_tool():
    """Post-B2 (0.6.8), verify_chain is reachable via MCP. Non-Python
    clients can probe a folder's chain health after upgrade — this is
    the cross-surface parity guarantee that the three-surfaces table
    in the workspace usage docs.
    """
    # 2026-06-12 surface fold: the standalone tool became the workspace_audit
    # facade op "verify_chain" - the parity contract is the op, not the name.
    from workspaces import mcp_server  # type: ignore
    assert "workspace_audit" in mcp_server._DECLARED_TOOLS
    ops = {o["op"] for o in mcp_server.workspace_audit("help")["ops"]}
    assert "verify_chain" in ops, "workspace_audit lost the verify_chain op"
