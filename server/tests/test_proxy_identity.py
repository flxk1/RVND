# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Trusted-front identity (rungs 2–3): the bridge believes only its declared
principal header, injects the resolved party as the actor on governed
operations, scopes reads to party membership, refuses unresolved principals
fail-closed, and auto-registers parties from mapped directory groups.

Claims under test (written before the logic):
  X1  no declared header → identity headers are ignored entirely (rung-0
      regression: a spoofed header changes nothing)
  X2  declared header + registered party → the actor is injected on governed
      calls; a client-sent actor is overridden by the proxy's principal
  X3  unresolved principal → writes and folder-addressed reads alike refuse
      in words (fail-closed: an unmatched principal reads nothing in the
      folder, never everything)
  X4  declared trust but no principal header on the request → every tool
      call refuses (the proxy is missing, fail closed)
  X5  groups header + identity map → the party auto-registers with mapped
      competences (recorded) and then resolves; unmapped groups register
      nothing and leave the caller unresolved (refused)
  X6  a decision claimed through the proxy records auth_rung=proxy-verified
  X7  /whoami reports trust mode and the principal
  X8  the workspace list is scoped to party membership: two parties
      registered in two different folders see disjoint lists; suspending a
      party removes its membership
  X9  an unmatched principal gets an empty workspace list (no default
      leaked) and cannot read a folder's policy snapshot; a registered
      party keeps the snapshot read
  X10 local single-operator mode (no declared header) is unchanged: the
      full workspace list and the snapshot answer regardless of any
      identity header sent
  X11 run-lifecycle operations without a folder_context (queue, take_next,
      inspect_stuck, and every run_id-addressed op) are scoped to party
      membership: a party sees and touches only its own workspaces' runs,
      an unmatched principal gets empty results and refusals, and local
      mode keeps the cross-workspace view
  X12 /whoami?folder= answers which console units the resolved party's
      role warrants (chrome gating — comfort, not protection): approver →
      widget only; unmatched or suspended → none; local mode → all units
  X13 an unresolved principal is refused a folder addressed by any key, not
      only folder_context: the filesystem-browser read (workspace_folder
      op=list, keyed on path) and a card read (keyed on folder) both refuse
  X14 remote requests cannot override server-owned storage roots, including
      when the caller is a resolved party; local single-operator calls retain
      the Python API's explicit-root behavior

Run: python -m pytest server/tests/test_proxy_identity.py -q
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "app"))
import serve  # noqa: E402

import workspaces.mcp_server as S
from workspaces.parties import list_parties, register_party, set_party_status

SURFACE = {
    "query": "q",
    "options": [{"id": "a", "label": "A", "conclusion": "a",
                 "supporting": [], "consequences": []},
                {"id": "b", "label": "B", "conclusion": "b",
                 "supporting": [], "consequences": []}],
}


