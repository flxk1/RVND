# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governance-layer ops validate at one boundary.

The tests cover structured value errors, malformed inputs, string dimensions,
half-specified KG paths, and typoed officer control forms.
"""
from __future__ import annotations

import os

from rvnd import mcp_server as M

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")

PROV = [{"pinpoint": "Art. 16", "text": "Providers of high-risk AI systems shall ensure that "
         "their systems undergo the relevant conformity assessment procedure."}]


def test_unknown_task_is_a_value_error():
    r = M.workspace_workflow("model_capability", {"task": "bogus"})
    assert "missing param" not in r.get("error", "")     # the old misdiagnosis
    assert "bogus" in r["error"] and "extraction" in r["error"]        # names the valid tasks


def test_bad_param_values_return_structured_errors():
    bad = [("security_dashboard", {"folder_context": "", "group_by": "bogus"}),
           ("governance_kg", {"provisions": PROV, "level": "bogus"}),
           ("governance_map", {"provisions": ["a string"]})]
    for op, params in bad:
        r = M.workspace_workflow(op, params)
        assert r.get("ok") is False and r.get("error"), f"{op} did not error structurally: {r}"


def test_kg_dimensions_string_selects_not_chars():
    g = M.workspace_workflow("governance_kg", {"provisions": PROV, "instrument": "AI Act",
                                               "level": "detail", "dimensions": "relational"})
    assert g["dimensions"] == ["relational"]             # not ['a','e','i','l','n','o','r','t']
    assert g["edges"] and all(e["dimension"] == "relational" for e in g["edges"])


def test_kg_half_a_path_query_errors():
    r = M.workspace_workflow("governance_kg", {"provisions": PROV, "from": "role:provider"})
    assert r.get("ok") is False and "from" in r["error"] and "to" in r["error"]


def test_officer_unknown_control_form_fails_closed():
    r = M.workspace_workflow("officer", {"oversees": ["gate:hiring"],
                                         "control_form": "single-approver"})   # typo'd
    assert r.get("ok") is False and "control_form" in r["error"]
