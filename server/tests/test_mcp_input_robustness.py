# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RV-18: MCP input robustness + a golden tool-schema snapshot.

Two gaps the register named: hostile-input coverage was one empty-query
test, and there was no snapshot to catch a breaking tool-schema change
between releases.

1. Transport robustness. The app bridge (``serve._facade_call``) is the
   shipping path — the browser POSTs ``{tool, args}`` to it. Its contract is
   "never a 500 crash; every result is a dict". This sweeps every declared
   tool x a fixed corpus of adversarial inputs (oversized scalars, deep
   nesting, wrong types, unhashable ``op``) through that boundary and asserts
   the contract holds. Deterministic — fixed corpus, no randomness.

   Scope note: this asserts the guarantee at the boundary that ships. A
   direct in-process caller of a raw facade can still surface a raised
   TypeError on some type-confused inputs (e.g. a non-string folder path);
   that is defense-in-depth, not the transport contract, and is tracked as
   an RV-18 residual. The one shared pre-dispatch seam
   (``_require_op_params``) is hardened against an unhashable ``op`` here.

2. Golden schema snapshot. ``docs/evidence/mcp-surface-baseline.json`` freezes
   the declared tools and, per op facade, each op's required-param list. A
   tool/op added or removed, or a required param changed, fails this until the
   baseline is regenerated deliberately — the review checkpoint for a breaking
   API change. Distinct from ``surface-baseline.json`` (UI-op reachability):
   this is the MCP *contract* shape, that is UI coverage.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "app"))   # serve.py lives under app/, like test_deploy_bind.py
import serve  # noqa: E402
import workspaces.mcp_server as S  # noqa: E402
from workspaces.mcp_server import _DECLARED_TOOLS, _require_op_params  # noqa: E402

BASELINE = REPO / "docs" / "evidence" / "mcp-surface-baseline.json"


def _deep(n: int):
    d: object = "bottom"
    for _ in range(n):
        d = {"k": d}
    return d


_BIG = "A" * 1_000_000
_ADVERSARIAL_ARGS = [
    {},
    {"op": 123, "params": {"folder_context": "/x"}},
    {"op": {"nested": "dict"}, "params": {}},
    {"op": ["snapshot"], "params": {}},
    {"op": "snapshot", "params": "not-a-dict"},
    {"op": "snapshot", "params": {"folder_context": 99}},
    {"op": "snapshot", "params": {"folder_context": _BIG}},
    {"op": "snapshot", "params": {"folder_context": {"nested": "dict"}}},
    {"op": "snapshot", "params": {"extra": _deep(200)}},
    {"op": _BIG, "params": {}},
    {"params": {"folder_context": {"n": "d"}}},
]


@pytest.mark.parametrize("tool", sorted(_DECLARED_TOOLS))
def test_bridge_never_crashes_on_adversarial_input(tool):
    for args in _ADVERSARIAL_ARGS:
        r = serve._facade_call(tool, args)
        assert isinstance(r, dict), (
            f"bridge returned {type(r).__name__} for {tool} on {args!r} — "
            "the transport contract is a dict for every call")


def test_require_op_params_tolerates_unhashable_op():
    # regression for the shared pre-dispatch seam: an unhashable op must not
    # raise on the required_map.get(op) lookup.
    assert _require_op_params({"snapshot": ["folder_context"]}, {"x": 1}, {}) is None
    assert _require_op_params({"snapshot": ["folder_context"]}, ["snapshot"], {}) is None
    assert _require_op_params({"snapshot": ["folder_context"]}, 123, {}) is None


def _live_surface() -> dict:
    surface = {"schema": "mcp-surface-1", "tools": {}}
    for t in sorted(_DECLARED_TOOLS):
        fn = getattr(S, t, None)
        entry: dict = {}
        if fn and "op" in inspect.signature(fn).parameters:
            h = fn("help")
            if isinstance(h, dict) and isinstance(h.get("ops"), list):
                entry["ops"] = {o["op"]: sorted(o.get("required", []))
                                for o in h["ops"] if "op" in o}
        else:
            entry["standalone"] = True
        surface["tools"][t] = entry
    return surface


def test_declared_surface_matches_golden_snapshot():
    assert BASELINE.exists(), (
        f"missing {BASELINE.name} — regenerate it deliberately (see the module docstring)")
    golden = json.loads(BASELINE.read_text())
    live = _live_surface()
    if live != golden:
        gt, lt = set(golden["tools"]), set(live["tools"])
        msgs = []
        if lt - gt:
            msgs.append(f"new tools: {sorted(lt - gt)}")
        if gt - lt:
            msgs.append(f"removed tools: {sorted(gt - lt)}")
        for t in sorted(gt & lt):
            go, lo = golden["tools"][t].get("ops", {}), live["tools"][t].get("ops", {})
            if go != lo:
                added, removed = set(lo) - set(go), set(go) - set(lo)
                changed = {o: (go[o], lo[o]) for o in set(go) & set(lo) if go[o] != lo[o]}
                msgs.append(f"{t}: +ops {sorted(added)} -ops {sorted(removed)} "
                            f"required-changed {changed}")
        pytest.fail("MCP tool-schema drift vs golden snapshot — if intended, "
                    "regenerate docs/evidence/mcp-surface-baseline.json:\n  " +
                    "\n  ".join(msgs))
