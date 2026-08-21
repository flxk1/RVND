# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Feature flag for the governance authoring/navigation layer.

The tests cover default-on behavior, disabled-op refusal, catalog hiding, and
unrelated workflow operations.
"""
from __future__ import annotations

import os

from rvnd import mcp_server as M

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

_LAYER = {"governance_map", "governance_chat", "governance_kg",
          "model_capability", "security_dashboard", "officer"}


def _catalog_ops():
    return {row["op"] for row in M.workspace_workflow("ops", {"folder_context": ""})["ops"]}


def test_layer_on_by_default(monkeypatch):
    monkeypatch.delenv("RVND_GOVERNANCE_LAYER", raising=False)
    assert M.workspace_workflow(
        "model_capability", {"folder_context": ""})["version"] == "model_capability/v1"
    assert _LAYER <= _catalog_ops()


def test_layer_off_refuses_and_hides(monkeypatch):
    monkeypatch.setenv("RVND_GOVERNANCE_LAYER", "off")
    d = M.workspace_workflow("governance_map", {"folder_context": "", "policy_text": "x"})
    assert d["error"] == "governance layer disabled" and d["flag"] == "RVND_GOVERNANCE_LAYER"
    ops = _catalog_ops()
    assert not (_LAYER & ops)          # none of the layer ops are advertised while off
    assert "define" in ops             # unrelated workflow ops unaffected
