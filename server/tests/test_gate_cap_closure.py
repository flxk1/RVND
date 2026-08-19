# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Every path to a GO passes a point a human can hold — or is on the register.

The Breaker inverts the default: running requires continuous permission, and the
human's *absence* stops the agent. That inversion is only as good as its closure.
A single call site that reaches ``action_gate.gate`` with a grade nobody capped is
a path on which a quarantined agent still acts, and no amount of correctness in
the Breaker itself repairs it — the mechanism was simply not consulted.

Closure is not a property of any one module, so no unit test sees it. This is an
architecture test: it reads the source, finds every module that can produce a GO,
and asserts each one either caps the requested grade against that agent's Breaker
state or appears below with a reason. **The register is a baseline, not an
endorsement.** Its entries are known-uncapped paths; the test's job is to stop the
set growing silently and to force a decision when it changes.

The invariant, stated once: *a GO is only ever issued against a grade some
authority could have lowered.*

Sibling to ``test_guardian_invariants.py``, which pins the same property from the
other side — the human's kill switch cannot be gated away. Together: the human's
power cannot be removed, and it cannot be routed around.
"""
from __future__ import annotations

import ast
import pathlib

WORKSPACES = pathlib.Path(__file__).resolve().parents[1] / "src" / "workspaces"

#: A module consults a cap if it reaches any of these. ``cap_grade`` and
#: ``Breaker`` are the Breaker path; ``_actor_grade_cap`` is the party-register
#: kill switch that ``governance`` applies on top of it.
_CAP_MARKERS = ("cap_grade", "Breaker", "_actor_grade_cap")

#: Known-uncapped ``gate`` call sites, each with why it is still here. Every entry
#: is a path on which a quarantined agent's cap does not apply. Moving one out of
#: this register (by capping it) is the fix; adding one needs a reason as good as
#: these, and the reasons here are explanations, not justifications.
UNCAPPED = {
    "cross_workspace.py":
        "lateral read across workspaces; names an agent but gates on the "
        "caller-supplied grade (MCP tool `cross_workspace_read`, default L2)",
    "workspace_orchestrate.py":
        "companion dispatch planning; same shape — named agent, caller-supplied "
        "grade (MCP tool `workspace_orchestrate`, default L2)",
    "workflows.py":
        "per-step dispatch gate; the grade comes from the workflow spec's step "
        "or the run's dispatch grade, neither of which is checked against the "
        "acting agent's Breaker state",
    "adapters/norm.py":
        "obligation scheduler adapter; agent is named (`obligation-scheduler`) "
        "and the grade is a constructor argument",
    "_quarantine/obligation_scheduler.py":
        "quarantined module, not on any live path; retained so quarantine does "
        "not become a way to keep an uncapped site off this register",
}


def _gate_importers() -> dict[str, ast.Module]:
    """Modules importing ``gate`` from ``action_gate`` — the GO producers.

    Import-level rather than call-level on purpose: a module that pulls in the
    gate is on the hook for capping, wherever in its body it calls.
    """
    found: dict[str, ast.Module] = {}
    for path in sorted(WORKSPACES.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                       # pragma: no cover - not our file
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom) and node.module
                    and node.module.split(".")[-1] == "action_gate"
                    and any(a.name == "gate" for a in node.names)):
                found[str(path.relative_to(WORKSPACES))] = tree
                break
    return found


def _consults_a_cap(tree: ast.Module) -> bool:
    return any(isinstance(n, ast.Name) and n.id in _CAP_MARKERS
               or isinstance(n, ast.Attribute) and n.attr in _CAP_MARKERS
               or isinstance(n, ast.alias) and n.name in _CAP_MARKERS
               for n in ast.walk(tree))


# --- the closure ------------------------------------------------------------------

def test_the_set_of_uncapped_gate_sites_is_exactly_the_register():
    """A new uncapped path fails here rather than being noticed after an incident."""
    importers = _gate_importers()
    assert importers, "found no gate importers — the scan is broken, not the code"

    uncapped = {m for m, tree in importers.items() if not _consults_a_cap(tree)}
    new = uncapped - set(UNCAPPED)
    fixed = set(UNCAPPED) - uncapped

    assert not new, (
        "new path to a GO with no Breaker cap: "
        + ", ".join(sorted(new))
        + ". Cap the grade against the acting agent's Breaker state, or add it to "
          "UNCAPPED with a reason.")
    assert not fixed, (
        "these now cap and should leave UNCAPPED: " + ", ".join(sorted(fixed)))


def test_at_least_one_path_actually_caps():
    """Guards the guard: a scan that found nothing would pass the test above."""
    importers = _gate_importers()
    capped = {m for m, tree in importers.items() if _consults_a_cap(tree)}
    assert {"governance.py", "oversight.py"} <= capped


def test_every_register_entry_carries_a_reason():
    for module, why in UNCAPPED.items():
        assert why.strip(), module
        assert len(why) > 40, f"{module}: a reason, not a label"


def test_the_register_names_real_modules():
    # An entry for a module that no longer exists would silently mask a new one
    # of the same name.
    for module in UNCAPPED:
        assert (WORKSPACES / module).exists(), module


# --- the cap does what the register assumes ------------------------------------------

def test_a_quarantined_agent_is_capped_to_interactive():
    from rvnd.breaker import cap_grade
    assert cap_grade("L4", "L0") == "L0"


def test_capping_only_lowers():
    from rvnd.breaker import cap_grade
    for asked in ("L0", "L1", "L2", "L3", "L4"):
        for cap in ("L0", "L1", "L2", "L3", "L4"):
            assert cap_grade(asked, cap) <= max(asked, cap)
            assert cap_grade(asked, cap) == min(asked, cap)


def test_an_absent_breaker_does_not_lower_anything():
    # Documenting the fail-open seam rather than asserting it is fine: `assess`
    # defaults `breaker=None`, so a caller that forgets one gets RUNNING. That is
    # the same hole as an uncapped call site, one level up, and it is why the
    # register above is a baseline rather than a clean bill.
    from rvnd.action_gate import ActionRequest
    from rvnd.oversight import assess
    out = assess(ActionRequest(agent="a", action_class="read",
                               autonomy_grade="L4", footprint=()))
    assert out.breaker_state == "RUNNING"


# --- the other side of the same property -----------------------------------------------

def test_the_guardian_invariants_are_still_present():
    # This test pins that a GO cannot be reached around the human's power. Its
    # sibling pins that the power cannot be removed. Neither is sufficient alone,
    # so a deletion of either should be visible from the other.
    sibling = pathlib.Path(__file__).with_name("test_guardian_invariants.py")
    assert sibling.exists()
    text = sibling.read_text(encoding="utf-8")
    assert "ROOT KEY UN-GATEABLE" in text
