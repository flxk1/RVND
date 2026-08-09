#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the Live Governance drawer (governance_live, v2).

Seeds REAL state — no fixtures, no fakes: three parties with governance lanes,
three sessions opened through the live admission gate (one expired via a
1-second TTL, one suspended AFTER admission so its lane verdict degrades),
use-cases registered so lane_capabilities has content, and two runs enqueued on
the same folder+workflow so the run-lease view shows one holder and one queued.
Then boots serve.py and runs govlive_render.mjs, which asserts presence plus
the four v2 honesty invariants: admission honesty, run-lease serialization,
chain linearity (DOM layer: replay index only — the own-hash stays private),
verdict honesty. And that the drawer is read-only (zero write buttons).

  .venv/bin/python app/panels/govlive_render_test.py
"""
from __future__ import annotations
import os, sys, time, threading, subprocess, tempfile
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="govlive_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                               # noqa: E402
import workspaces.mcp_server as S          # noqa: E402
from workspaces.governance_lane import GovernanceLane, register_lane   # noqa: E402
from workspaces.parties import register_party, set_party_status        # noqa: E402
from workspaces.session_admission import governance_open               # noqa: E402
from workspaces.mcp_serving import clear_request_principal, set_request_principal  # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)
LOG = os.environ["WORKSPACE_L0_LOG_ROOT"]

# Party names are the seed↔assert contract shared with govlive_render.mjs.
P_ADMITTED, P_EXPIRED, P_SUSPENDED = "aria", "nyx", "rex"
FP = "sha256:approved"


def _lane(party: str) -> None:
    register_party(F, party, "agent", grade="L2", log_root=LOG)
    register_lane(F, GovernanceLane(
        lane_id="lane-" + party, agent=party, max_grade="L2",
        action_classes=("classify",), folder=F, policy_fingerprint=FP,
        approved_by="controller", rationale="render-gate seed",
    ), log_root=LOG)


def seed_fixture() -> None:
    """Drive the real modules through their public surface, exactly as the
    admission tests do — the op then projects this state; nothing is faked."""
    S.workspace_workflow("use_case_register", {
        "folder_context": F, "use_case_id": "u", "name": "u",
        "fingerprint": {"issue_type": "automated_decision"},
        "risk": "high", "allowed_agents": [], "actor": "alex"})
    for party in (P_ADMITTED, P_EXPIRED, P_SUSPENDED):
        _lane(party)
    try:
        set_request_principal(P_ADMITTED, P_ADMITTED)
        governance_open(F, party=P_ADMITTED, policy_fingerprint=FP, log_root=LOG)
        set_request_principal(P_EXPIRED, P_EXPIRED)
        governance_open(F, party=P_EXPIRED, policy_fingerprint=FP,
                        ttl_seconds=1, log_root=LOG)
        set_request_principal(P_SUSPENDED, P_SUSPENDED)
        governance_open(F, party=P_SUSPENDED, policy_fingerprint=FP, log_root=LOG)
    finally:
        clear_request_principal()
    # Degrade rex AFTER admission: the session exists, the lane verdict must
    # no longer be in the GO family (verdict honesty needs a live example).
    set_party_status(F, P_SUSPENDED, "suspended", log_root=LOG)
    # Expire nyx's 1-second session for real — no clock mocking.
    time.sleep(1.3)
    # Two runs on the same folder+workflow: one holder, one queued. Every call
    # must succeed NOW — a silently failed seed would surface later as a bogus
    # invariant failure (or worse, a bogus pass) once the op lands.
    def ok(label, resp):
        assert isinstance(resp, dict) and resp.get("ok") and not resp.get("error"), (
            f"seed step {label!r} failed: {resp!r}")
        return resp
    ok("define", S.workspace_workflow("define", {
        "folder_context": F, "name": "operate",
        "steps": [{"skill_id": "workspace:noop", "query": "x", "on_failure": "stop"}],
        "description": "render-gate seed"}))
    ok("enqueue#1", S.workspace_workflow("enqueue", {"folder_context": F, "name": "operate", "enqueued_by": "seed"}))
    ok("take_next", S.workspace_workflow("take_next", {"worker_id": "worker-1", "lease_seconds": 120}))
    # A routed role-quorum reservation (I4): the step inspector drills a real
    # reserved step into its routed approvers / m-of-n quorum / competences.
    ok("approval_request", S.workspace_workflow("approval_request", {
        "folder_context": F, "request_id": "rq-quorum", "form": "four_eyes",
        "quorum": 2, "competences": ["legal", "finance", "risk"],
        "requester": P_ADMITTED, "now": int(time.time())}))
    # Invariant 5 at the SOURCE (contract 2026-08-08): serialization is BY
    # REFUSAL — the run plane refuses a second concurrent run for the same
    # (folder, workflow), so the queued-contender state cannot exist and the
    # board renders exactly one in-flight lease. Assert the refusal here;
    # the .mjs asserts the single-lease rendering.
    resp2 = S.workspace_workflow("enqueue", {"folder_context": F, "name": "operate", "enqueued_by": "seed"})
    assert not resp2.get("ok") and "already_" in str(resp2.get("error", "")), (
        f"seed step 'enqueue#2' must be REFUSED (already_queued/already_running): {resp2!r}")
    # Seed ownership (spec §4, final): each side keeps its OWN honest
    # real-state seed — a shared fixture would couple this render gate to the
    # op's key material. This seed and the op's unit fixture stay independent
    # by design; the modules they drive are the shared truth.


def main() -> int:
    seed_fixture()
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "govlive_render.mjs"), str(PORT), F,
                            P_ADMITTED, P_EXPIRED, P_SUSPENDED],
                           capture_output=True, text=True, timeout=45)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
