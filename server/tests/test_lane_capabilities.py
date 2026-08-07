# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""lane_capabilities: schema, lane scoping, fail-closed reads, provenance
stamping, the admission handshake — and THE fence: preview == enforcement.

The property test at the bottom is the point of the verb: for every candidate
(kind, risk, actor) the verdict the projection reports MUST equal the terminal
effective verdict the enforcement evaluator disposes for the same inputs.
Any change that lets the two drift fails here."""
from __future__ import annotations

import pytest

from workspaces.adapters.solver.loomground import (
    RISKS,
    VERDICTS,
    evaluate_log,
    grade_meets,
)
from workspaces.governance_graph import governance_graph
from workspaces.governance_lane import GovernanceLane, register_lane
from workspaces.lane_capabilities import SCHEMA_KIND, lane_capabilities, preview_patch
from workspaces.operations import AUTO_GRADE_MIN
from workspaces.parties import register_party
from workspaces.use_case import register_use_case

# The lane-declared kind the folder never wires (projects default-deny).
GHOST = "ghost_kind"


def _uc(folder, log, uid, *, agents, prior=0, reservations=None, prohibited=None):
    register_use_case(
        folder, use_case_id=uid, name=uid, fingerprint={"issue_type": uid},
        risk="low", allowed_agents=agents, actor="controller",
        prior_approvals=prior, policy_reservations=reservations,
        prohibited=prohibited, log_root=log)


@pytest.fixture()
def governed(tmp_path, monkeypatch):
    """A folder whose lanes + gates exercise every verdict symbol."""
    folder, log = str(tmp_path / "workspace"), str(tmp_path / "log")
    (tmp_path / "workspace").mkdir()
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    register_party(folder, "bot", "agent", grade="L2", log_root=log)
    register_party(folder, "rival", "agent", grade="L4", log_root=log)
    register_lane(folder, GovernanceLane(
        lane_id="lane-bot", agent="bot", max_grade="L2",
        action_classes=("classify", "pii_export", "publish_note",
                        "forbidden", "novice", GHOST),
        folder=folder, policy_fingerprint="sha256:approved",
        approved_by="controller", rationale="bounded worker"), log_root=log)
    register_lane(folder, GovernanceLane(
        lane_id="lane-rival", agent="rival", max_grade="L4",
        action_classes=("other_uc",),
        folder=folder, policy_fingerprint="sha256:rival",
        approved_by="controller", rationale="second lane"), log_root=log)
    # hardened + unguarded -> auto at every tier (collapses flat)
    _uc(folder, log, "classify", agents=["bot"], prior=10)
    # hardened but conditionally reserved -> auto low/medium, reserved high+
    _uc(folder, log, "pii_export", agents=["bot"], prior=10, reservations={
        "pii_export": {"reserved_to": "privacy-officer", "act_type": "approve",
                       "source": "company policy", "when": "risk >= high"}})
    # unconditional reservation -> reserved at every tier
    _uc(folder, log, "publish_note", agents=["bot"], reservations={
        "publish_note": {"reserved_to": "editor", "act_type": "approve",
                         "source": "company policy"}})
    # severed act -> prohibited at every tier
    _uc(folder, log, "forbidden", agents=["bot"], prior=10, prohibited=True)
    # unhardened -> grade below the auto threshold -> human at every tier
    _uc(folder, log, "novice", agents=["bot"])
    # wired to the OTHER agent only -> default-deny (refused) for bot
    _uc(folder, log, "other_uc", agents=["rival"], prior=10)
    return folder, log


def _flat_or_cell(entry, risk):
    """A capability entry's cell for one risk tier (collapsed or expanded)."""
    if "by_risk" in entry:
        return entry["by_risk"][risk]
    return {k: entry[k] for k in ("verdict", "grade_required",
                                  "escalation", "guard")}


def _by_kind(out):
    return {e["kind"]: e for e in out["capabilities"]}


