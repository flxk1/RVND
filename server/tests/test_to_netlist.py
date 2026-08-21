# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""to_netlist must be a faithful serializer (critic-panel P4 finding): it may not
silently drop grant kinds/risks, reserve duration, or node party/name."""
from __future__ import annotations

from rvnd import loomground_lang as L


def test_roundtrip_rich_patch():
    patch = {
        "nodes": [
            # lead is a declared human: a delegator must be a declared actor or human,
            # and a human delegator constrains no grant (§6)
            {"id": "lead", "class": "human"},
            {"id": "bot", "class": "actor", "party": "vendor", "on_behalf_of": "lead"},
            {"id": "alice", "class": "human", "role": "legal", "name": "Alice Q. Example"},
            {"id": "decide", "class": "gate", "risk_floor": "high", "party": "controller"},
        ],
        "grants": [{"gate": "decide", "actor": "bot", "kinds": ["loans"], "risks": ["high", "critical"]}],
        "cords": [{"from": "bot", "to": "decide"}, {"from": "decide", "to": "master"}],
        "reservations": [{"kind": "loans", "by": "legal", "when": "risk >= high",
                          "duration": "30d", "on_elapse": "halt"}],
        "redress": [{"kind": "loans", "by": "appeals", "overturn": True, "within": "14d"}],
    }
    assert L.validate(patch)["ok"], L.validate(patch)["errors"]
    net = L.to_netlist(patch)
    # the previously-dropped fields are emitted
    assert "on-behalf-of lead" in net and "party vendor" in net
    assert "name Alice Q. Example" in net          # human name runs to end of line
    assert "grant bot[loans:high,critical]" in net
    assert "duration 30d : halt" in net
    assert "within 14d" in net and "overturn" in net
    # round-trip: parse → validate → project is stable
    rp = L.parse(net)
    assert L.validate(rp)["ok"], L.validate(rp)["errors"]
    assert L.project(rp) == L.project(patch)
