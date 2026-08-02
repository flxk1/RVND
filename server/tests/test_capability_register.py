# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The supported capability register gate.

`docs/evidence/capability-register.json` classifies every declared runtime operation into
one supported/deferred/internal status. This gate keeps the register honest
against the live catalogue: its keys must be exactly the operations the runtime
declares, so a newly declared operation fails until it is classified and a
removed operation cannot linger as a stale entry. Status is a reviewed
classification; the keys are enforced, not hand-maintained.

Claims under test:
  C1  every live operation (verify_surface.build_op_inventory) has exactly one
      register entry; none is missing
  C2  no register entry names an operation the runtime no longer declares
  C3  every entry has a status drawn from the declared vocabulary and a
      non-empty basis
  C4  gateway=true holds for exactly the operations the curated gateway exposes
      (gateway.py ALLOWED_OPS plus the standalone server_info tool)
  C5  status is derived from auditable evidence, not asserted: each entry's
      recorded surface_state and route_tested match a live recomputation, and the
      status is exactly what the stated rule yields from that evidence — a
      handler that exists but has no console surface, no gateway route and no
      op-route test is deferred, never supported

Run: python -m pytest server/tests/test_capability_register.py -q
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGISTER = REPO / "docs" / "evidence" / "capability-register.json"

_spec = importlib.util.spec_from_file_location("verify_surface", REPO / "scripts" / "verify_surface.py")
vs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vs)

_VALID_STATUS = {
    "ui-supported", "mcp-supported", "gateway-supported",
    "experimental", "deferred", "internal",
}


def _live_ops() -> set[tuple[str, str | None]]:
    mcp = vs.load_mcp_module()
    rows = vs.classify_inventory(vs.build_op_inventory(mcp),
                                 vs.load_composed_html())
    return {(r["facade"], r["op"]) for r in rows}


def _register() -> list[dict]:
    return json.loads(REGISTER.read_text(encoding="utf-8"))["operations"]


def _register_keys(reg) -> set[tuple[str, str | None]]:
    return {(e["facade"], e["op"]) for e in reg}


def _transport_evidence(reg) -> dict[tuple[str, str | None], str]:
    """Return only complete, file-backed success+refusal transport proofs."""
    proven: dict[tuple[str, str | None], str] = {}
    for entry in reg:
        proof = entry.get("transport_proven")
        if not proof:
            continue
        assert isinstance(proof, dict), (
            f"{entry['facade']}.{entry['op']} transport_proven must be an object")
        channel = proof.get("channel")
        assert channel in {"ui", "gateway", "mcp"}, (
            f"{entry['facade']}.{entry['op']} has invalid transport channel {channel!r}")
        for outcome in ("success", "refusal"):
            nodeid = proof.get(outcome, "")
            assert "::test_" in nodeid, (
                f"{entry['facade']}.{entry['op']} lacks a test nodeid for {outcome}")
            rel, test_name = nodeid.split("::", 1)
            path = REPO / rel
            assert path.is_file(), f"transport evidence file missing: {rel}"
            assert f"def {test_name}(" in path.read_text(encoding="utf-8"), (
                f"transport evidence test missing: {nodeid}")
        proven[(entry["facade"], entry["op"])] = channel
    return proven


def test_every_live_operation_is_classified():                    # C1
    missing = sorted(k for k in _live_ops() - _register_keys(_register()) if True)
    assert not missing, (
        f"declared operations absent from docs/evidence/capability-register.json: {missing}. "
        "A newly declared operation must be classified before it ships.")


def test_no_stale_register_entries():                             # C2
    stale = sorted(k for k in _register_keys(_register()) - _live_ops() if True)
    assert not stale, (
        f"register classifies operations the runtime no longer declares: {stale}. "
        "Remove the entry when the operation is removed.")


def test_every_entry_has_valid_status_and_basis():                # C3
    reg = _register()
    bad_status = sorted((e["facade"], e["op"]) for e in reg
                        if e.get("status") not in _VALID_STATUS)
    assert not bad_status, f"entries with a status outside the vocabulary: {bad_status}"
    no_basis = sorted((e["facade"], e["op"]) for e in reg if not e.get("basis"))
    assert not no_basis, f"entries with no basis: {no_basis}"
    # keys must be unique
    keys = [(e["facade"], e["op"]) for e in reg]
    assert len(keys) == len(set(keys)), "duplicate (facade, op) keys in the register"


