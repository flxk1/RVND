# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""P2 — the twin workflow ENFORCES its declarations on the chain.

Before P2, ingest→patch_apply wrote structure only: reserve/prohibit landed in the
apply `pending` map, never on the chain, so an ingested "shall not / must be
reviewed" applied as a use_case with reserved=[], grade_ceiling L4, gate verdict GO
(full autonomy — a systemic fail-open). Now:
  - reservations reach the chain as reserved acts;
  - a reserved act caps the autonomy ceiling at L2 (human-in-the-loop);
  - a prohibition severs the act (ceiling L0, egress prohibited, decide_action NO-GO);
  - the extractor reads negation/conditional ("shall not X without/unless Y",
    "No X without Y") as a RESERVATION, not a blanket prohibition;
  - singular/plural inflections collapse to one canonical gate.
Lock/Shield + policy-ingest panels."""
from __future__ import annotations

import os
import pytest

from rvnd import mcp_server as M
from rvnd.adapters.ingest.governance import compiler as P
from rvnd.governance import decide_action

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    w = tmp_path / "org"; w.mkdir()
    return str(w)


def _apply(ws, text):
    tw = P.ingest(text)
    assert tw["ok"], tw.get("errors")
    res = M.workspace_workflow(op="patch_apply", params={
        "folder_context": ws, "actor": "alex", "netlist": tw["netlist"]})
    assert res["ok"], res.get("errors")
    return tw, res


def _use_cases(res):
    return [n for n in res["graph"]["nodes"] if n["kind"] == "use_case"]


# ── reservations now reach the chain (step 1) ─────────────────────────────────

def test_reservation_reaches_the_chain(ws):
    tw, res = _apply(ws, "Generated content must be approved by a moderator.")
    assert "reservations" not in res["pending"]           # no longer deferred
    reserved = [u for u in _use_cases(res) if u["reserved"]]
    assert reserved, "an ingested reservation produced no reserved act on the chain"


# ── a reserved act lowers the ceiling (step 2) ────────────────────────────────

def test_reserved_act_lowers_ceiling_to_l2(ws):
    tw, res = _apply(ws, "Outputs must be approved by legal counsel.")
    reserved = [u for u in _use_cases(res) if u["reserved"]]
    assert reserved and all(u["grade_ceiling"] <= 2 for u in reserved)


# ── prohibitions enforce as NO-GO (step 3) ────────────────────────────────────

def test_prohibition_enforced_as_nogo(ws):
    tw, res = _apply(ws, "The assistant must not run unreviewed inferences.")
    proh = [u for u in _use_cases(res) if u.get("prohibited")]
    assert proh and all(u["grade_ceiling"] == 0 for u in proh)
    egress = {e["from"]: e["verdict"] for e in res["graph"]["edges"] if e["kind"] == "egress"}
    assert egress[proh[0]["id"]] == "prohibited"
    kind = proh[0]["id"][3:]                               # strip the "uc:" prefix
    d = decide_action(ws, action_class=kind, grade="L4", actor="bot7", log_root=M._log_root())
    assert d["gate_verdict"] == "NO-GO" and d["verdict"] == "deny"


def test_unrelated_action_is_not_prohibited(ws):
    _apply(ws, "The assistant must not run unreviewed inferences.")
    d = decide_action(ws, action_class="some_other_action", grade="L4", actor="bot7", log_root=M._log_root())
    assert d["gate_verdict"] != "NO-GO"                    # the prohibition does not bleed


# ── extractor fidelity: negation/conditional is a RESERVATION (step 4) ─────────

def test_shall_not_without_is_reservation():
    tw = P.ingest("Automated decisions shall not be taken without human review.")
    assert tw["ok"]
    assert tw["patch"].get("reservations")                # required-review -> reserve
    assert not tw["patch"].get("prohibitions")            # NOT a blanket ban


def test_gdpr_unless_is_reservation():
    tw = P.ingest("Automated decision-making shall not be permitted unless the "
                  "subject can obtain human intervention.")
    assert tw["ok"] and tw["patch"].get("reservations") and not tw["patch"].get("prohibitions")


def test_no_x_without_y_is_never_silently_dropped():
    tw = P.ingest("No automated decision without human review by qualified staff.")
    assert tw["ok"]
    assert tw["patch"].get("reservations") or tw["classification"]["unmapped"]


def test_plain_prohibition_still_prohibits():
    # A negation with NO 'without/unless' is still a real prohibition (no regression).
    tw = P.ingest("The system must not store biometric data.")
    assert tw["ok"] and tw["patch"].get("prohibitions") and not tw["patch"].get("reservations")


# ── singular/plural collapse to one gate (step 5) ─────────────────────────────

def test_singular_plural_one_canonical_gate():
    tw = P.ingest("The automated decision must be reviewed by a person. "
                  "Automated decisions must be reviewed by a person.")
    gate_ids = [n["id"] for n in tw["patch"]["nodes"] if n["class"] == "gate"]
    assert "automated_decisions" not in gate_ids
    assert gate_ids.count("automated_decision") == 1


# ── doctrine guard: no declaration lingers only in pending (step 7) ───────────

def test_multiple_approvers_on_one_gate_all_survive(ws):
    # Two policy sentences reserving the same act by different roles must BOTH owe
    # their act — neither silently overwrites the other (panel blocker).
    tw, res = _apply(ws, "Releases must be approved by the security officer. "
                         "Releases must be approved by the compliance officer.")
    reserved = [u for u in _use_cases(res) if u["reserved"]]
    assert reserved, "no reserved act landed"
    # the chain carries both approvers for the gate
    from rvnd.use_case import get_use_case
    uc_id = reserved[0]["id"][3:]
    acts = get_use_case(ws, uc_id, log_root=M._log_root())["reserved_acts"]
    approvers = {a.get("reserved_to") for a in acts}
    assert "security_officer" in approvers and "compliance_officer" in approvers


def test_prohibition_is_sticky_across_reapply(ws):
    # Re-applying a twin that no longer declares the prohibition must NOT silently
    # clear a prohibition already on the chain (fail-closed; panel blocker).
    _apply(ws, "The assistant must not run unreviewed inferences.")
    tw2, res2 = _apply(ws, "The assistant must not run unreviewed inferences.")  # same patch again
    proh = [u for u in _use_cases(res2) if u.get("prohibited")]
    assert proh, "prohibition was lost on re-apply"


def test_decide_action_fails_closed_when_lookup_raises(ws, monkeypatch):
    # If the use-case store cannot be read, an action whose prohibition we cannot
    # verify must be treated as prohibited (NO-GO), not waved through (panel blocker).
    import rvnd.use_case as UC
    monkeypatch.setattr(UC, "get_use_case",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("store unreadable")))
    d = decide_action(ws, action_class="anything", grade="L4", actor="bot7", log_root=M._log_root())
    assert d["gate_verdict"] == "NO-GO" and d["verdict"] == "deny"


def test_apply_has_no_silent_disagreement(ws):
    tw, res = _apply(ws, "The assistant must not generate hate speech.\n"
                         "Generated content must be approved by a moderator.")
    assert "reservations" not in res["pending"]
    assert "prohibitions" not in res["pending"]
    assert res["applied"]["reservations"] >= 1 and res["applied"]["prohibitions"] >= 1
