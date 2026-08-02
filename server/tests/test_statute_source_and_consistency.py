# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""G3 + G4 (2026-06-25, six-scenario plan):

G3 — the statute ANCHOR: when an ingested policy NAMES a statute, that citation is
carried on the reservation as its `source`, attributed to the user's policy — Rvnd
never asserts a statute of its own, and never fabricates one absent from the text.
(Rvnd-layer enrichment; the vendored Loomground policy_ingest is untouched.)

G4 — display == enforcement: the graph verdict a user SEES and the run-path
disposition operate() ENFORCES must agree, so the C1-class drift (graph said
prohibited while operate auto-proceeded) can never come back.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from workspaces import mcp_server as M


@pytest.fixture
def ws(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    f = tmp_path / "w"; f.mkdir()
    M.workspace_workspace("add", {"folder_context": str(f)})
    return str(f)


# ---- G3: statute anchor (attributed, not asserted) --------------------------

def test_named_statute_becomes_the_reservation_source(ws):
    """A policy that NAMES a statute → the reservation carries it as source."""
    twin = M.workspace_workflow("policy_ingest", {"policy_text":
        "High-risk AI must be authorized by the ai-oversight officer per the "
        "EU AI Act Article 14."})
    res = (twin.get("patch") or {}).get("reservations") or []
    assert res, "policy_ingest extracted no reservation to anchor"
    assert any(r.get("source") and "AI Act" in r["source"] for r in res), \
        f"named statute not anchored as source: {res}"


def test_no_statute_named_means_no_fabricated_source(ws):
    """Rvnd must not invent a citation the policy never made (attributed != asserted)."""
    twin = M.workspace_workflow("policy_ingest", {"policy_text":
        "Refund approvals must be reviewed by a manager."})
    res = (twin.get("patch") or {}).get("reservations") or []
    assert res, "expected a reservation from the refund sentence"
    assert all(not r.get("source") for r in res), \
        f"a source was fabricated with no statute in the text: {res}"


def test_hyphenated_role_still_anchors_the_statute(ws):
    """Loop fix B1/B2: a hyphenated role/kind must still match the naming sentence
    (hyphens normalized to spaces both sides)."""
    twin = M.workspace_workflow("policy_ingest", {"policy_text":
        "Cross-border transfer must be approved by the data-protection-officer under "
        "GDPR Article 44."})
    res = (twin.get("patch") or {}).get("reservations") or []
    assert res, "no reservation extracted"
    assert any(r.get("source") and "GDPR" in r["source"] for r in res), \
        f"hyphenated role failed to anchor the statute: {res}"


def test_ambiguous_statutes_in_one_sentence_are_not_misattributed(ws):
    """Loop fix B4: when a matched sentence names TWO statutes, leave the source
    unset (under-attribute) rather than guess one."""
    twin = M.workspace_workflow("policy_ingest", {"policy_text":
        "Profiling must be approved by the officer under GDPR Article 22 and AI Act "
        "Article 14."})
    res = (twin.get("patch") or {}).get("reservations") or []
    assert all(not r.get("source") for r in res), \
        f"a statute was guessed from an ambiguous (two-statute) sentence: {res}"


def test_authored_reserve_says_it_cites_nothing(ws):
    """A reserve authored directly in a .lg patch cites nothing — the basis says
    so plainly (no fake 'ingested policy' / 'by law')."""
    M.workspace_policy("party_register", {"folder_context": ws, "party_id": "bot", "kind": "agent", "actor": "x"})
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman boss role approver\ngate ship risk high grant bot\n"
        "cord bot -> ship\ncord ship -> master\nreserve ship by boss\n"})
    from workspaces.use_case import get_use_case
    import os
    uc = get_use_case(ws, "ship", log_root=os.environ["WORKSPACE_L0_LOG_ROOT"])
    src = (uc.get("reserved_acts") or [{}])[0].get("source", "")
    assert "authored" in src.lower(), f"authored reserve should say it cites nothing, got {src!r}"


# ---- G4: display == enforcement ---------------------------------------------

def _graph_verdict(ws, uc):
    v = M.workspace_workflow("governance_graph", {"folder_context": ws}).get("verdicts", {})
    return (v.get(f"uc:{uc}") or {}).get("verdict")


def _run_disposition(ws, uc, agent, itype):
    r = M.workspace_workflow("operate", {"folder_context": ws, "use_case_id": uc, "agent_id": agent,
        "issues": [{"issue_id": "i1", "issue_type": itype, "completeness": "high"}],
        "now_epoch": 1_750_000_000})
    if r.get("final") == "refused":
        return "refused"
    return (r.get("steps") or [{}])[0].get("disposition")


def test_prohibited_display_matches_refused_enforcement(ws):
    M.workspace_policy("party_register", {"folder_context": ws, "party_id": "bot", "kind": "agent", "actor": "x"})
    M.workspace_workflow("use_case_register", {"folder_context": ws, "use_case_id": "danger",
        "name": "d", "fingerprint": {"issue_type": "danger"}, "risk": "low",
        "allowed_agents": ["bot"], "prior_approvals": 20, "actor": "x"})
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\ngate danger risk low grant bot\ncord bot -> danger\n"
        "cord danger -> master\nprohibit danger\n"})
    assert _graph_verdict(ws, "danger") == "prohibited"
    assert _run_disposition(ws, "danger", "bot", "danger") == "refused"


def test_reserved_display_matches_reserved_enforcement(ws):
    M.workspace_policy("party_register", {"folder_context": ws, "party_id": "bot", "kind": "agent", "actor": "x"})
    M.workspace_policy("party_register", {"folder_context": ws, "party_id": "chief", "kind": "human", "actor": "x"})
    M.workspace_workflow("patch_apply", {"folder_context": ws, "actor": "x", "netlist":
        "actor bot\nhuman chief role approver\ngate launch risk high grant bot\n"
        "cord bot -> launch\ncord launch -> master\nreserve launch by chief\n"})
    # reserved resolves on the run (unlike prohibited, which is structural): the
    # run-path must reserve, and the graph the user then sees must agree.
    assert _run_disposition(ws, "launch", "bot", "launch") == "reserved"
    assert _graph_verdict(ws, "launch") == "reserved"
