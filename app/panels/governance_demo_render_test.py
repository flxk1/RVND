#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""End-to-end governance-story acceptance test, driven through the visualiser.

Boots serve.py and seeds the governance story: an agent (bot7, L2) and a person
(alice); a low-risk task the agent may run on its own (uc-draft → auto) and a
high-risk task reserved to a person (uc-decide → reserved, "needs a person");
and one pending decision raised by the agent for the person to resolve. Then
runs governance_demo_render.mjs, which loads the actual app/src/index.html in
jsdom against the running server and walks the whole story: it identifies the
non-mutating sample (demo) mode, inspects the agent/task/person/boundary, reads
the SERVER-declared verdicts (proving the DOM renders those and not the client's
hardcoded sample), claims + records the pending decision as the person, confirms
the choice on the workspace's signed chain, and reloads to prove the decision
closed and persisted server-side.

  python3 app/governance_demo_render_test.py
"""
from __future__ import annotations
import os, sys, time, tempfile, threading, subprocess
from pathlib import Path

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp(prefix="govdemo_")
os.environ["WORKSPACE_KEY_DIR"] = os.path.join(tmp, "keys")
os.environ["WORKSPACE_L0_LOG_ROOT"] = os.path.join(tmp, "logs")
os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "server" / "src"))
import serve                                   # noqa: E402
import rvnd.mcp_server as S              # noqa: E402

F = os.path.join(tmp, "org")
os.makedirs(F, exist_ok=True)


def setup() -> str:
    """Seed the story and open one pending decision. Returns the decision id."""
    # This gate tests the PROJECTION (reserved vs auto verdicts on the canvas),
    # not authentication. Since signed governance sessions are enforced, a
    # high-risk `operate` without an admitted session is refused — correct in
    # production, but here it would swap the reserved verdict this gate exists
    # to render. Same convention as conftest's `_isolate_session_admission`:
    # replace the verifier in this test process only; the signed gate itself
    # is covered by the live_session_admission tests.
    from rvnd import session_admission
    session_admission.verify_operation_session = lambda *a, **k: object()
    S.workspace_workspace("add", {"folder_context": F})
    # an agent (grade L2) and a person
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "bot7", "kind": "agent", "grade": "L2"})
    S.workspace_policy("party_register", {"folder_context": F, "party_id": "alice", "kind": "human"})
    # a low-risk task the agent may run on its own → auto
    S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "uc-draft", "name": "uc-draft",
                                               "fingerprint": {"issue_type": "liability_cap"}, "risk": "low",
                                               "allowed_agents": ["bot7"], "actor": "alex",
                                               "prior_approvals": 25, "override_window_seconds": 120})
    # a high-risk task with no agent, reserved to a person → reserved
    S.workspace_workflow("use_case_register", {"folder_context": F, "use_case_id": "uc-decide", "name": "uc-decide",
                                               "fingerprint": {"issue_type": "automated_decision"}, "risk": "high",
                                               "allowed_agents": [], "actor": "alex"})
    # author the reservation (attributed to the user's own policy), then run both
    # tasks so the server projects a reserved egress verdict and an auto one.
    S.workspace_workflow("patch_apply", {"folder_context": F, "actor": "alex", "netlist":
        "actor bot7\ngate uc-decide risk high grant bot7\ncord bot7 -> uc-decide\n"
        "cord uc-decide -> master\nreserve uc-decide by data-protection\n"})
    S.workspace_workflow("operate", {"folder_context": F, "use_case_id": "uc-draft", "agent_id": "bot7",
                                     "issues": [{"issue_id": "i1", "issue_type": "liability_cap", "completeness": "high"}],
                                     "now_epoch": 1000})
    S.workspace_workflow("operate", {"folder_context": F, "use_case_id": "uc-decide", "agent_id": "bot7",
                                     "issues": [{"issue_id": "i2", "issue_type": "automated_decision", "completeness": "high"}],
                                     "now_epoch": 1000})
    # the residual the engine will not decide: a pending decision for the person.
    # Raised by the agent so separation of duties lets the person (app-user) decide.
    out = S.workspace_dispatch("decision_open", {"folder_context": F, "raised_by": "bot7", "auto_notify": False,
        "surface": {"query": "Approve the automated decision for this high-risk task?",
                    "options": [
                        {"id": "approve", "label": "Approve with human sign-off", "conclusion": "approve",
                         "supporting": [], "consequences": ["proceeds under human sign-off (L2)"]},
                        {"id": "refuse", "label": "Refuse — route back", "conclusion": "refuse",
                         "supporting": [], "consequences": ["the task halts"]}]}})
    if not out.get("ok"):
        raise SystemExit("seed failed to open the decision: " + str(out))
    return out["decision_id"]


def main() -> int:
    did = setup()
    os.environ["RVND_BRIDGE_TOKEN"] = os.urandom(24).hex()  # server + node share this session token
    srv = serve.make_server(port=0)          # ephemeral — no cross-test collisions
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        r = subprocess.run(["node", str(HERE / "governance_demo_render.mjs"), str(PORT), F, did],
                           capture_output=True, text=True, timeout=90)
    finally:
        srv.shutdown()
    print((r.stdout + r.stderr).strip())
    return 0 if r.returncode == 0 and "PASS" in r.stdout else 1


if __name__ == "__main__":
    raise SystemExit(main())
