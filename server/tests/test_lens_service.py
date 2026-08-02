# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for lens_service — the JSON-boundary surface of the in-vivo Lens
(shared by the workspace_lens MCP facade and the workspaces lens CLI)."""

from __future__ import annotations

from workspaces import lens_service as ls


# --- classify: admit / hold / reject (default-deny) ---

def test_classify_admit_when_covered_and_provenanced():
    out = ls.classify({"cls": "style-pref", "content_hash": "h1",
                       "source_actor": "alex", "signature": "sig",
                       "scope": {"allow": ["style-pref"]}})
    assert out["admission"] == "admit"


def test_classify_holds_unknown_class_default_deny():
    out = ls.classify({"cls": "mystery", "content_hash": "h2",
                       "source_actor": "alex", "signature": "sig",
                       "scope": {"allow": ["style-pref"]}})
    assert out["admission"] == "hold"


def test_classify_holds_when_no_provenance():
    out = ls.classify({"cls": "style-pref", "content_hash": "h3",
                       "scope": {"allow": ["style-pref"]}})    # no source/sig
    assert out["admission"] == "hold"
    assert "no-provenance" in out["triggers"]


def test_classify_rejects_forbidden_floor_even_if_allowed():
    out = ls.classify({"cls": "protected-attribute", "content_hash": "h4",
                       "source_actor": "x", "signature": "s",
                       "scope": {"allow": ["protected-attribute"]}})
    assert out["admission"] == "reject"          # hard floor wins


# --- record to the signed log + read it back via admission_log ---

def test_classify_record_then_log_roundtrip(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    out = ls.classify({"cls": "style-pref", "content_hash": "hX",
                       "source_actor": "alex", "signature": "sig",
                       "scope": {"allow": ["style-pref"]},
                       "record": True, "folder_context": str(folder),
                       "actor": "alex"},
                      log_root=tmp_path / "log")
    assert "audit" in out and "audit_id" in out["audit"]
    feed = ls.admission_log(folder, log_root=tmp_path / "log")
    assert feed["count"] == 1
    assert feed["events"][0]["admission"] == "admit"
    assert feed["events"][0]["class"] == "style-pref"
    assert feed["spent"] == 1.0          # default magnitude, the budget-meter input


def test_admission_log_empty_when_none(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    feed = ls.admission_log(folder, log_root=tmp_path / "log")
    assert feed == {"folder": str(folder), "count": 0, "held": 0,
                    "spent": 0.0, "cap": None, "over_budget": False,
                    "events": []}


# --- precedent selection (stare decisis for agents) ---

def test_select_precedent_picks_highest_applicable():
    params = {"features": {"q": 1}, "candidates": [
        {"id": "p1", "actor": "alex", "learnable": True,
         "similarity_threshold": 0.8, "similarity": 0.85},
        {"id": "p2", "actor": "alex", "learnable": True,
         "similarity_threshold": 0.8, "similarity": 0.95},
        {"id": "p3", "actor": "alex", "learnable": False,   # not learnable
         "similarity_threshold": 0.8, "similarity": 0.99}]}
    out = ls.select(params)
    assert out["selected"]["id"] == "p2"
    assert out["actor_stamp"] == "agent-under-lens(precedent:p2)"


def test_select_precedent_none_when_below_threshold():
    out = ls.select({"candidates": [
        {"id": "p1", "actor": "f", "learnable": True,
         "similarity_threshold": 0.9, "similarity": 0.5}]})
    assert out["selected"] is None


# --- update budget ---

def test_budget_under_and_over():
    under = ls.budget({"cap": 10.0, "admitted": [{"magnitude": 3}, {"magnitude": 4}]})
    assert under["spent"] == 7.0 and under["over"] is False
    over = ls.budget({"cap": 5.0, "admitted": [{"magnitude": 3}, {"magnitude": 4}]})
    assert over["over"] is True


def test_budget_rejects_nonpositive_cap():
    assert "error" in ls.budget({"cap": 0})


# --- the MCP facade is registered + dispatches ---

def test_workspace_lens_facade_registered_and_dispatches():
    from workspaces import mcp_server
    assert "workspace_lens" in mcp_server._DECLARED_TOOLS
    res = mcp_server.workspace_lens("help")
    assert any(o["op"] == "classify" for o in res["ops"])
    v = mcp_server.workspace_lens("classify",
                             {"cls": "style-pref", "content_hash": "z",
                              "source_actor": "f", "signature": "s",
                              "scope": {"allow": ["style-pref"]}})
    assert v["admission"] == "admit"


# --- precedent persistence: declare / list / revoke on the signed log ---

def test_precedent_declare_list_revoke_roundtrip(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    lr = tmp_path / "log"
    ls.precedent_declare({"folder_context": str(folder), "id": "p1",
                          "chosen_option": "route to lawyer", "actor": "alex"},
                         log_root=lr)
    ls.precedent_declare({"folder_context": str(folder), "id": "p2",
                          "chosen_option": "auto-send", "actor": "alex"},
                         log_root=lr)
    shelf = ls.precedent_list(folder, log_root=lr)
    assert shelf["count"] == 2
    assert {p["id"] for p in shelf["precedents"]} == {"p1", "p2"}
    # revoke one → drops from the active shelf, still in include_inactive
    ls.precedent_revoke({"folder_context": str(folder), "id": "p1"}, log_root=lr)
    shelf2 = ls.precedent_list(folder, log_root=lr)
    assert {p["id"] for p in shelf2["precedents"]} == {"p2"}
    allp = ls.precedent_list(folder, log_root=lr, include_inactive=True)
    assert {p["id"] for p in allp["precedents"]} == {"p1", "p2"}


def test_precedent_list_excludes_expired(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    lr = tmp_path / "log"
    ls.precedent_declare({"folder_context": str(folder), "id": "old",
                          "expires_at": 1000.0, "actor": "f"}, log_root=lr)
    # now well past expiry → not active
    active = ls.precedent_list(folder, log_root=lr, now=2000.0)
    assert active["count"] == 0
    # before expiry → active
    active2 = ls.precedent_list(folder, log_root=lr, now=500.0)
    assert active2["count"] == 1


# --- update-budget cap persistence + admission_log surfacing ---

def test_budget_cap_get_set_roundtrip(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    lr = tmp_path / "log"
    assert ls.budget_cap_get(folder, log_root=lr) is None
    ls.budget_cap_set(folder, 5.0, log_root=lr)
    assert ls.budget_cap_get(folder, log_root=lr) == 5.0
    assert "error" in ls.budget_cap_set(folder, 0, log_root=lr)


def test_admission_log_surfaces_cap_and_over(tmp_path):
    folder = tmp_path / "workspace"; folder.mkdir()
    lr = tmp_path / "log"
    ls.budget_cap_set(folder, 1.5, log_root=lr)
    # two admits of magnitude 1.0 each → spent 2.0 > cap 1.5
    for h in ("a", "b"):
        ls.classify({"cls": "style-pref", "content_hash": h,
                     "source_actor": "f", "signature": "s",
                     "scope": {"allow": ["style-pref"]},
                     "record": True, "folder_context": str(folder)}, log_root=lr)
    feed = ls.admission_log(folder, log_root=lr)
    assert feed["cap"] == 1.5
    assert feed["spent"] == 2.0
    assert feed["over_budget"] is True


def test_facade_precedent_and_cap_ops(tmp_path):
    from workspaces import mcp_server
    help_ops = {o["op"] for o in mcp_server.workspace_lens("help")["ops"]}
    assert {"precedent_declare", "precedent_list", "precedent_revoke",
            "budget_cap_get", "budget_cap_set"} <= help_ops
