# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""govlive act route (I5) — interact through the GOVERNED surface, never the monitor.

The load-bearing claim: acting on a reserved/red step FROM the monitor is itself
a GOVERNED act, never a bypass. POST /govlive/act hands the intent to the SAME
governance facade the CLI uses (``approval_decide``); the competence-matched
quorum (``resolve_approval``) decides whether the vote COUNTS, and MutationLog
signs the ``ApprovalDecision`` onto the chain. Proven against the real server +
real modules (boots serve.make_server on a thread, no enforcement mocks):

  * T-i5-govern:      a competence-matched quorum clears a reserved step THROUGH
                      the route -> resolve grants -> verify_chain green.
  * T-i5-refuse:      an unauthorized caller (no token / no principal) is REFUSED
                      at the gate with ZERO chain mutation; an under-competent
                      hand's vote is recorded but does NOT count (stays pending).
  * T-i5-wrongkey:    an ApprovalDecision signed by a FOREIGN key fails verify_chain.
  * T-i5-readonly:    the board path has NO write route (POST -> 404); the act
                      route dispatches ONLY the governed-interaction allowlist
                      (a read/other op -> 403), and a refused act mutates nothing.
  * T-i5-onecontract: after the governed clear, the read-only board chain[] shows
                      EXACTLY the signed ApprovalDecision entries the gated path
                      recorded (the monitor has no privileged view).
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "app"))
import serve  # noqa: E402

from workspaces import signing  # noqa: E402
from workspaces import workspace_registry as _registry  # noqa: E402
from workspaces.approvals import (  # noqa: E402
    request_from_reservation, resolve_approval)
from workspaces.governance_live import governance_live  # noqa: E402
from workspaces.mutation_log import (  # noqa: E402
    MutationLog, _canonical_event_hash, _signed_bytes)
from workspaces.parties import register_party  # noqa: E402

_TOKEN = "govlive-act-test-token"
_PRINCIPAL_HEADER = "X-Auth-Request-Email"
# A 2-of-{legal,finance,risk} clearance: lara/finn count, mara (marketing) does not.
_QUORUM = {"kind": "reserved-task", "by": "2 of { legal, finance, risk }"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    log = tmp_path / "log"
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(log))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("RVND_BRIDGE_TOKEN", _TOKEN)
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    # Trust mode: the acting party rides the principal header (the monitor's
    # signed-in user), exactly as a fronting identity proxy would set it.
    monkeypatch.setenv("WORKSPACE_PRINCIPAL_HEADER", _PRINCIPAL_HEADER)
    folder = tmp_path / "ws"
    folder.mkdir()
    f = str(folder)
    # The agent that raised the reservation + a competence-matched human board.
    register_party(f, "agent-A", "agent", grade="L2", log_root=log)
    for pid, comp in (("lara", ["legal"]), ("finn", ["finance"]),
                      ("rita", ["risk"]), ("mara", ["marketing"])):
        register_party(f, pid, "human", name=pid.title(),
                       competences=comp, log_root=log)
    # Registered-workspaces-only egress (unconditional containment).
    _registry.add_known_workspace(f, log_root=log)
    srv = serve.make_server(port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"port": port, "folder": f, "log": str(log)}
    srv.shutdown()


def _open_clearance(env, rid="clear-1"):
    """The reserved step pre-exists — the governed run raised it; the monitor
    CLEARS it. Opened directly (not via the route), like T-own's fixture."""
    return request_from_reservation(
        env["folder"], rid, _QUORUM, requester="agent-A",
        now=time.time(), log_root=env["log"])


