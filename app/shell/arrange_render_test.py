#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Render test for the ARRANGE view: the LANES form (agent lanes + run clips,
read-only) and the MIX form (one channel strip per agent with status, a ladder
whose rungs each show the server matrix cell for the oversight band verbatim,
a mono readout, a strip screen, oversight word, pending count and a Hold
write; a bus header with pending total and Hold-all). Seeds a workspace with a
graded agent, a task, and a deliberately non-monotonic matrix band (go, block,
go, ask, block) — the grid carries no monotonicity rule, so a client that
composes a scalar ceiling from the cells renders false state; the assertions
compare the DOM to the server cells one for one. Also asserts the Hold write
landing as a party-status suspend and that the toggle restores the patch.

Strip screens: one global mode selector (Overview | Autonomy | Checks | Reach
| Time) governs every strip's screen. The seed adds a pending four_eyes
approval with a deadline routed to a competence the agent holds (Checks /
Time / Overview content), an ingress channel bound to the agent (Reach), and
a second agent whose use case carries a law-basis reserved act — that strip's
screen must show the reserved plate and render no control. The law act is
appended as a re-versioned UseCaseRegistered chain event, the way ingest
lands one (the authoring API carries policy-basis acts only).

Stops render through one shared component: the Checks page and the
inspector's CHECK·VERDICTS card both emit the stop card (.stopcard,
data-stopcard = hold|deny|reserved). State alone decides the affordance —
hold carries the Pending deep-link, reserved carries none.

  python3 app/arrange_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path
HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="arrange_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve, workspaces.mcp_server as S  # noqa: E402
F = os.path.join(tmp, "acme")
AGENT = "bot"
os.makedirs(F, exist_ok=True)
S.workspace_workspace("add", {"folder_context": F})
S.workspace_policy("party_register", {"folder_context": F, "party_id": AGENT, "kind": "agent", "grade": "L2",
                                      "competences": ["ship"], "channels": ["feed"], "actor": "alex"})
# a non-monotonic band: the matrix has no monotonicity rule, so per-rung cells
# must reach the strip verbatim — any client-composed scalar ceiling misrenders this grid
S.workspace_policy("set_oversight_level", {"folder_context": F, "level": "approve", "actor": "alex"})
for _g, _light in (("L0", "go"), ("L1", "block"), ("L2", "go"), ("L3", "ask"), ("L4", "block")):
    S.workspace_matrix("set", {"folder_context": F, "grade": _g, "oversight": "approve", "light": _light, "actor": "alex"})
S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "task1", "name": "Task one",
                                       "fingerprint": {}, "risk": "low", "allowed_agents": [AGENT], "actor": "alex"})
try:
    S.workspace_workflow("operate", {"folder_context": F, "use_case_id": "task1", "agent_id": AGENT,
                                 "issues": [{"issue_id": "i-1", "issue_type": "", "completeness": "high"}],
                                 "now_epoch": int(time.time())})
except Exception:
    pass
# strip-screen seeds: a channel for Reach, a deadlined four_eyes approval routed to
# the agent's competence for Checks/Time, and a law-locked second agent whose screen
# must show the reserved plate and no control
S.workspace_workflow("connector_register", {"folder_context": F, "connector_id": "feed",
                                            "role": "ingress", "channel": "email", "floor": "hold", "actor": "alex"})
S.workspace_workflow("approval_request", {"folder_context": F, "request_id": "req-1", "form": "four_eyes",
                                          "competence": "ship", "competences": ["ship"], "requester": "alex",
                                          "timeout_seconds": 3600, "now": int(time.time()),
                                          "on_elapse": "halt", "actor": "alex"})
LOCKED = "vault"
S.workspace_policy("party_register", {"folder_context": F, "party_id": LOCKED, "kind": "agent", "grade": "L1", "actor": "alex"})
S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "task2", "name": "Task two",
                                       "fingerprint": {}, "risk": "low", "allowed_agents": [LOCKED], "actor": "alex"})
from workspaces.mutation_log import LogEvent, MutationLog  # noqa: E402
from workspaces.use_case import get_use_case  # noqa: E402
_LR = os.environ["WORKSPACE_L0_LOG_ROOT"]
_rec = get_use_case(F, "task2", log_root=_LR)
_law = {"trigger": "automated_decision", "basis_kind": "law", "reserved_to": "dpo",
        "act_type": "review", "source": "GDPR Art. 22"}
MutationLog(F, log_root=_LR).append(LogEvent(
    event="system", folder_path=F, pair_id="use_case:task2", actor="ingest",
    extra={**_rec, "kind": "UseCaseRegistered",
           "reserved_acts": list(_rec.get("reserved_acts") or []) + [_law]}))


def main() -> int:
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "arrange_render.mjs"), str(PORT), F, AGENT, LOCKED],
                           capture_output=True, text=True, timeout=45)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