# Pin the bridge session token so the in-process HTTP caller can present it on
# POST /tool (the loopback guard alone no longer admits a local caller).
_BRIDGE_TOKEN = "proxy-identity-test-token"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "log"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("RVND_BRIDGE_TOKEN", _BRIDGE_TOKEN)
    srv = serve.make_server(port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    folder = tmp_path / "ws"
    folder.mkdir()
    yield {"port": port, "folder": str(folder), "log": str(tmp_path / "log"),
           "mp": monkeypatch}
    srv.shutdown()


def call(port, tool, args, headers=None, path="/tool"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps({"tool": tool, "args": args}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Workspaces-Token": _BRIDGE_TOKEN, **(headers or {})})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get(port, path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def opened(env) -> str:
    return S.workspace_dispatch("decision_open", {
        "folder_context": env["folder"], "surface": SURFACE,
        "raised_by": "crm-bot"})["decision_id"]


def test_no_declared_header_ignores_identity(env):               # X1
    did = opened(env)
    out = call(env["port"], "workspace_dispatch",
               {"op": "decision_claim",
                "params": {"folder_context": env["folder"],
                           "decision_id": did, "actor": "mallory"}},
               headers={"X-Auth-Request-Email": "spoof\x40example.com"})
    assert out["ok"] is True and out["claimed_by"] == "mallory", \
        "without a declared header the bridge must ignore identity headers"


def test_resolved_party_overrides_client_actor(env):             # X2
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    register_party(env["folder"], party_id="dana\x40corp.example", kind="human",
                   competences=["data-protection"], actor="alex",
                   log_root=env["log"])
    did = opened(env)
    out = call(env["port"], "workspace_dispatch",
               {"op": "decision_claim",
                "params": {"folder_context": env["folder"],
                           "decision_id": did, "actor": "mallory"}},
               headers={"X-Auth-Request-Email": "dana\x40corp.example"})
    assert out["ok"] is True
    assert out["claimed_by"] == "dana\x40corp.example", \
        "the proxy's principal must beat the browser's claimed actor"


@pytest.mark.parametrize("root_key", [
    "log_root", "user_root", "store_root", "key_dir",
])
@pytest.mark.parametrize("root_value", [None, "", "/tmp/client-chosen-root"])
def test_remote_request_cannot_override_server_storage_roots(
        env, root_key, root_value):                              # X14
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    register_party(env["folder"], party_id="dana\x40corp.example", kind="human",
                   competences=["data-protection"], actor="alex",
                   log_root=env["log"])
    out = call(env["port"], "workspace_dispatch",
               {"op": "decision_pending",
                "params": {"folder_context": env["folder"],
                           root_key: root_value}},
               headers={"X-Auth-Request-Email": "dana\x40corp.example"})
    assert out == {
        "ok": False,
        "error": "server-owned storage roots cannot be overridden by a remote request",
        "refused_params": [root_key],
    }


def test_local_mode_keeps_explicit_storage_root_api():           # X14
    from workspaces.mcp_serving import (
        apply_principal_to_params,
        clear_request_principal,
    )

    clear_request_principal()
    params = {"log_root": "/tmp/operator-selected-root"}
    assert apply_principal_to_params(None, params) is None
    assert params == {"log_root": "/tmp/operator-selected-root"}


def test_unresolved_refuses_writes_and_reads(env):               # X3
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    did = opened(env)
    hdr = {"X-Auth-Request-Email": "stranger\x40corp.example"}
    write = call(env["port"], "workspace_dispatch",
                 {"op": "decision_claim",
                  "params": {"folder_context": env["folder"],
                             "decision_id": did, "actor": "stranger"}},
                 headers=hdr)
    assert write["ok"] is False and "not a registered party" in write["error"]
    read = call(env["port"], "workspace_dispatch",
                {"op": "decision_pending",
                 "params": {"folder_context": env["folder"]}}, headers=hdr)
    assert read["ok"] is False and "not a registered party" in read["error"], \
        "an unmatched principal must read nothing in the folder"


def test_unresolved_refuses_path_and_folder_addressed_reads(env):  # X13
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    hdr = {"X-Auth-Request-Email": "stranger\x40corp.example"}
    # filesystem browser: addressed by `path`, once slipped past a folder_context-
    # only gate and enumerated the host filesystem for an unmatched principal
    browse = call(env["port"], "workspace_folder",
                  {"op": "list", "params": {"path": env["folder"]}}, headers=hdr)
    assert browse["ok"] is False and "not a registered party" in browse["error"], \
        "an unmatched principal must not enumerate a folder by path"
    # card read: addressed by `folder`
    card = call(env["port"], "workspace_legal",
                {"op": "card.list", "params": {"folder": env["folder"]}}, headers=hdr)
    assert card["ok"] is False and "not a registered party" in card["error"], \
        "an unmatched principal must not read cards by folder"


def test_missing_principal_fails_closed(env):                    # X4
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    out = call(env["port"], "workspace_workspace", {"op": "list"})
    assert out["ok"] is False and "no principal header" in out["error"]


def test_proxy_refuses_unregistered_folder_before_identity_mapping(env, tmp_path):
    """Request-controlled folders are allowlisted before group auto-registers.

    Without this order, a proxy-authenticated request carrying mapped groups
    could make ``ensure_party`` touch the audit store for an arbitrary host
    path before the workspace boundary had been checked.
    """
    env["mp"].delenv("WORKSPACES_ALLOW_UNREGISTERED", raising=False)
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    env["mp"].setenv("WORKSPACE_PRINCIPAL_GROUPS_HEADER", "X-Auth-Request-Groups")
    identity_map = tmp_path / "identity-map.yml"
    identity_map.write_text("groups:\n  trusted:\n    competences: [review]\n")
    env["mp"].setenv("WORKSPACE_IDENTITY_MAP", str(identity_map))
    outside = tmp_path / "unregistered" / "target"

    out = call(env["port"], "workspace_dispatch",
               {"op": "decision_pending",
                "params": {"folder_context": str(outside)}},
               headers={"X-Auth-Request-Email": "proxy-user",
                        "X-Auth-Request-Groups": "trusted"})

    assert out["ok"] is False
    assert "not a registered party" in out["error"]
    assert not outside.exists()


def test_groups_map_auto_registers(env, tmp_path):               # X5
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    env["mp"].setenv("WORKSPACE_PRINCIPAL_GROUPS_HEADER", "X-Auth-Request-Groups")
    m = tmp_path / "identity-map.yml"
    m.write_text("groups:\n  sg-dpo:\n    competences: [data-protection]\n")
    env["mp"].setenv("WORKSPACE_IDENTITY_MAP", str(m))
    did = opened(env)
    out = call(env["port"], "workspace_dispatch",
               {"op": "decision_claim",
                "params": {"folder_context": env["folder"], "decision_id": did}},
               headers={"X-Auth-Request-Email": "neu\x40corp.example",
                        "X-Auth-Request-Groups": "sg-dpo,sg-other"})
    assert out["ok"] is True and out["claimed_by"] == "neu\x40corp.example"
    roster = list_parties(env["folder"], log_root=env["log"])["parties"]
    neu = next(p for p in roster if p["party_id"] == "neu\x40corp.example")
    assert neu["competences"] == ["data-protection"]
    # unmapped groups register nothing — the caller stays unresolved and the
    # folder-addressed read refuses (fail-closed read scoping)
    out2 = call(env["port"], "workspace_dispatch",
                {"op": "decision_pending",
                 "params": {"folder_context": env["folder"]}},
                headers={"X-Auth-Request-Email": "other\x40corp.example",
                         "X-Auth-Request-Groups": "sg-unmapped"})
    assert out2["ok"] is False and "not a registered party" in out2["error"]
    ids = {p["party_id"] for p in list_parties(env["folder"],
                                               log_root=env["log"])["parties"]}
    assert "other\x40corp.example" not in ids


def test_proxy_claim_records_rung(env):                          # X6
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    register_party(env["folder"], party_id="dana\x40corp.example", kind="human",
                   competences=[], actor="alex", log_root=env["log"])
    did = opened(env)
    call(env["port"], "workspace_dispatch",
         {"op": "decision_claim",
          "params": {"folder_context": env["folder"], "decision_id": did}},
         headers={"X-Auth-Request-Email": "dana\x40corp.example"})
    from workspaces.mutation_log import MutationLog
    events = [e.extra for e in MutationLog(Path(env["folder"]),
                                           log_root=Path(env["log"])).replay()
              if (e.extra or {}).get("kind") == "decision.claimed"]
    assert events and events[-1].get("auth_rung") == "proxy-verified"


def test_whoami_reports_trust_state(env):                        # X7
    assert get(env["port"], "/whoami")["trust_mode"] is False
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    out = get(env["port"], "/whoami",
              headers={"X-Auth-Request-Email": "dana\x40corp.example"})
    assert out["trust_mode"] is True and out["principal"] == "dana\x40corp.example"


def _workspace_list(env, principal):
    return call(env["port"], "workspace_workspace", {"op": "list"},
                headers={"X-Auth-Request-Email": principal})


def test_workspace_list_scoped_to_membership(env):               # X8
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    folder_b = str(Path(env["folder"]).parent / "ws-b")
    Path(folder_b).mkdir()
    S.workspace_workspace("add", {"folder_context": env["folder"]})
    S.workspace_workspace("add", {"folder_context": folder_b})
    register_party(env["folder"], party_id="alice\x40corp.example", kind="human",
                   competences=[], actor="alex", log_root=env["log"])
    register_party(folder_b, party_id="bob\x40corp.example", kind="human",
                   competences=[], actor="alex", log_root=env["log"])
    a_paths = {w["path"] for w in
               _workspace_list(env, "alice\x40corp.example")["workspaces"]}
    b_paths = {w["path"] for w in
               _workspace_list(env, "bob\x40corp.example")["workspaces"]}
    assert a_paths == {str(Path(env["folder"]).resolve())}
    assert b_paths == {str(Path(folder_b).resolve())}
    assert not (a_paths & b_paths), \
        "each party must see only the workspaces it is registered in"
    # suspension ends membership: the scoped list empties
    set_party_status(env["folder"], "alice\x40corp.example", "suspended",
                     actor="alex", log_root=env["log"])
    assert _workspace_list(env, "alice\x40corp.example")["workspaces"] == []


def test_unmatched_principal_reads_nothing(env):                 # X9
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    S.workspace_workspace("add", {"folder_context": env["folder"]})
    register_party(env["folder"], party_id="alice\x40corp.example", kind="human",
                   competences=[], actor="alex", log_root=env["log"])
    hdr = {"X-Auth-Request-Email": "stranger\x40corp.example"}
    lst = call(env["port"], "workspace_workspace", {"op": "list"}, headers=hdr)
    assert lst["ok"] is True and lst["workspaces"] == []
    assert lst["default"] == ""
    snap = call(env["port"], "workspace_policy",
                {"op": "snapshot",
                 "params": {"folder_context": env["folder"]}}, headers=hdr)
    assert snap["ok"] is False and "not a registered party" in snap["error"]
    # a registered party keeps the scoped read
    ok_snap = call(env["port"], "workspace_policy",
                   {"op": "snapshot",
                    "params": {"folder_context": env["folder"]}},
                   headers={"X-Auth-Request-Email": "alice\x40corp.example"})
    assert "lock_is_active" in ok_snap


def test_local_mode_reads_unchanged(env):                        # X10
    S.workspace_workspace("add", {"folder_context": env["folder"]})
    hdr = {"X-Auth-Request-Email": "stranger\x40corp.example"}
    lst = call(env["port"], "workspace_workspace", {"op": "list"}, headers=hdr)
    assert lst["ok"] is True
    assert {w["path"] for w in lst["workspaces"]} == \
        {str(Path(env["folder"]).resolve())}
    snap = call(env["port"], "workspace_policy",
                {"op": "snapshot",
                 "params": {"folder_context": env["folder"]}}, headers=hdr)
    assert "lock_is_active" in snap and "error" not in snap


def test_run_lifecycle_scoped_without_folder_context(env):       # X11
    folder_a, folder_b = env["folder"], str(Path(env["folder"]).parent / "ws-b")
    Path(folder_b).mkdir()
    register_party(folder_a, party_id="alice\x40corp.example", kind="human",
                   competences=[], actor="alex", log_root=env["log"])
    register_party(folder_b, party_id="bob\x40corp.example", kind="human",
                   competences=[], actor="alex", log_root=env["log"])
    run_a = S.workspace_workflow("enqueue", {"folder_context": folder_a,
                                             "name": "wf-a"})["run_id"]
    run_b = S.workspace_workflow("enqueue", {"folder_context": folder_b,
                                             "name": "wf-b"})["run_id"]
    # local mode first: the folderless queue read spans workspaces unchanged
    both = call(env["port"], "workspace_workflow", {"op": "queue", "params": {}})
    assert {e["run_id"] for e in both["entries"]} == {run_a, run_b}
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    alice = {"X-Auth-Request-Email": "alice\x40corp.example"}
    stranger = {"X-Auth-Request-Email": "stranger\x40corp.example"}
    # queue: membership-scoped rows; an unmatched principal gets nothing
    q = call(env["port"], "workspace_workflow", {"op": "queue", "params": {}},
             headers=alice)
    assert {e["run_id"] for e in q["entries"]} == {run_a}, \
        "a party must see only its own workspaces' queue entries"
    q0 = call(env["port"], "workspace_workflow", {"op": "queue", "params": {}},
              headers=stranger)
    assert q0["ok"] is True and q0["entries"] == []
    # inspect_stuck: both runs are pending-stale after the cutoff elapses
    time.sleep(2)
    stuck = call(env["port"], "workspace_workflow",
                 {"op": "inspect_stuck", "params": {"stale_pending_seconds": 1}},
                 headers=alice)
    assert {r["entry"]["run_id"] for r in stuck["stuck"]} == {run_a}
    stuck0 = call(env["port"], "workspace_workflow",
                  {"op": "inspect_stuck", "params": {"stale_pending_seconds": 1}},
                  headers=stranger)
    assert stuck0["ok"] is True and stuck0["stuck"] == []
    # take_next: an unmatched principal leases nothing; a party leases only
    # a run in its own workspace
    t0 = call(env["port"], "workspace_workflow",
              {"op": "take_next", "params": {"worker_id": "mallory"}},
              headers=stranger)
    assert t0 == {"ok": True, "state": "empty"}
    took = call(env["port"], "workspace_workflow",
                {"op": "take_next", "params": {"worker_id": "alice-worker"}},
                headers=alice)
    assert took["run_id"] == run_a
    assert took["folder_path"] == str(Path(folder_a).resolve())
    # run_id-addressed ops against another workspace's run refuse in words
    for op in ("renew_lease", "mark_done", "mark_failed", "resume", "cancel"):
        out = call(env["port"], "workspace_workflow",
                   {"op": op, "params": {"run_id": run_b}}, headers=alice)
        assert out["ok"] is False and "not a registered party" in out["error"], \
            f"{op} must refuse a foreign workspace's run"
    entry_b = next(e for e in S.workspace_workflow("queue", {})["entries"]
                   if e["run_id"] == run_b)
    assert entry_b["state"] == "pending", "the foreign run must stay untouched"
    # the run's own party keeps the lifecycle write
    ok = call(env["port"], "workspace_workflow",
              {"op": "cancel", "params": {"run_id": run_b}},
              headers={"X-Auth-Request-Email": "bob\x40corp.example"})
    assert ok["ok"] is True and ok["cancelled"] is True


def test_whoami_units_follow_role(env):                          # X12
    from urllib.parse import quote
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    register_party(env["folder"], party_id="vera\x40corp.example", kind="human",
                   role="approver", competences=[], actor="alex",
                   log_root=env["log"])
    q = "/whoami?folder=" + quote(env["folder"], safe="")
    out = get(env["port"], q,
              headers={"X-Auth-Request-Email": "vera\x40corp.example"})
    assert out["party"] == "vera\x40corp.example" and out["role"] == "approver"
    assert out["units"] == ["widget"], \
        "an approver's role warrants only the sign-off widget"
    stranger = get(env["port"], q,
                   headers={"X-Auth-Request-Email": "who\x40corp.example"})
    assert stranger["party"] is None and stranger["units"] == [], \
        "an unmatched principal warrants no units (fail-closed chrome)"
    set_party_status(env["folder"], "vera\x40corp.example", "suspended",
                     actor="alex", log_root=env["log"])
    held = get(env["port"], q,
               headers={"X-Auth-Request-Email": "vera\x40corp.example"})
    assert held["party"] is None and held["units"] == [], \
        "a suspended party's units die with its status"
    env["mp"].delenv("WORKSPACE_PRINCIPAL_HEADER")
    local = get(env["port"], "/whoami")
    assert local["trust_mode"] is False
    assert set(local["units"]) == {"chat", "mixdesk", "patchbay", "screen",
                                   "widget"}, \
        "local single-operator mode warrants every unit"


# X13  the console rollup (console_snapshot) is a folderless cross-workspace
#      read: it aggregates one bus per visible workspace, and in principal mode
#      it enumerates through the scoped registry — a party sees only its own
#      folders in the rollup, an unmatched principal gets an empty rollup, and
#      local mode sees every bus.
def _console(env, headers=None):
    return call(env["port"], "workspace_workflow",
                {"op": "console_snapshot", "params": {}}, headers=headers)


def test_console_snapshot_scoped_to_membership(env):             # X13
    folder_b = str(Path(env["folder"]).parent / "ws-b")
    Path(folder_b).mkdir()
    S.workspace_workspace("add", {"folder_context": env["folder"]})
    S.workspace_workspace("add", {"folder_context": folder_b})
    register_party(env["folder"], party_id="alice\x40corp.example", kind="human",
                   competences=[], actor="alex", log_root=env["log"])
    register_party(folder_b, party_id="bob\x40corp.example", kind="human",
                   competences=[], actor="alex", log_root=env["log"])

    # local mode: every bus is in the rollup
    local = _console(env)
    assert local["ok"] is True
    local_paths = {b["path"] for b in local["buses"]}
    assert local_paths == {str(Path(env["folder"]).resolve()),
                           str(Path(folder_b).resolve())}
    assert local["count"] == 2

    # principal mode: each party sees only its own bus — the folderless read
    # cannot widen past membership
    env["mp"].setenv("WORKSPACE_PRINCIPAL_HEADER", "X-Auth-Request-Email")
    a = _console(env, headers={"X-Auth-Request-Email": "alice\x40corp.example"})
    a_paths = {b["path"] for b in a["buses"]}
    assert a_paths == {str(Path(env["folder"]).resolve())}, \
        "alice must not see bob's folder in the cross-workspace rollup"

    # an unmatched principal gets an empty rollup, never the full set
    stranger = _console(env, headers={"X-Auth-Request-Email": "who\x40corp.example"})
    assert stranger["ok"] is True and stranger["buses"] == [] \
        and stranger["count"] == 0
