# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""CLI-channel proof — supported ops reachable through the real ``workspaces`` CLI.

The capability register proves ops callable over the UI bridge, the gateway, and
the MCP facade. The CLI (``python -m workspaces.cli``) is the operator's real
surface, and it is a curated argparse tree — only some supported ops have a
subcommand. This gate maps each such subcommand to the register op it drives by
SHARED IMPLEMENTATION (the CLI handler and the facade op call the same underlying
function, so a CLI success is the op's success), then drives every mapping as a
real subprocess in a disposable workspace:

  * a valid invocation exits 0 and prints a real success marker;
  * an invalid invocation is refused (non-zero exit, or a controlled error with
    no success marker).

No mocks, no in-process shortcut — the actual CLI entrypoint runs. Ops needing an
inference backend are not CLI-provable in a clean candidate and are absent here by
design. Emits ``docs/evidence/cli-channel-matrix.json``.

  python -m pytest server/tests/test_cli_channel.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "server" / "src"
REGISTER = json.loads((REPO / "docs" / "evidence" / "capability-register.json").read_text())["operations"]
_STATUS = {(e["facade"], e["op"]): e["status"] for e in REGISTER}
MATRIX_OUT = REPO / "docs" / "evidence" / "cli-channel-matrix.json"

_SUPPORTED = {"ui-supported", "mcp-supported", "gateway-supported"}


# Each row: the register op, the shared impl proving the mapping, and how to build
# valid/invalid argv. ``F`` is a fresh workspace folder, ``S`` a second folder.
# ``allow`` toggles WORKSPACES_ALLOW_UNREGISTERED for the *valid* call; the invalid
# call either omits it (so an unregistered folder is refused) or supplies a bad arg.
def _rows(F: str, S: str) -> list[dict]:
    return [
        {"facade": "cross_workspace_read", "op": None,
         "impl": "cross_workspace.cross_workspace_read",
         "cli": "cross-workspace",
         "valid": ["cross-workspace", "--folder", F, "--source", S, "--json"],
         "marker": '"target"',
         "invalid": ["cross-workspace", "--folder", F, "--json"],  # no --source
         "invalid_allow": True},
        {"facade": "workspace_audit", "op": "shadow_scan",
         "impl": "shadow_workflow.classify_shadow_workflows",
         "cli": "shadow-scan",
         "valid": ["shadow-scan", "--folder", F, "--json"],
         "marker": '"summary"',
         "invalid": ["shadow-scan", "--folder", F, "--json"],  # unregistered (allow off)
         "invalid_allow": False},
        {"facade": "workspace_audit", "op": "verify_chain",
         "impl": "mutation_log.MutationLog.verify_chain",
         "cli": "status",
         "valid": ["status", "--folder", F],
         "marker": "Audit chain:",
         "invalid": ["status", "--folder", F],  # unregistered (allow off)
         "invalid_allow": False},
        {"facade": "workspace_lens", "op": "precedent_declare",
         "impl": "lens_service.precedent_declare",
         "cli": "lens precedent-declare",
         "valid": ["lens", "precedent-declare", "--json",
                   json.dumps({"folder_context": F, "id": "p1",
                               "chosen_option": "go", "actor": "alex"})],
         "marker": '"declared"',
         "invalid": ["lens", "precedent-declare", "--json",
                     json.dumps({"folder_context": F})],  # missing id
         "invalid_allow": True},
        {"facade": "workspace_lens", "op": "precedent_revoke",
         "impl": "lens_service.precedent_revoke",
         "cli": "lens precedent-revoke",
         "valid": ["lens", "precedent-revoke", "--json",
                   json.dumps({"folder_context": F, "id": "p1", "actor": "alex"})],
         "marker": '"revoked"',
         "invalid": ["lens", "precedent-revoke", "--json",
                     json.dumps({"folder_context": F})],  # missing id
         "invalid_allow": True},
        {"facade": "workspace_lens", "op": "precedent_list",
         "impl": "lens_service.precedent_list",
         "cli": "lens precedents",
         "valid": ["lens", "precedents", "--folder", F],
         "marker": "precedents:",
         "invalid": ["lens", "precedents", "--folder", F],  # unregistered (allow off)
         "invalid_allow": False},
        {"facade": "workspace_lens", "op": "budget_cap_set",
         "impl": "lens_service.budget_cap_set",
         "cli": "lens cap --set",
         "valid": ["lens", "cap", "--folder", F, "--set", "5"],
         "marker": "cap set:",
         "invalid": ["lens", "cap", "--folder", F, "--set", "-1"],  # cap must be > 0
         "invalid_allow": True},
        {"facade": "workspace_dispatch", "op": "list_pinned",
         "impl": "pinned_skills.list_pinned",
         "cli": "list-pins",
         "valid": ["list-pins", "--folder", F],
         "marker": "pinned",
         "invalid": ["list-pins", "--folder", F],  # unregistered (allow off)
         "invalid_allow": False},
    ]


def _run(argv: list[str], workspace: Path, allow: bool) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), env.get("PYTHONPATH", "")])
    env["WORKSPACE_KEY_DIR"] = str(workspace / "keys")
    env["WORKSPACE_L0_LOG_ROOT"] = str(workspace / "logs")
    if allow:
        env["WORKSPACES_ALLOW_UNREGISTERED"] = "1"
    else:
        env.pop("WORKSPACES_ALLOW_UNREGISTERED", None)
    r = subprocess.run([sys.executable, "-m", "workspaces.cli", *argv],
                       cwd=str(REPO), env=env, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


def _drive(row: dict) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="cli_"))
    (tmp / "ws").mkdir()
    (tmp / "src").mkdir()
    F, S = str(tmp / "ws"), str(tmp / "src")
    live = next(r for r in _rows(F, S) if (r["facade"], r["op"]) == (row["facade"], row["op"]))

    rc, out = _run(live["valid"], tmp, allow=True)
    valid_ok = rc == 0 and live["marker"] in out and '"error"' not in out
    valid_reason = "" if valid_ok else f"rc={rc} out={out.strip()[:160]!r}"

    rc2, out2 = _run(live["invalid"], tmp / "src", allow=live["invalid_allow"])
    refused = rc2 != 0 or ("error" in out2.lower() and live["marker"] not in out2)
    invalid_reason = "" if refused else f"rc={rc2} out={out2.strip()[:160]!r}"

    return {"facade": row["facade"], "op": row["op"], "cli": live["cli"],
            "impl": live["impl"], "status": _STATUS.get((row["facade"], row["op"])),
            "valid_ok": valid_ok, "valid_reason": valid_reason,
            "invalid_ok": refused, "invalid_reason": invalid_reason}


