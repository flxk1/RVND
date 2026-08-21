# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Regression lock for the MCP facade consolidation (108 → 22 tools).

Guards the two invariants the refactor must always satisfy:
  1. _DECLARED_TOOLS == the actually-registered FastMCP tool set;
  2. the surface stays consolidated and every facade self-describes + dispatches.

Skips cleanly when the `mcp` package isn't installed (same as the other
MCP-surface tests), so it never blocks the pure-logic suites.
"""

from __future__ import annotations

import asyncio
import pytest

mcp_server = pytest.importorskip("rvnd.mcp_server")

FACADES = [
    "workspace_legal", "workspace_folder", "workspace_mirror", "workspace_policy", "workspace_workflow",
    "workspace_lock", "workspace_memory", "workspace_dispatch", "workspace_ingest", "workspace_contract",
    "workspace_erase", "workspace_workspace", "workspace_audit", "workspace_model", "workspace_capture",
    "workspace_conformity",
]


def _registered() -> set:
    res = mcp_server.mcp.list_tools()
    if asyncio.iscoroutine(res):
        # asyncio.run, not get_event_loop().run_until_complete: the legacy
        # pattern breaks when another test (e.g. test_gateway) has already
        # run-and-closed a loop in this thread.
        res = asyncio.run(res)
    return {t.name for t in res}


def test_declared_equals_registered():
    """The hand-maintained _DECLARED_TOOLS must match FastMCP's registry exactly."""
    assert set(mcp_server._DECLARED_TOOLS) == _registered()


def test_surface_stays_consolidated():
    reg = _registered()
    # was 108 -> 33 -> 23 (2026-06-12 fold: standalone duplicates became
    # facade ops; remaining standalones = the governed entry points
    # workspace_ask / workspace_orchestrate / cross_workspace_read + server_info).
    # 24 (2026-07-02): +workspace_session — a genuinely new capability (env
    # save/load), not a foldable duplicate; the ceiling was raised deliberately.
    assert len(reg) <= 24, f"surface grew back to {len(reg)} tools"
    assert set(FACADES) <= reg


# 2026-06-12 fold: every removed standalone tool stays reachable as a facade
# op — the NAME left the surface, the capability did not.
FOLDED = {
    "audit_verify_chain":    ("workspace_audit", "verify_chain"),
    "workspace_shadow_scan":      ("workspace_audit", "shadow_scan"),
    "list_known_workspaces": ("workspace_workspace", "list"),
    "route_to_workspace":         ("workspace_workspace", "route"),
    "write_file_to_folder":  ("workspace_folder", "write_file"),
    "search":                ("workspace_memory", "search"),
    "by_id":                 ("workspace_memory", "pair"),
    "reason":                ("workspace_memory", "reason"),
    "recent_dispatches":     ("workspace_dispatch", "recent"),
    "workspace_cascade":          ("workspace_model", "cascade"),
}


def test_folded_tools_left_the_registered_surface():
    reg = _registered()
    assert reg & set(FOLDED) == set(), \
        f"still registered: {sorted(reg & set(FOLDED))}"


def test_folded_tools_reachable_as_facade_ops():
    """Each fold target is a real op: listed in the facade's help, and the
    python function still exists (the facade calls it)."""
    for fn_name, (facade, op) in FOLDED.items():
        assert hasattr(mcp_server, fn_name), f"{fn_name} function removed"
        help_ops = {o["op"] for o in getattr(mcp_server, facade)("help")["ops"]}
        assert op in help_ops, f"{facade} help missing op {op!r} ({fn_name})"


def test_folded_ops_dispatch(tmp_path):
    f = str(tmp_path / "workspace")
    r = mcp_server.workspace_memory("reason", {"folder_context": f})
    # reason() dispatches; on an unindexed workspace it fail-closes (Versum
    # index required) with a clean error dict rather than raising.
    assert isinstance(r, dict)
    w = mcp_server.workspace_workspace("route", {"query": "anything"})
    assert isinstance(w, dict)
    d = mcp_server.workspace_dispatch("recent", {"folder_context": f})
    assert isinstance(d, dict) and "error" not in d


def test_every_facade_self_describes():
    for f in FACADES:
        r = getattr(mcp_server, f)("help")
        assert isinstance(r, dict) and r.get("ops"), f
        assert all("op" in o and "required" in o for o in r["ops"]), f


def test_unknown_op_errors_cleanly_per_facade():
    for f in FACADES:
        r = getattr(mcp_server, f)("__no_such_op__")
        assert isinstance(r, dict) and "error" in r, f


def test_real_dispatch_through_a_facade():
    r = mcp_server.workspace_legal("select.context", {
        "entity": "acme", "legal_system": "DE", "clause_needs": ["Zahlung Tagen"],
        "corpus": [{"id": "acme:x", "text": "Zahlung 30 Tagen"},
                   {"id": "globex:x", "text": "Zahlung 14 Tagen"}]})
    assert [c["doc_id"] for c in r["clauses"]] == ["acme:x"]   # entity-scoped, no cross-contamination


def test_server_info_version_is_real_not_hardcoded():
    """server_info must report the installed package version (the 0.6.6
    string was hardcoded and survived three releases — found 2026-06-12 when
    a live server identified itself as 0.6.6 against an 0.6.8.1 tree)."""
    r = mcp_server.server_info()
    try:
        from importlib.metadata import version
        expected = version("rvnd")
    except Exception:
        expected = "unknown"
    assert r["server_version"] == expected
    assert r["server_name"] == "workspaces"
    assert r["tool_count"] == len(mcp_server._DECLARED_TOOLS) == 24