def test_schema_shape_and_provenance_stamp(governed):
    folder, log = governed
    out = lane_capabilities(folder, "bot", log_root=log)
    assert out["ok"] is True and out["kind"] == SCHEMA_KIND
    assert out["advisory"] is True and out["readable"] is True
    assert out["actor"] == "bot"
    # the risk axis is the governance vocabulary, never re-declared
    assert out["risk_axis"] == list(RISKS)
    prov = out["provenance"]
    assert prov["policy_fingerprint"] == "sha256:approved"  # the lane binding
    assert prov["lane_id"] == "lane-bot" and prov["lane_version"] == 1
    assert prov["max_grade"] == "L2" and prov["language_version"]
    assert prov["derived_at"] > 0 and "projection" in prov["source"]
    # candidate space = lane kinds ∪ wired kinds — no silent blanks
    assert set(_by_kind(out)) == {"classify", "pii_export", "publish_note",
                                  "forbidden", "novice", GHOST, "other_uc"}
    for entry in out["capabilities"]:
        cells = [_flat_or_cell(entry, r) for r in out["risk_axis"]]
        for cell in cells:
            assert cell["verdict"] in VERDICTS
            assert set(cell) == {"verdict", "grade_required",
                                 "escalation", "guard"}


def test_projected_dispositions_and_attribution(governed):
    folder, log = governed
    caps = _by_kind(lane_capabilities(folder, "bot", log_root=log))
    # auto everywhere -> collapsed flat, stamped with the gate's required grade
    assert "by_risk" not in caps["classify"]
    assert caps["classify"]["verdict"] == "auto"
    assert caps["classify"]["grade_required"] == f"L{AUTO_GRADE_MIN}"
    assert caps["classify"]["escalation"] is None
    # the conditional reserve varies by tier -> expanded, guard verbatim
    pii = caps["pii_export"]["by_risk"]
    assert pii["low"]["verdict"] == "auto" and pii["medium"]["verdict"] == "auto"
    for tier in ("high", "critical"):
        assert pii[tier]["verdict"] == "reserved"
        assert pii[tier]["escalation"] == "privacy-officer"
        assert pii[tier]["guard"] == (
            "reserve pii_export by privacy-officer when risk >= high")
    # unconditional reserve -> flat reserved, routed to its competence
    assert caps["publish_note"]["verdict"] == "reserved"
    assert caps["publish_note"]["escalation"] == "editor"
    # severed -> prohibited, escalation severed
    assert caps["forbidden"]["verdict"] == "prohibited"
    assert caps["forbidden"]["escalation"] == "severed"
    # autonomy not earned -> human-in-the-loop
    assert caps["novice"]["verdict"] == "human"
    assert caps["novice"]["escalation"] == "human-in-the-loop"
    # no authority cord -> default-deny; wired but out of lane
    assert caps["other_uc"]["verdict"] == "refused"
    assert caps["other_uc"]["in_lane"] is False
    assert "default-deny" in caps["other_uc"]["guard"]
    # lane-named but unwired -> still projected, refused, in lane
    assert caps[GHOST]["verdict"] == "refused"
    assert caps[GHOST]["in_lane"] is True and caps[GHOST]["wired"] is False


def test_lane_scoping_per_actor(governed):
    folder, log = governed
    bot = _by_kind(lane_capabilities(folder, "bot", log_root=log))
    rival_out = lane_capabilities(folder, "rival", log_root=log)
    rival = _by_kind(rival_out)
    # each agent reads ITS lane's binding, not the other's
    assert rival_out["provenance"]["policy_fingerprint"] == "sha256:rival"
    assert rival_out["provenance"]["lane_id"] == "lane-rival"
    # the same wired kind projects per-agent: authority is per actor
    assert rival["other_uc"]["verdict"] == "auto"
    assert rival["other_uc"]["in_lane"] is True
    assert bot["other_uc"]["verdict"] == "refused"
    # bot's gates are out-of-lane, unauthorized kinds for rival
    assert rival["classify"]["in_lane"] is False
    assert rival["classify"]["verdict"] == "refused"


def test_fail_closed_never_all_allowed(governed, monkeypatch):
    folder, log = governed
    # no active lane -> no capabilities
    out = lane_capabilities(folder, "stranger", log_root=log)
    assert out["ok"] is False and out["capabilities"] == []
    assert out["reason"] == "no active governance lane"
    # unreadable chain -> readable:false, no capabilities, never 'auto'
    def _boom(*a, **k):
        raise OSError("chain unreadable")
    monkeypatch.setattr("workspaces.lane_capabilities.get_lane", _boom)
    out = lane_capabilities(folder, "bot", log_root=log)
    assert out["ok"] is False and out["readable"] is False
    assert out["capabilities"] == []
    assert "policy unreadable" in out["reason"] and "OSError" in out["reason"]


def test_risk_axis_is_vocabulary_gated(governed):
    folder, log = governed
    out = lane_capabilities(folder, "bot", risks=["high"], log_root=log)
    assert out["risk_axis"] == ["high"]
    # an axis of unknown tiers fails closed rather than guessing
    out = lane_capabilities(folder, "bot", risks=["cosmic"], log_root=log)
    assert out["ok"] is False and out["capabilities"] == []