def _act(port, folder, *, op="approval_decide", params=None, principal=None,
         token=_TOKEN):
    """POST /govlive/act; return (status, body_dict_or_None)."""
    h = {"Content-Type": "application/json"}
    if token is not None:
        h["X-Workspaces-Token"] = token
    if principal is not None:
        h[_PRINCIPAL_HEADER] = principal
    body = json.dumps({"folder": folder, "op": op,
                       "params": params or {}}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/govlive/act", data=body, headers=h,
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None


def _board(port, folder, principal="agent-A"):
    """GET the read-only board (I3) under a resolved principal."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/govlive/board?folder={folder}",
        headers={"X-Workspaces-Token": _TOKEN, _PRINCIPAL_HEADER: principal})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _approval_events(folder, log):
    return [e for e in MutationLog(folder, log_root=Path(log)).replay()
            if str(e.pair_id).startswith("approval:")]


# --- T-i5-govern -----------------------------------------------------------
def test_competence_matched_quorum_clears_from_the_monitor(env):
    f, log = env["folder"], env["log"]
    _open_clearance(env)
    # First competent hand alone does NOT clear (quorum needs two).
    s1, b1 = _act(env["port"], f,
                  params={"request_id": "clear-1", "decision": "approve"},
                  principal="lara")                                     # legal ✓
    assert s1 == 200 and b1.get("ok") is True
    # Operator-honest outcome: lara's vote COUNTED, but the quorum is not met —
    # the response says exactly that, not a bare ok that reads as "cleared".
    assert b1["counted"] is True and b1["state"] == "pending"
    assert resolve_approval(f, "clear-1", now=time.time() + 60,
                            log_root=log)["state"] == "pending"
    # Second competent hand tips the competence-matched quorum -> granted.
    s2, b2 = _act(env["port"], f,
                  params={"request_id": "clear-1", "decision": "approve"},
                  principal="finn")                                     # finance ✓
    assert s2 == 200 and b2.get("ok") is True
    assert b2["counted"] is True and b2["state"] == "granted"           # honest: cleared
    r = resolve_approval(f, "clear-1", now=time.time() + 60, log_root=log)
    assert r["state"] == "granted"
    assert set(r["approvers"]) == {"lara", "finn"}
    # Signed, not asserted: the whole clearing trail verifies.
    assert MutationLog(f, log_root=Path(log)).verify_chain().ok is True


# --- T-i5-refuse (unauthorized) --------------------------------------------
def test_unauthorized_act_refused_with_zero_mutation(env):
    f, log = env["folder"], env["log"]
    _open_clearance(env)
    before = len(_approval_events(f, log))
    # (a) no session token -> refused at the gate (op is valid, so this is the
    # token gate genuinely firing, not the allowlist).
    s_notok, _ = _act(env["port"], f, token=None, principal="lara",
                      params={"request_id": "clear-1", "decision": "approve"})
    assert s_notok == 403
    # (b) trust declared but NO principal header -> refused at the gate.
    s_noprin, _ = _act(env["port"], f, principal=None,
                       params={"request_id": "clear-1", "decision": "approve"})
    assert s_noprin == 403
    # Neither refused attempt wrote anything to the chain.
    assert len(_approval_events(f, log)) == before


# --- T-i5-refuse (under-competent) -----------------------------------------
def test_under_competent_hand_does_not_clear(env):
    f, log = env["folder"], env["log"]
    _open_clearance(env)
    # mara (marketing) is a resolvable party, so her vote IS recorded...
    s, b = _act(env["port"], f, principal="mara",
                params={"request_id": "clear-1", "decision": "approve"})
    assert s == 200 and b.get("ok") is True
    # ...and the RESPONSE says so honestly: the vote did not count and the step
    # is still pending — the monitor cannot misread "ok" as a governance change.
    assert b["counted"] is False and b["state"] == "pending"
    assert "mara" not in (b.get("approvers") or [])
    # ...and the competence gate did not COUNT it: the step stays pending.
    r = resolve_approval(f, "clear-1", now=time.time() + 60, log_root=log)
    assert r["state"] == "pending"
    assert "mara" not in set(r.get("approvers", []))


# --- T-i5-wrongkey ---------------------------------------------------------
def test_foreign_key_decision_fails_verify_chain(env):
    f, log = env["folder"], env["log"]
    _open_clearance(env)
    _act(env["port"], f, principal="lara",
         params={"request_id": "clear-1", "decision": "approve"})
    mlog = MutationLog(f, log_root=Path(log))
    assert mlog.verify_chain().ok is True
    # Forge an ApprovalDecision with a FOREIGN key (an attacker lacking the
    # workspace identity key). Same host_id + valid prev_hash, so the ONLY
    # thing wrong is the signature — isolating the signature gate.
    lines = [json.loads(x) for x in mlog.log_file.read_text().splitlines()
             if x.strip()]
    foreign = Ed25519PrivateKey.generate()
    forged = {
        "event": "system", "channel": "system", "folder_path": f,
        "pair_id": "approval:clear-1", "lifecycle_state": "", "problem_id": "",
        "source_hash": "", "actor": "mallory", "audit_id": "forged-decision",
        "ts": 1.0, "extra": {"kind": "ApprovalDecision",
                             "request_id": "clear-1", "decision": "approve"},
        "prev_hash": _canonical_event_hash(lines[-1]), "signature": "",
        "host_id": lines[-1].get("host_id", ""),
    }
    forged["signature"] = signing.sign_bytes(
        _signed_bytes({**forged, "signature": ""}), foreign)
    mlog.log_file.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lines + [forged])
        + "\n")
    result = MutationLog(f, log_root=Path(log)).verify_chain()
    assert result.ok is False
    assert any(sf.get("audit_id") == "forged-decision"
               for sf in result.signature_failures)


# --- T-i5-readonly ---------------------------------------------------------
def test_board_path_has_no_write_route(env):
    # The read-only board path rejects a POST outright (no write handler).
    req = urllib.request.Request(
        f"http://127.0.0.1:{env['port']}/govlive/board?folder={env['folder']}",
        data=b"{}", headers={"X-Workspaces-Token": _TOKEN,
                             "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 404


def test_act_route_dispatches_only_the_allowlist(env):
    f, log = env["folder"], env["log"]
    before = len(_approval_events(f, log))
    # A read op (or any non-interaction op) is NOT dispatchable through the act
    # route — refused before any store touch, ZERO mutation. The monitor cannot
    # turn its interaction surface into a general write proxy.
    for bad in ("governance_live", "operate", "workspace_workspace", ""):
        s, b = _act(env["port"], f, op=bad, principal="lara",
                    params={"request_id": "clear-1", "decision": "approve"})
        assert s == 403, bad
        assert "not a governed-interaction op" in (b or {}).get("error", ""), bad
    assert len(_approval_events(f, log)) == before


# --- T-i5-onecontract ------------------------------------------------------
def test_board_reflects_exactly_the_governed_act(env):
    f, log = env["folder"], env["log"]
    _open_clearance(env)
    _act(env["port"], f, principal="lara",
         params={"request_id": "clear-1", "decision": "approve"})
    _act(env["port"], f, principal="finn",
         params={"request_id": "clear-1", "decision": "approve"})
    board = _board(env["port"], f)
    # The monitor sees EXACTLY what the gated path recorded — the two signed
    # ApprovalDecision votes appear on the read-only board chain.
    decisions = {(e["actor"], e["extra"]) for e in board["chain"]
                 if e["extra"] == "ApprovalDecision"}
    assert ("lara", "ApprovalDecision") in decisions
    assert ("finn", "ApprovalDecision") in decisions
    # One contract: the HTTP board IS the governance_live projection, field-for-
    # field — no privileged monitor view.
    assert board == governance_live(f, log_root=log, chain_limit=100)
