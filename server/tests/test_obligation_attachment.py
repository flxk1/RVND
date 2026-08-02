# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Loomground SPEC §159 — the master releases an `auto` action only if every egress
obligation is ATTACHED (declared on a gate that actually egresses to the master). An
obligation whose attachment point never reaches the boundary cannot be borne, so the
boundary withholds (fail-closed). Closes the audit's "obligations_attached" gap (the
reference engine had hardcoded it True). Loomground / visual-editors panel."""
from __future__ import annotations

import workspaces.loomground_lang as L

_BASE = (
    "actor bot\n"
    "gate g_mid risk low grant bot\n"
    "gate g_end risk low grant bot\n"
    "cord bot -> g_mid\n"
    "cord g_mid -> g_end\n"      # pipe: g_mid is interior, not an egress gate
    "cord g_end -> master\n"     # egress: only g_end reaches the master
)
_TRANSPORT = {"activations": [{"token": {"kind": "draft", "risk": "low"}, "source": "g_mid"}]}


def _master(extra: str):
    patch = L.parse(_BASE + extra)
    assert L.validate(patch)["ok"]
    return L.evaluate(patch, _TRANSPORT)["g_end"]["master"]


def test_no_obligation_auto_releases():
    assert _master("") == "act"


def test_obligation_on_egress_gate_is_attached_and_releases():
    # the obligation sits on the gate that egresses → attached → release.
    assert _master("obligation ai-interaction-disclosure on g_end\n") == "act"


def test_obligation_on_interior_gate_is_unattached_and_withholds():
    # the obligation sits on an interior gate that never egresses → unattached →
    # the master withholds even though the effective verdict is auto (fail-closed).
    assert _master("obligation ai-interaction-disclosure on g_mid\n") == "withhold"