def test_supported_status_is_non_empty():
    """A release register must prove at least one caller-facing capability.

    An all-deferred register may be honest inventory, but it is not evidence of
    a usable product and makes every supported-surface reconciliation vacuous.
    """
    supported = [e for e in _register() if e["status"] in {
        "ui-supported", "mcp-supported", "gateway-supported",
    }]
    assert supported, (
        "capability register has no supported operations; add committed "
        "operation-specific success and refusal evidence over a public "
        "transport before release")


def _gateway_exposed() -> set[tuple[str, str | None]]:
    from workspaces import gateway as gw
    allowed = getattr(gw, "ALLOWED_OPS", None)
    if allowed is None:
        return None
    exposed: set[tuple[str, str | None]] = {("server_info", None)}
    for facade, ops in allowed.items():
        for op in ops:
            exposed.add((facade, op))
    return exposed


def test_gateway_flag_matches_the_curated_profile():              # C4
    exposed = _gateway_exposed()
    if exposed is None:
        import pytest
        pytest.skip("gateway.ALLOWED_OPS not importable in this environment")
    reg = _register()
    flagged = {(e["facade"], e["op"]) for e in reg if e.get("gateway")}
    only_flagged = sorted(flagged - exposed)
    only_exposed = sorted(exposed - flagged)
    assert not only_flagged, f"register marks gateway=true for non-exposed ops: {only_flagged}"
    assert not only_exposed, f"gateway exposes ops the register does not flag: {only_exposed}"


def _status_for(surface_state, gateway, route_tested, callable_, facade, op,
                transport_proven: set | None = None) -> str:
    """The classification rule. An operation is supported only when a committed
    test drives it to a validated success AND a validated refusal across its
    public transport boundary (a started MCP host over stdio, a real HTTP `/tool`
    request with bridge auth, or the gateway serving boundary) — recorded in
    ``transport_proven`` as ``(facade, op) -> channel``.

    In-process callability (``callable_``, from the coverage smoke matrix) and a
    console/gateway binding (``surface_state``/``gateway``) are recorded as facts
    but do NOT confer support: calling a Python function in-process does not cross
    the transport, so it cannot prove public callability. An operation without
    public-transport evidence is deferred, whatever its in-process behaviour.

    ``transport_proven`` is empty unless committed evidence exercises a public
    transport for both success and refusal. Success-only render or MCP checks do
    not qualify, and an untested gateway serving boundary remains deferred."""
    if (facade, op) == ("workspace_contract", "demo"):
        return "experimental"
    tp = transport_proven or {}
    channel = tp.get((facade, op))
    if channel == "ui":
        return "ui-supported"
    if channel == "gateway":
        return "gateway-supported"
    if channel == "mcp":
        return "mcp-supported"
    return "deferred"


def _coverage_callable() -> set:
    """The (facade, op) pairs the executable coverage harness proved callable —
    every claimed channel valid_ok and invalid_ok — from docs/evidence/capability-coverage-matrix.json."""
    path = REPO / "docs" / "evidence" / "capability-coverage-matrix.json"
    rows = json.loads(path.read_text())["rows"]
    from collections import defaultdict
    byop = defaultdict(list)
    for r in rows:
        byop[(r["facade"], r["op"])].append(r)
    return {k for k, rs in byop.items() if all(x["valid_ok"] and x["invalid_ok"] for x in rs)}


def route_tested(facade: str, op, blob: str) -> bool:
    """True iff a test invokes THIS ``(facade, op)`` pair through its op route.

    The op string alone is insufficient — a repeated op name like ``list`` must
    credit only the facade actually invoked. Two invocation forms are recognised,
    both binding the facade to the op within the same call:

    - direct facade call: ``workspace_folder(op="list"`` (op is the first argument);
    - bridge/dispatch call: ``"workspace_folder", {"op": "list"`` (op is the first
      key of the argument object).

    A standalone tool (``op is None``) is credited only for a call to its public
    tool route by name — a bridge ``call(..., "server_info"`` or a direct
    ``server_info(`` — not an incidental mention. Any op-fragment whose facade
    cannot be paired within the same call is not credited (fail-closed): a
    mispaired or unrecognised form leaves the route deferred, never supported."""
    import re
    f = re.escape(facade)
    if op is None:
        bridge = r'\bcall\([^)]*["\']%s["\']' % f
        direct = r'\b%s\s*\(' % f
        return bool(re.search(bridge, blob) or re.search(direct, blob))
    o = re.escape(op)
    # direct: facade( op="X"  (op the first kwarg)
    direct = r'\b%s\s*\(\s*op\s*=\s*["\']%s["\']' % (f, o)
    # bridge: "facade", { "op": "X"   (op near the opening brace, first key)
    bridge = r'["\']%s["\']\s*,\s*\{[^{}]{0,24}?\bop["\']?\s*[:=]\s*["\']%s["\']' % (f, o)
    return bool(re.search(direct, blob) or re.search(bridge, blob))


