# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Op-drift gate: every op a skill declares must exist on the live MCP server.

The skills speak the real ``workspace_*`` surface (see
``plugin/rvnd/references/catalogue.md``), and that catalogue warns it
"can drift with the server". Nothing else in CI catches a skill's manifest
declaring a ``workspace_workflow(op=...)`` the server no longer exposes — a
silent break of the plugin -> backend connection for every orchestrator
(Claude, Codex, or an HTTP client). This test closes that gap: it reads the
live op surface from the installed server (``op="ops"`` is ground truth, the
same "discovery over memorisation" the skills rely on) and asserts every
op declared in every skill's ``manifest.yaml`` resolves.

Runs in the CI job that has the RVND package installed (server-tests, which runs
``pytest plugin/tests`` after the editable install). If the package is absent
it skips loudly rather than passing vacuously; the guard assertions below also
fail if manifest parsing silently finds nothing, so a "green" always means real
coverage.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "rvnd"
SKILLS = PLUGIN / "skills"

def _live_ops() -> dict[str, set[str]]:
    """Map every ``workspace_*`` facade tool -> the ops it exposes now.

    Discovers facades dynamically from the server module rather than a hardcoded
    list, so a skill binding a tool the test's author forgot to enumerate cannot
    produce a false "not a live tool" (or, worse, a false clean). Ground truth is
    each facade's own ``op="ops"`` — the same discovery the skills rely on.
    """
    m = pytest.importorskip(
        "rvnd.mcp_server",
        reason="RVND package not installed in this environment; run this in the "
               "CI job that installs it (server-tests) so drift is actually checked.")
    live: dict[str, set[str]] = {}
    for tool in [n for n in dir(m) if n.startswith("workspace_")]:
        fn = getattr(m, tool, None)
        if not callable(fn):
            continue
        try:
            r = fn(op="ops")
        except Exception:  # noqa: BLE001 — a facade without a metadata op just isn't mapped
            continue
        ops = r.get("ops") if isinstance(r, dict) else None
        if not ops:
            continue
        names = {o if isinstance(o, str) else (o.get("op") or o.get("name")) for o in ops}
        live[tool] = {n for n in names if n}
    return live


def _declared_ops() -> list[tuple[str, str, str]]:
    """Every (skill, tool, op) declared across the skills' manifests."""
    out: list[tuple[str, str, str]] = []
    for mf in sorted(SKILLS.glob("*/manifest.yaml")):
        skill = mf.parent.name
        text = mf.read_text()
        data = None
        try:
            import yaml  # optional; regex fallback below keeps this dependency-free
            data = yaml.safe_load(text)
        except Exception:  # noqa: BLE001
            data = None
        tools = []
        if isinstance(data, dict):
            tools = (data.get("rvnd") or {}).get("tools") or []
        if not tools:  # regex fallback: `- tool: X` then `ops: [a, b]`
            for blk in re.finditer(r"tool:\s*(\w+)\s*\n\s*ops:\s*\[([^\]]*)\]", text):
                tools.append({"tool": blk.group(1),
                              "ops": [o.strip() for o in blk.group(2).split(",") if o.strip()]})
        for t in tools:
            tool = t.get("tool")
            for op in (t.get("ops") or []):
                if tool and op:
                    out.append((skill, tool, op))
    return out


def test_every_declared_skill_op_exists_on_the_live_server():
    live = _live_ops()
    assert live, "no live op surface resolved — server import/enumeration failed"
    declared = _declared_ops()

    # No vacuous pass: the plugin ships several skills that bind ops. If parsing
    # finds almost nothing, the test is broken, not the surface clean.
    skills_with_ops = {s for (s, _, _) in declared}
    assert len(skills_with_ops) >= 5, f"suspiciously few skills parsed: {sorted(skills_with_ops)}"
    assert len(declared) >= 20, f"suspiciously few declared ops parsed: {len(declared)}"

    drift = []
    for skill, tool, op in declared:
        if tool not in live:
            drift.append(f"{skill}: {tool} is not a live facade tool")
        elif op not in live[tool]:
            drift.append(f"{skill}: {tool}(op={op!r}) not on the live server")

    assert not drift, "skill/server op drift (update the skill manifest or the catalogue):\n  " + \
        "\n  ".join(sorted(drift))
