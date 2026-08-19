# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Proof loop: RVND works AND is usable purely via its MCP tools.

Every step below goes through a ``@mcp.tool()`` entry point (``workspace_*`` in
``mcp_server``) — the same surface an MCP client calls — never an internal function. The
loop drives the whole governance lifecycle for several scenarios and asserts the verdict:

    policy_ingest (MCP)  →  patch_apply (MCP)  →  operate / governance_graph (MCP)

It proves: (1) a written policy compiles to an enforced twin; (2) a reservation routes to a
human (disposition=reserved); (3) a prohibition is a hard NO-GO (egress verdict=prohibited,
ceiling 0); (4) the negation-conditional is read as a reservation, not a ban; (5) the tools
self-describe (help) and the governance/audit facades are reachable via MCP.

Runnable two ways:
    pytest tests/test_mcp_usability_proof.py
    PYTHONPATH=src python3 tests/test_mcp_usability_proof.py     # prints a PASS/FAIL loop report
"""
from __future__ import annotations

import os
import tempfile

from rvnd import mcp_server as M

AGENT = "ai_system"     # policy_ingest emits this actor; operate runs as this agent

# (name, policy text, expected governance outcome)
SCENARIOS = [
    ("reservation-routes-to-human",
     "Generated content must be approved by a moderator.", "reserved"),
    ("negation-conditional-is-a-reservation",
     "Automated decisions shall not be taken without human review.", "reserved"),
    ("prohibition-is-hard-nogo",
     "The assistant must not run unreviewed inferences.", "prohibited"),
]


def _setup_workspace(folder: str) -> None:
    M.workspace_workspace("add", {"folder_context": folder})
    M.workspace_policy("party_register",
                       {"folder_context": folder, "party_id": AGENT, "kind": "agent", "actor": "x"})


def _kind(twin: dict) -> str:
    patch = twin.get("patch", {})
    for key in ("reservations", "prohibitions"):
        if patch.get(key):
            return patch[key][0]["kind"]
    raise AssertionError("no express primitive in twin")


def run_scenario(folder: str, name: str, policy: str, expect: str) -> list[str]:
    fail: list[str] = []

    # 1. INGEST via MCP
    tw = M.workspace_workflow("policy_ingest", {"folder_context": folder, "policy_text": policy})
    if not tw.get("ok") or not tw.get("netlist"):
        return [f"{name}: policy_ingest(MCP) failed: {tw}"]
    kind = _kind(tw)

    # 2. APPLY via MCP — write the twin to the signed chain, get the governance graph
    res = M.workspace_workflow("patch_apply",
                               {"folder_context": folder, "actor": "alex", "netlist": tw["netlist"]})
    if not res.get("ok"):
        return [f"{name}: patch_apply(MCP) failed: {res}"]
    ucs = [n for n in res["graph"]["nodes"] if n["kind"] == "use_case"]
    if not ucs:
        fail.append(f"{name}: no use_case node produced by apply")

    if expect == "reserved":
        # 3a. OPERATE via MCP — a reserved act must route to a human, even with zero issues
        r = M.workspace_workflow("operate", {"folder_context": folder, "use_case_id": kind,
                                             "agent_id": AGENT, "issues": [],
                                             "now_epoch": 1_750_000_000})
        steps = r.get("steps", [])
        if not any(s.get("disposition") == "reserved" for s in steps):
            fail.append(f"{name}: operate(MCP) did not reserve; got {r}")
        if r.get("final") == "complete":
            fail.append(f"{name}: reserved act auto-completed (fail-open) {r}")

    elif expect == "prohibited":
        # 3b. graph (MCP) — a prohibition is a hard NO-GO: egress verdict + ceiling 0
        proh = [u for u in ucs if u.get("prohibited")]
        egress = {e["from"]: e["verdict"] for e in res["graph"]["edges"] if e["kind"] == "egress"}
        if not proh:
            fail.append(f"{name}: prohibition not enforced (no prohibited use_case)")
        else:
            if not all(u["grade_ceiling"] == 0 for u in proh):
                fail.append(f"{name}: prohibited act ceiling != 0")
            if egress.get(proh[0]["id"]) != "prohibited":
                fail.append(f"{name}: egress verdict != prohibited; got {egress}")
    return fail


def _proof(folder: str) -> tuple[int, list[str], list[str]]:
    _setup_workspace(folder)
    scenario_failures: list[str] = []
    for name, policy, expect in SCENARIOS:
        scenario_failures += run_scenario(folder, name, policy, expect)

    # usability of the MCP surface itself: tools self-describe + facades reachable
    usability_failures: list[str] = []
    for tool, op in ((M.workspace_workflow, "help"), (M.workspace_audit, "help"),
                     (M.workspace_policy, "help")):
        out = tool(op)
        if not isinstance(out, dict) or not out:
            usability_failures.append(f"tool help via MCP returned nothing: {tool.__name__}")
    graph = M.workspace_workflow("governance_graph", {"folder_context": folder})   # nodes are top-level
    if not [n for n in graph.get("nodes", []) if n.get("kind") == "use_case"]:
        usability_failures.append("governance_graph(MCP) shows no applied governance on the chain")

    passed = len(SCENARIOS) - len({f.split(':', 1)[0] for f in scenario_failures})
    return passed, scenario_failures, usability_failures


# ── pytest entry ──────────────────────────────────────────────────────────────
def test_rvnd_is_usable_via_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "org"; f.mkdir()
    _, scenario_failures, usability_failures = _proof(str(f))
    failures = scenario_failures + usability_failures
    assert not failures, "MCP usability failures:\n" + "\n".join(f"  - {x}" for x in failures)


# ── standalone loop report ──────────────────────────────────────────────────────
def main() -> int:
    os.environ["WORKSPACES_ALLOW_UNREGISTERED"] = "1"
    d = tempfile.mkdtemp(prefix="rvnd_mcp_proof_")
    os.environ.setdefault("WORKSPACE_L0_LOG_ROOT", os.path.join(d, "logs"))
    os.environ.setdefault("WORKSPACE_KEY_DIR", os.path.join(d, "keys"))
    folder = os.path.join(d, "org"); os.makedirs(folder, exist_ok=True)
    passed, scenario_failures, usability_failures = _proof(folder)
    print("RVND — MCP usability proof loop (every call via a workspace_* MCP tool)\n")
    print("  governance lifecycle  ingest → patch_apply → operate / graph  (all via MCP):")
    for name, policy, expect in SCENARIOS:
        ok = not any(x.startswith(name + ":") for x in scenario_failures)
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}  (expect {expect})  ← {policy!r}")
    print(f"\n  MCP surface usability: {'PASS' if not usability_failures else 'FAIL'}"
          "  (tools self-describe via help · governance graph readable)")
    failures = scenario_failures + usability_failures
    print(f"\n  scenarios {passed}/{len(SCENARIOS)} · total checks "
          f"{'ALL GREEN' if not failures else str(len(failures)) + ' FAILED'}")
    if failures:
        for x in failures:
            print(f"    - {x}")
    print("\n  " + ("RVND works and is usable via MCP." if not failures else "SEE FAILURES ABOVE."))
    return 0 if not failures else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
