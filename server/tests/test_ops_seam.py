# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""ADR-0004 Phase 1: the four migrated ops resolve through the registry (not a
lexical branch), help_for round-trips their entries verbatim, a duplicate
(facade, op) fails closed at assembly, and the registry fails closed when the
in-tree rvnd-core provider is not discovered."""
from __future__ import annotations

import pytest

import rvnd.mcp_server as m
import rvnd.ops_seam as ops_seam
from rvnd.ops_seam import OpBundle, OpSpec, Registry, load_registry

MIGRATED = ["connected_agents", "connected_agents_governance",
            "session_governance", "transport_audit"]


@pytest.mark.parametrize("op", MIGRATED)
def test_op_resolves_via_registry_not_a_branch(op):
    spec = load_registry().lookup("workspace_workflow", op)
    assert spec is not None, f"{op} does not resolve through the registry"
    # the handler is the seam wrapper in _ops_core, proving dispatch no longer
    # rides a lexical `if op ==` branch in mcp_server
    assert spec.handler.__module__ == "rvnd._ops_core"


def test_help_for_round_trips_the_four_entries_verbatim():
    live = {e["op"]: e for e in m.workspace_workflow(op="help")["ops"]}
    seam = {e["op"]: e for e in load_registry().help_for("workspace_workflow")}
    for op in MIGRATED:
        assert op in seam
        # identical dicts AND identical key sets (mutates/optional presence is
        # part of the byte-identical help contract)
        assert seam[op] == live[op]
        assert list(seam[op].keys()) == list(live[op].keys())


def test_duplicate_facade_op_fails_closed_at_assembly():
    def _h(p, host):
        return {}
    dup = OpSpec("f", "x", _h)
    with pytest.raises(ValueError, match="duplicate op"):
        Registry([OpBundle("a", specs=[dup]), OpBundle("b", specs=[dup])])


def test_registry_fails_closed_when_core_provider_absent(monkeypatch):
    """The confirmed stale-metadata footgun: a missing entry point would SILENTLY
    drop the core ops AND the clientInfo/presence lifecycle hooks. load_registry()
    must instead RAISE. Rebuild from a cleared memo so it re-runs discovery, and
    monkeypatch discovery to omit rvnd-core (simulating the missing entry point)."""
    monkeypatch.setattr(ops_seam, "_REGISTRY", None)
    monkeypatch.setattr(ops_seam, "load_plugins", lambda: [])
    with pytest.raises(RuntimeError, match="rvnd-core"):
        load_registry()
    # a non-core-only composition is still refused (guard keys on provider_id)
    monkeypatch.setattr(ops_seam, "_REGISTRY", None)
    monkeypatch.setattr(
        ops_seam, "load_plugins",
        lambda: [OpBundle("some-plugin", specs=[OpSpec("f", "y", lambda p, h: {})])])
    with pytest.raises(RuntimeError, match="rvnd-core"):
        load_registry()
