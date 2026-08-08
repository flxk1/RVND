#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Real render test for the Integral governance strip (I1, governance_live).

Seeds the same REAL state as the drawer's gate — three parties with lanes,
three live admissions (one expired via 1s TTL, one suspended after admission
so its verdict leaves the GO family), one run leased — then boots serve.py and
runs govstrip_render.mjs: the always-on strip renders the four traffic-light
tiles with counts from the live board, the HOTL alarm is armed and NAMES a
not-green session (cross-checked against a direct governance_live call, not a
hardcoded expectation), clicking the strip expands the full v2 drawer, and the
strip carries no write controls.

  .venv/bin/python app/shell/govstrip_render_test.py
"""
from __future__ import annotations
import os, sys, time, threading, subprocess, tempfile
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="govstrip_")
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
P_ADMITTED, P_EXPIRED, P_SUSPENDED = "aria", "nyx", "rex"
FP = "sha256:approved"


def _lane(party: str) -> None:
    register_party(F, party, "agent", grade="L2", log_root=LOG)
    register_lane(F, GovernanceLane(
        lane_id="lane-" + party, agent=party, max_grade="L2",
        action_classes=("classify",), folder=F, policy_fingerprint=FP,
        approved_by="controller", rationale="strip-gate seed",
    ), log_root=LOG)


def seed() -> None:
    def ok(label, resp):
        assert isinstance(resp, dict) and resp.get("ok") and not resp.get("error"), (
            f"seed step {label!r} failed: {resp!r}")
        return resp
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
    set_party_status(F, P_SUSPENDED, "suspended", log_root=LOG)
    time.sleep(1.3)
    ok("define", S.workspace_workflow("define", {
        "folder_context": F, "name": "operate",
        "steps": [{"skill_id": "workspace:noop", "query": "x", "on_failure": "stop"}],
        "description": "strip-gate seed"}))
    ok("enqueue", S.workspace_workflow("enqueue", {"folder_context": F, "name": "operate", "enqueued_by": "seed"}))
    ok("take_next", S.workspace_workflow("take_next", {"worker_id": "worker-1", "lease_seconds": 120}))


def main() -> int:
    seed()
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    # Watchdog-retry (ticker-gate discipline): retry ONLY on a jsdom boot
    # stall/timeout — a real assertion failure returns immediately, unmasked.
    try:
        out = ""
        for attempt in range(3):
            try:
                r = subprocess.run(["node", str(HERE / "govstrip_render.mjs"), str(PORT), F],
                                   capture_output=True, text=True, timeout=30)
                out = (r.stdout + r.stderr).strip()
                if "PASS" in r.stdout:
                    print(out)
                    return 0
                if "watchdog" not in out:
                    print(out)
                    return 1
            except subprocess.TimeoutExpired:
                out = f"node timed out (attempt {attempt + 1}) — jsdom boot stall"
            print(f"[retry] {out}")
        print(out)
        return 1
    finally:
        srv.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
