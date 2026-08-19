# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Grounder workbench export — the read-only frontend seam.

The artifact (plugin/assets/workspace-grounder.html) renders what export_state
produces; these tests pin that contract: all five views fed, every citation
style pre-rendered, residuals surfaced not resolved, JSON-safe throughout.
"""

from __future__ import annotations

import json

from rvnd.workspace_grounder import CITATION_STYLES
from rvnd.grounder_workbench import build_demo, export_state, write_state


def _demo(tmp_path):
    build_demo(tmp_path, log_root=tmp_path / "log")
    return export_state(tmp_path, log_root=tmp_path / "log")


def test_export_feeds_all_five_views(tmp_path):
    s = _demo(tmp_path)
    assert s["meta"]["schema"] == 1
    assert s["works"] and s["claims"] and s["provenance"]["edges"]
    assert s["coverage"]["works"] == len(s["works"])
    assert s["audit"]                                   # grounding events only
    assert all(a["kind"].startswith("grounding") for a in s["audit"])


def test_every_style_prerendered_per_work(tmp_path):
    s = _demo(tmp_path)
    assert s["meta"]["styles"] == list(CITATION_STYLES)
    for w in s["works"]:
        assert set(w["citations"]) == set(CITATION_STYLES)
        assert w["citations"]["apa"]


def test_demo_covers_every_claim_state_and_signal(tmp_path):
    s = _demo(tmp_path)
    statuses = {c["status"] for c in s["claims"]}
    assert {"verified", "disputed", "asserted"} <= statuses
    assert s["coverage"]["disputed_residuals"]          # residual present
    assert s["coverage"]["claims_without_evidence"]     # gap present
    refusals = [a for a in s["audit"] if a["kind"] == "grounding-refusal"]
    assert refusals                                     # refusal audited, shown


def test_disputed_first_in_claim_order(tmp_path):
    s = _demo(tmp_path)
    assert s["claims"][0]["status"] == "disputed"       # residuals up front


def test_frontier_flags_match_provenance(tmp_path):
    s = _demo(tmp_path)
    flagged = {w["id"] for w in s["works"] if w["on_frontier"]}
    assert flagged == set(s["provenance"]["frontier"])
    ingested = [w for w in s["works"]
                if w["title"] == "Grounded Attribution for Agentic AI"]
    assert ingested and not ingested[0]["on_frontier"]  # traced by ingest
    assert ingested[0]["fixity"] is True


def test_export_is_json_safe_and_writes(tmp_path):
    build_demo(tmp_path, log_root=tmp_path / "log")
    out = tmp_path / "state" / "grounder-state.json"
    res = write_state(tmp_path, out, log_root=tmp_path / "log")
    assert res["ok"] and res["works"] >= 5
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["meta"]["tool"] == "workspace-grounder"


def test_export_empty_folder_is_valid(tmp_path):
    s = export_state(tmp_path, log_root=tmp_path / "log")
    assert s["works"] == [] and s["claims"] == []
    assert s["coverage"]["works"] == 0
    json.dumps(s)                                       # serialisable


def test_view_is_read_only_surface(tmp_path):
    """The workbench artifact gets no mutation hooks: export carries no op
    names other than documented read paths, and claims keep their ids so
    resolution happens via the MCP ops, not the view."""
    s = _demo(tmp_path)
    disputed = [c for c in s["claims"] if c["status"] == "disputed"]
    assert all(c["id"].startswith("claim:") for c in disputed)
    html = (json.dumps(s))
    assert "claim.status" not in html                   # no embedded op calls
