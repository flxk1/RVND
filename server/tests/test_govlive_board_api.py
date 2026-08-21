# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""govlive read-only board API (I3) — the §1 governance_live board published as a
stable, versioned HTTP/JSON contract on serve.py.

Proven against the real server (boots serve.make_server on a thread, real HTTP),
no mocks:

  * ONE CONTRACT: GET /govlive/board returns the SAME board dict the MCP
    governance_live op returns and the panel renders — no privileged view.
  * VERSIONED: the contract version rides the X-Govlive-Contract header, and the
    body is exactly the MCP board (the version is NOT in the body).
  * EGRESS-GOVERNED (the load-bearing negatives): a tokenless caller, an
    unauthorized consumer (trust mode, no principal), an unregistered folder, and
    a '..' path are each REFUSED with ZERO board data; a sealed folder reveals no
    plaintext chain.
  * READ-ONLY: there is NO POST route to the path (a mutation attempt -> 404),
    and a read cycle leaves the chain tip + registry byte-identical.

The board API shares the exact _govlive_read gate with the I2 stream, so this
also guards that the two egress surfaces cannot drift.
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

_TOKEN = "govlive-board-test-token"


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
    # Registered-workspaces-only egress (unconditional containment).
    _registry.add_known_workspace(str(folder), log_root=tmp_path / "log")
    srv = serve.make_server(port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"port": port, "folder": str(folder), "log": str(tmp_path / "log"),
           "mp": monkeypatch}
    srv.shutdown()


def _board(port, folder, token=_TOKEN, headers=None):
    """GET /govlive/board; return (status, board_dict_or_None, contract_header)."""
    h = dict(headers or {})
    if token is not None:
        h["X-Workspaces-Token"] = token
    url = f"http://127.0.0.1:{port}/govlive/board?folder={folder}"
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return (r.status, json.loads(r.read().decode()),
                    r.headers.get("X-Govlive-Contract"))
    except urllib.error.HTTPError as e:
        return e.code, None, None


def test_board_is_the_governance_live_projection(env):
    # ONE CONTRACT: the API returns the same board dict the MCP op returns.
    status, board, _ = _board(env["port"], env["folder"])
    assert status == 200
    reference = governance_live(env["folder"], log_root=env["log"], chain_limit=100)
    assert board == reference                       # no privileged view, field-for-field


def test_contract_version_in_header_not_body(env):
    status, board, contract = _board(env["port"], env["folder"])
    assert status == 200
    assert contract == serve.GOVLIVE_CONTRACT_VERSION   # versioned via header
    assert "contract_version" not in board              # body stays the MCP board


def test_missing_token_refused(env):
    status, board, _ = _board(env["port"], env["folder"], token=None)
    assert status == 403 and board is None              # zero board data


def test_unauthorized_principal_refused(env):
    # Trust mode ON (read live per request) + no principal header -> refused.
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    status, board, _ = _board(env["port"], env["folder"])
    assert status == 403 and board is None              # unauthorized consumer, zero data


def test_sealed_folder_reveals_no_chain(env):
    _seal.seal_folder(env["folder"], passphrase="pw-123", log_root=Path(env["log"]))
    assert _seal.is_sealed(env["folder"], log_root=Path(env["log"]))
    status, board, _ = _board(env["port"], env["folder"])
    assert status == 200 and board["chain"] == []       # sealed -> no plaintext steps


def test_traversal_folder_refused(env):
    for bad in ("../etc/passwd", "/a/../b"):
        status, board, _ = _board(env["port"], bad)
        assert status == 400 and board is None, bad


def test_unregistered_folder_refused(env):
    # Unconditional containment: a clean but UNREGISTERED folder is refused.
    for bad in ("/tmp/not-a-registered-workspace", "/var/empty/nope"):
        status, board, _ = _board(env["port"], bad)
        assert status == 403 and board is None, bad


def test_no_write_route_mutation_refused(env):
    # READ-ONLY: no POST route to the board path -> a mutation attempt is refused.
    req = urllib.request.Request(
        f"http://127.0.0.1:{env['port']}/govlive/board?folder={env['folder']}",
        data=b"{}", headers={"X-Workspaces-Token": _TOKEN,
                             "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 404                                  # no write route to the API


def test_zero_mutation_across_reads(env):
    before_n = MutationLog(env["folder"], log_root=Path(env["log"])).count()
    before_reg = _registry.load_registry(log_root=Path(env["log"]))
    _board(env["port"], env["folder"])
    _board(env["port"], env["folder"])                  # a second read
    assert MutationLog(env["folder"], log_root=Path(env["log"])).count() == before_n
    assert _registry.load_registry(log_root=Path(env["log"])) == before_reg
