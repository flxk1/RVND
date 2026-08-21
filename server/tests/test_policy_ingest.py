# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Golden fixture for deterministic policy text to digital twin.

The cue-extraction, classification, patch-validation, netlist-roundtrip and
residual-backstop unit tests moved with the compiler to ``loomground-ingest``.
What stays in the RVND host is the MCP facade/surface test, which owns the
operator surface, plus the shared golden ``POLICY``/``EXPRESS`` fixtures that
the judgment-quarantine test imports.
"""
from __future__ import annotations

import os

from rvnd import mcp_server as M

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

POLICY = """Automated hiring decisions must be reviewed by a compliance officer.
The AI system shall not use biometric categorisation of individuals.
Users must be informed when they are interacting with the AI system.
Individuals may appeal an automated decision and request human review.
All decisions must be logged and retained for two years.
The autonomy level for low-risk tasks is set by the deployment team."""

EXPRESS = [
    "reserve automated_hiring_decision by compliance_officer",
    "prohibit use_biometric_categorisation_of_individual",
    "obligation ai-interaction-disclosure on ai_interaction",
    "redress automated_decision by appeals",
]


def test_facade_op_and_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    ws = str(tmp_path / "org"); os.makedirs(ws)
    r = M.workspace_workflow(op="policy_ingest", params={"folder_context": ws, "policy_text": POLICY})
    assert r["ok"] and r["classification"]["express"] == EXPRESS and r["applied"] is False
    assert len(M._DECLARED_TOOLS) == 24