# built once — one subprocess pair per mapping (fast; ~8 rows)
ROWS = [_drive(r) for r in _rows("_", "_")]
_BY = {(r["facade"], r["op"]): r for r in ROWS}


@pytest.mark.parametrize("key", sorted(_BY, key=lambda k: (k[0], k[1] or "")))
def test_cli_op_is_registered(key):
    """Every op the CLI channel maps must be a classified register entry (guards a
    CLI command mapping to an unknown op). Under the strict transport basis these
    ops are deferred: the CLI is a real transport but not a public ui/gateway/mcp
    tier, so its success+refusal evidence (proven by the two tests below) is
    recorded in docs/evidence/transport-evidence-appendix.md, not as register support."""
    assert key in _STATUS, (
        f"{key} is mapped to a CLI command but is absent from the register")


@pytest.mark.parametrize("key", sorted(_BY, key=lambda k: (k[0], k[1] or "")))
def test_cli_valid_invocation_succeeds(key):
    r = _BY[key]
    assert r["valid_ok"], f"{r['cli']} valid call did not succeed: {r['valid_reason']}"


@pytest.mark.parametrize("key", sorted(_BY, key=lambda k: (k[0], k[1] or "")))
def test_cli_invalid_invocation_is_refused(key):
    r = _BY[key]
    assert r["invalid_ok"], f"{r['cli']} invalid call was not refused: {r['invalid_reason']}"


def test_write_cli_channel_matrix():
    proven = [r for r in ROWS if r["valid_ok"] and r["invalid_ok"]]
    doc = {"schema": "cli-channel-matrix-1",
           "cli_reachable_supported_ops": len(ROWS),
           "proven": len(proven),
           "rows": sorted(ROWS, key=lambda r: (r["facade"], r["op"] or ""))}
    MATRIX_OUT.write_text(json.dumps(doc, indent=1) + "\n")
    assert MATRIX_OUT.exists()
    assert len(proven) == len(ROWS), (
        f"{len(ROWS) - len(proven)} CLI mapping(s) not fully proven; see matrix")
