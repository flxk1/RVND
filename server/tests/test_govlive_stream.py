# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""govlive live step-stream (I2) — read-only chain-tail long-poll on serve.py.

The stream is a DATA EGRESS of the govlive board's chain[]. Proven here against
the real server (boots serve.make_server on a thread, real HTTP), no mocks:

  * emits the seeded governed chain steps in arrival order;
  * ONE-CONTRACT parity: each streamed entry is byte-identical to the §1 board
    chain[] entry (a drift guard, even though the endpoint reuses the
    governance_live projection verbatim);
  * ?since advances a contiguous cursor;
  * SEAL-RESPECTING / egress-governed (the load-bearing negative): a sealed
    folder streams NOTHING, and an unauthorized consumer (trust mode, no
    principal) or a tokenless caller is REFUSED with zero bytes;
  * READ-ONLY / ZERO-MUTATION: a subscribe+emit cycle leaves the chain tip and
    registry byte-identical, and the path has no write route.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "app"))
import serve  # noqa: E402

from rvnd import seal as _seal  # noqa: E402
from rvnd import workspace_registry as _registry  # noqa: E402
from rvnd.governance_live import governance_live  # noqa: E402
from rvnd.mutation_log import LogEvent, MutationLog  # noqa: E402

_TOKEN = "govlive-stream-test-token"


def _seed(folder: str, log: str, n: int = 3) -> None:
    mlog = MutationLog(folder, log_root=Path(log))
    for i in range(n):
        mlog.append(LogEvent(
            event="ingest", folder_path=folder, pair_id=f"sha256:step-{i}",
            actor=f"agent-{i}", extra={"kind": "GovernedStep", "i": i}))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("RVND_BRIDGE_TOKEN", _TOKEN)
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    folder = tmp_path / "ws"
    folder.mkdir()
    _seed(str(folder), str(tmp_path / "log"))
    # The stream is a registered-workspaces-only egress (unconditional
    # containment): register the folder so it resolves to a trusted path.
    _registry.add_known_workspace(str(folder), log_root=tmp_path / "log")
    srv = serve.make_server(port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"port": port, "folder": str(folder), "log": str(tmp_path / "log"),
           "mp": monkeypatch}
    srv.shutdown()


def _stream(port, folder, since=-1, token=_TOKEN, headers=None):
    """GET the long-poll; return (status, events, tip)."""
    h = dict(headers or {})
    if token is not None:
        h["X-Workspaces-Token"] = token
    url = f"http://127.0.0.1:{port}/govlive/stream?folder={folder}&since={since}"
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            tip = r.headers.get("X-Govlive-Tip")
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, [], None
    events = [json.loads(ln[len("data: "):]) for ln in body.splitlines()
              if ln.startswith("data: ")]
    return status, events, tip


def test_stream_emits_seeded_steps(env):
    status, events, tip = _stream(env["port"], env["folder"])
    assert status == 200
    assert [e["seq"] for e in events] == [0, 1, 2]         # arrival order, contiguous
    for e in events:
        assert set(e) == {"seq", "actor", "event", "extra", "hash", "prev_hash"}
    assert tip == "2"


def test_one_contract_parity_with_board(env):
    _, events, _ = _stream(env["port"], env["folder"])
    board = governance_live(env["folder"], log_root=env["log"])["chain"]
    by_seq = {e["seq"]: e for e in board}
    assert events, "expected streamed entries to compare"
    for e in events:                                       # field-for-field parity
        assert e == by_seq[e["seq"]]


def test_since_advances_contiguously(env):
    _, events, tip = _stream(env["port"], env["folder"], since=0)
    assert [e["seq"] for e in events] == [1, 2]            # only newer than seq 0
    _, none_new, tip2 = _stream(env["port"], env["folder"], since=int(tip))
    assert none_new == [] and tip2 == tip                  # nothing past the tip


def test_missing_token_refused(env):
    status, events, _ = _stream(env["port"], env["folder"], token=None)
    assert status == 403 and events == []                  # no bytes without the token


def test_unauthorized_principal_refused(env):
    # Trust mode ON (read live per request) + no principal header → refused.
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    status, events, _ = _stream(env["port"], env["folder"])
    assert status == 403 and events == []                  # unauthorized consumer, zero bytes


def test_sealed_folder_streams_nothing(env):
    _seal.seal_folder(env["folder"], passphrase="pw-123", log_root=Path(env["log"]))
    assert _seal.is_sealed(env["folder"], log_root=Path(env["log"]))
    status, events, _ = _stream(env["port"], env["folder"])
    assert status == 200 and events == []                  # sealed → no plaintext steps stream


def test_zero_mutation_across_subscribe_cycle(env):
    before_n = MutationLog(env["folder"], log_root=Path(env["log"])).count()
    before_reg = _registry.load_registry(log_root=Path(env["log"]))
    _stream(env["port"], env["folder"])                    # subscribe + emit
    _stream(env["port"], env["folder"], since=0)           # a second poll
    assert MutationLog(env["folder"], log_root=Path(env["log"])).count() == before_n
    assert _registry.load_registry(log_root=Path(env["log"])) == before_reg


def test_traversal_folder_refused(env):
    # Layered input hygiene: a '..' path is rejected outright — zero bytes, and
    # the tainted string never reaches path resolution.
    for bad in ("../etc/passwd", "/a/../b"):
        status, events, _ = _stream(env["port"], bad)
        assert status == 400 and events == [], bad


def test_unregistered_folder_refused(env):
    # Unconditional containment: a clean but UNREGISTERED folder is refused —
    # the stream serves registered workspaces only.
    for bad in ("/tmp/not-a-registered-workspace", "/var/empty/nope"):
        status, events, _ = _stream(env["port"], bad)
        assert status == 403 and events == [], bad


def test_no_write_route_on_the_stream(env):
    req = urllib.request.Request(
        f"http://127.0.0.1:{env['port']}/govlive/stream?folder={env['folder']}",
        data=b"{}", headers={"X-Workspaces-Token": _TOKEN,
                             "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 404                                     # no POST handler for this path
