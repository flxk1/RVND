# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-11: chain-format rollback (forward-compat) behaviour, pinned.

Backward migration — a NEW wheel reading an OLD chain — is covered by
server/tests/migration/. The untested direction is ROLLBACK: an OLD wheel
reading a chain a newer wheel wrote (a downgrade / `pip install rvnd==<prev>`
against existing data). The chain has no version stamp and no version floor, so
the current behaviour is: the reader TOLERATES fields and event kinds it does
not know — it does not crash, and it does not refuse a too-new chain — at the
cost of silently losing the semantics of the unknown fields.

This test pins that behaviour so it cannot change silently. It also documents
the tradeoff: tolerance keeps rollback working but means a downgraded reader
runs with the older version's guarantees, not the newer ones (see
deploy/rollback-and-key-lifecycle.md). If a version floor is ever added to
REFUSE a too-new chain instead, this test must be updated deliberately.
"""
from __future__ import annotations

import pytest

from rvnd import mutation_log as ML
from rvnd.mutation_log import LogEvent, MutationLog

pytestmark = pytest.mark.security  # chain-integrity / rollback safety


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    from rvnd import signing
    signing.ensure_keypair()
    ws = tmp_path / "ws"
    ws.mkdir()
    return {"ws": str(ws), "lr": str(tmp_path / "logs")}


def _append_normal(log, ws, pair_id):
    log.append(LogEvent(event="ingest", folder_path=ws, pair_id=pair_id))


class _future_kind:
    """Teach append a future event kind for the duration of the block, then
    restore the real vocabulary — the 'newer wheel writes, older wheel reads'
    simulation. Restores ONLY VALID_EVENTS (not monkeypatch's env setup, whose
    undo would also revert WORKSPACE_KEY_DIR and change the verifying key)."""

    def __init__(self, *kinds):
        self._kinds = set(kinds)

    def __enter__(self):
        self._saved = ML.VALID_EVENTS
        ML.VALID_EVENTS = ML.VALID_EVENTS | self._kinds
        return self

    def __exit__(self, *exc):
        ML.VALID_EVENTS = self._saved
        return False


def test_reader_tolerates_a_future_event_kind(env):
    """A newer wheel writes an event kind this build doesn't know. The
    downgraded reader must still verify the chain — the unknown kind is
    tolerated, its signature and hash link validate normally."""
    log = MutationLog(env["ws"], log_root=env["lr"])
    _append_normal(log, env["ws"], "sha256:pair-1")

    # Simulate the newer wheel: temporarily teach append a future kind so the
    # event is validly signed + hash-linked, exactly as a newer build would
    # have written it.
    future = "future_event_kind_2099"
    with _future_kind(future):
        log.append(LogEvent(
            event=future, folder_path=env["ws"], pair_id="sha256:pair-future",
            extra={"kind": future, "a_field_this_wheel_never_heard_of": 42}))
    # back to this build's real vocabulary — the "old reader"
    assert future not in ML.VALID_EVENTS, "guard: the future kind is now unknown"

    # The downgraded reader verifies the chain that contains the unknown kind.
    result = MutationLog(env["ws"], log_root=env["lr"]).verify_chain()
    assert result.ok, (
        "an older reader hard-failed on a chain containing a newer event kind — "
        f"rollback would brick the chain. broken={result.broken_links[:3]} "
        f"sig={result.signature_failures[:3]}")
    assert not result.signature_failures, (
        "the future event's signature failed under the old reader — the unknown "
        "kind must not affect signature verification")


def test_no_version_floor_refuses_a_too_new_chain(env):
    """Pins the deliberate absence of a version floor: the reader does NOT
    refuse a chain solely because it carries a newer event kind. Tolerance is
    what keeps rollback working; if a floor is added to fail-closed on a
    too-new chain, update this test (and the runbook) on purpose."""
    log = MutationLog(env["ws"], log_root=env["lr"])
    _append_normal(log, env["ws"], "sha256:pair-1")
    future = "validator_rejected_v2_hypothetical"
    with _future_kind(future):
        log.append(LogEvent(event=future, folder_path=env["ws"],
                            pair_id="sha256:pair-2", extra={"kind": future}))

    result = MutationLog(env["ws"], log_root=env["lr"]).verify_chain()
    # No "unknown event kind" / "chain too new" refusal exists today.
    reasons = " ".join(str(b) for b in result.broken_links).lower()
    assert "too new" not in reasons and "unknown event" not in reasons, (
        "a version floor now refuses a too-new chain — intended? then update "
        "this test and deploy/rollback-and-key-lifecycle.md")
    assert result.ok