# ---------------------------------------------------------------- THE fence
def test_preview_equals_enforcement(governed):
    """Anti-drift property: for every candidate (kind, risk, actor) the
    projected verdict EQUALS the enforcement evaluator's terminal effective
    verdict for the same activation over the same compiled patch."""
    folder, log = governed
    checked = 0
    seen: set[str] = set()
    for actor in ("bot", "rival"):
        out = lane_capabilities(folder, actor, log_root=log)
        graph = governance_graph(folder, log_root=log)
        gates = {n["id"].split(":", 1)[1]: n for n in graph["nodes"]
                 if n["kind"] == "use_case"}
        for entry in out["capabilities"]:
            kind = entry["kind"]
            if kind not in gates:
                # no gate to enter: §7.1 step-(2) default-deny is the
                # enforcement disposition for an ungranted kind
                for risk in out["risk_axis"]:
                    assert _flat_or_cell(entry, risk)["verdict"] == "refused"
                    checked += 1
                seen.add("refused")
                continue
            patch = preview_patch(graph, actor, kind)
            for risk in out["risk_axis"]:
                projected = _flat_or_cell(entry, risk)["verdict"]
                token = {"id": f"t:{kind}:{risk}", "kind": kind, "risk": risk,
                         "party": actor, "provenance": [],
                         "tags": list(gates[kind].get("tags") or [])}
                trace = evaluate_log(patch, {"activations": [
                    {"source": kind, "actor": actor, "token": token}]})
                disposed = trace[-1]["verdict"]
                assert projected == disposed, (
                    f"preview/enforcement drift at ({actor}, {kind}, {risk}): "
                    f"projected {projected!r}, gate disposes {disposed!r}")
                seen.add(disposed)
                checked += 1
    # the fence exercised the whole verdict alphabet, not a happy path
    assert seen == set(VERDICTS)
    # bot: 6 lane kinds + 1 wired-only; rival: 1 lane kind + 5 wired-only
    assert checked == (7 + 6) * len(RISKS)


def test_preview_auto_split_is_the_run_path_grade_rule(governed):
    """The auto/human split mirrors operate()'s own authority: grade_meets of
    the EARNED contract grade against AUTO_GRADE_MIN — no local re-rule."""
    folder, log = governed
    caps = _by_kind(lane_capabilities(folder, "bot", log_root=log))
    graph = governance_graph(folder, log_root=log)
    gates = {n["id"].split(":", 1)[1]: n for n in graph["nodes"]
             if n["kind"] == "use_case"}
    for kind in ("classify", "novice"):        # authorized, unreserved gates
        expected = ("auto" if grade_meets(gates[kind]["grade"], AUTO_GRADE_MIN)
                    else "human")
        assert caps[kind]["verdict"] == expected


# ------------------------------------------------------- handshake + verb
def test_admission_response_carries_the_projection(governed):
    from workspaces.mcp_serving import (
        clear_request_principal,
        set_request_principal,
    )
    from workspaces.session_admission import governance_open
    folder, log = governed
    set_request_principal("bot", "bot")
    try:
        opened = governance_open(folder, party="bot",
                                 policy_fingerprint="sha256:approved",
                                 log_root=log)
    finally:
        clear_request_principal()
    caps = opened["capabilities"]
    assert caps["ok"] is True and caps["kind"] == SCHEMA_KIND
    # provably about the SAME policy the token is bound to
    assert (caps["provenance"]["policy_fingerprint"]
            == opened["claims"]["policy_fingerprint"] == "sha256:approved")
    # re-queryable mid-session: the standalone read returns the same map
    again = lane_capabilities(folder, "bot", log_root=log)
    assert again["capabilities"] == caps["capabilities"]


def test_mcp_verb_is_registered_and_read_only(governed, monkeypatch):
    from workspaces import mcp_server as M
    folder, log = governed
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", log)
    ops = {o["op"] for o in M.workspace_workflow("help")["ops"]}
    assert "lane_capabilities" in ops
    before = (governance_graph(folder, log_root=log)["summary"])
    out = M.workspace_workflow("lane_capabilities",
                               {"folder_context": folder, "actor": "bot"})
    assert out["ok"] is True and out["kind"] == SCHEMA_KIND
    assert out["provenance"]["policy_fingerprint"] == "sha256:approved"
    # read-only: the projection appends nothing to the chain
    after = (governance_graph(folder, log_root=log)["summary"])
    assert after == before