def _route_tested_live() -> dict:
    """A cached ``tested(facade, op) -> bool`` bound to the live test corpus,
    pairing each op to the facade actually invoked with it (see route_tested)."""
    blob = "\n".join(p.read_text(errors="ignore")
                     for p in (REPO / "server" / "tests").rglob("*.py"))
    cache: dict = {}

    def tested(facade, op):
        key = (facade, op)
        if key not in cache:
            cache[key] = route_tested(facade, op, blob)
        return cache[key]

    return tested


def test_status_is_derived_from_auditable_evidence():             # C5
    mcp = vs.load_mcp_module()
    rows = vs.classify_inventory(vs.build_op_inventory(mcp),
                                 vs.load_composed_html())
    live_state = {(r["facade"], r["op"]): r["state"] for r in rows}
    exposed = _gateway_exposed()
    tested = _route_tested_live()
    proven = _coverage_callable()
    transport_proven = _transport_evidence(_register())

    mismatches = []
    for e in _register():
        f, o = e["facade"], e["op"]
        # recorded evidence must match the live recomputation
        live_surface = live_state.get((f, o), "unsurfaced")
        live_route = tested(f, o)
        if e.get("surface_state") != live_surface:
            mismatches.append((f, o, f"surface_state {e.get('surface_state')} != live {live_surface}"))
            continue
        if bool(e.get("route_tested")) != live_route:
            mismatches.append((f, o, f"route_tested {e.get('route_tested')} != live {live_route}"))
            continue
        # the recorded callability must match the coverage matrix, and the status
        # must follow the rule from (surface, gateway, route, callable)
        if bool(e.get("callable")) != ((f, o) in proven):
            mismatches.append((f, o, f"callable {e.get('callable')} != matrix {(f, o) in proven}"))
            continue
        gw = (f, o) in exposed if exposed is not None else e.get("gateway")
        want = _status_for(
            live_surface, gw, live_route, (f, o) in proven, f, o,
            transport_proven,
        )
        if e["status"] != want:
            mismatches.append((f, o, f"status {e['status']} != derived {want}"))
    assert not mismatches, (
        "register status not derivable from live auditable evidence + coverage:\n  "
        + "\n  ".join(f"{f}.{o}: {why}" for f, o, why in mismatches[:25]))


def test_route_evidence_binds_op_to_its_facade():                 # C5 regression
    """A repeated op name must credit only the facade actually invoked. Two
    facades share ``list``; only workspace_folder (direct form) and
    workspace_workspace (bridge form) are invoked, so the other two stay
    deferred. An op-fragment with no pairable facade is not credited."""
    blob = (
        'r = workspace_folder(op="list", params={"path": "/x"})\n'
        'out = call(port, "workspace_workspace", {"op": "list"})\n'
        '# workspace_mirror and workspace_workflow list routes are never invoked\n'
        'stray = {"op": "list"}  # no facade paired -> must not credit anyone\n'
    )
    # the two invoked pairs are credited, by their two forms
    assert route_tested("workspace_folder", "list", blob) is True
    assert route_tested("workspace_workspace", "list", blob) is True
    # the two un-invoked pairs sharing the op name are NOT credited (the defect)
    assert route_tested("workspace_mirror", "list", blob) is False
    assert route_tested("workspace_workflow", "list", blob) is False
    # status follows public-transport evidence, not the route grep or in-process
    # callability: an op is supported only when transport_proven records its
    # channel; otherwise deferred, whatever its grep/callable/surface facts.
    tp = {("workspace_mirror", "list"): "mcp"}
    assert _status_for("unsurfaced", False, False, True, "workspace_mirror", "list", tp) == "mcp-supported"
    # callable in-process + route grep + surfaced, but no transport evidence -> deferred
    assert _status_for("surfaced", True, True, True, "workspace_folder", "list", {}) == "deferred"
    # fail-closed: a bridge fragment whose facade is a different tool does not leak
    assert route_tested("workspace_mirror", "list",
                        'call(port, "workspace_workspace", {"op": "list"})') is False


def test_standalone_route_needs_the_public_tool_call():           # C5 regression
    """A standalone tool is credited for a call to its tool route by name, not an
    incidental mention."""
    assert route_tested("server_info", None, 'call(port, "server_info")') is True
    assert route_tested("server_info", None, 'x = server_info()') is True
    assert route_tested("server_info", None, '# server_info is documented here') is False
