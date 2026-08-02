# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Starter templates (S14) — recipes materialized in-process, never fixtures.

Pins the trust-model decision: a template is declarative data instantiated
through the governed write paths and signed with the local key, because a
shipped signed fixture would be a foreign-key session — view-only under
decision B, hence never "loadable as a fresh environment".
"""
from __future__ import annotations

import pytest

from workspaces import (connectors, draft_store, parties, session_io as S,
                        session_templates as T, use_case)

CREATED = "2026-07-09T12:00:00Z"


@pytest.fixture
def kids(tmp_path):
    lr = str(tmp_path / "logs")
    out = T.materialize("kids-ai", tmp_path / "env", created=CREATED,
                        log_root=lr)
    assert out["ok"], out
    return out, lr


# ---- catalogue ---------------------------------------------------------------

def test_catalogue_lists_the_builtins():
    cat = {t["id"]: t for t in T.list_templates()}
    assert {"kids-ai", "enterprise-baseline"} <= set(cat)
    for t in cat.values():
        assert t["name"] and t["description"] and t["workspaces"]
    assert [w["id"] for w in cat["enterprise-baseline"]["workspaces"]] == [
        "operations", "compliance"]


# ---- the decision pin: local-key materialization, not a shipped fixture ------

def test_materialized_bundle_verifies_and_is_continuable(kids):
    out, _ = kids
    report = S.verify_full(out["bundle"])
    assert report["ok"], report
    # The trust-model point: signed here, with the local key — continuable,
    # which no static fixture signed elsewhere could ever be.
    assert out["continuation"]["continuable"] is True
    assert out["card"]["name"] == "Govern a kid's AI"
    assert out["version"].startswith("sha256:")


def test_a_shipped_fixture_would_be_view_only(kids, tmp_path):
    """The road not taken, kept refused: the same bundle with a foreign chain
    key (what a packager-signed fixture is on every user machine) cannot be
    restored as a fresh environment."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    out, _ = kids
    foreign_pem = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    fixture = out["bundle"]
    fixture["workspaces"][0]["chain"]["pubkey_pem"] = foreign_pem
    assert S.continuation_check(fixture)["continuable"] is False
    with pytest.raises(S.SessionIntegrityError) as e:
        S.restore_environment(fixture, tmp_path / "elsewhere")
    assert e.value.report["refusal"]["reason"] == S.REFUSAL_FOREIGN_KEY


# ---- the recipe goes through the real governed paths --------------------------

def test_recipe_config_is_chain_projected(kids):
    out, lr = kids
    folder = out["folders"]["kids-ai"]
    ucs = use_case.list_use_cases(folder, log_root=lr)
    assert any(u["use_case_id"] == "homework-help" for u in ucs)
    roster = parties.list_parties(folder, log_root=lr)["parties"]
    assert {"guardian", "companion"} <= {p["party_id"] for p in roster}
    cs = {c["connector_id"] for c in connectors.list_connectors(folder, log_root=lr)}
    assert "chat" in cs
    # and the agent is actually permitted on the seeded use case
    assert use_case.agent_permitted(folder, "homework-help", "companion",
                                    log_root=lr)


def test_referential_integrity_holds_by_construction(kids):
    out, _ = kids
    ref = S.check_referential_integrity(out["bundle"])
    assert ref["ok"], ref["dangling"]


def test_drafts_seed_through_the_store_and_embed(kids):
    out, lr = kids
    folder = out["folders"]["kids-ai"]
    loaded = draft_store.load(folder, "policy_paste", log_root=lr)
    assert loaded["ok"] and "homework" in loaded["payload"]["text"]
    ws0 = out["bundle"]["workspaces"][0]
    assert ws0["drafts"]["policy_paste"] == loaded["payload"]
    assert out["drafts_refused"] == {}


def test_enterprise_baseline_builds_the_two_desk_rail(tmp_path):
    lr = str(tmp_path / "logs")
    out = T.materialize("enterprise-baseline", tmp_path / "env",
                        created=CREATED, log_root=lr)
    assert out["ok"], out
    assert out["rail"] == {"order": ["operations", "compliance"],
                           "focused": "operations"}
    assert set(out["folders"]) == {"operations", "compliance"}
    assert S.verify_full(out["bundle"])["ok"]
    assert out["continuation"]["continuable"] is True


# ---- loadable as a fresh environment (the S14 acceptance line) ----------------

def test_bundle_restores_as_a_fresh_environment(kids, tmp_path):
    out, _ = kids
    applied = S.restore_environment(out["bundle"], tmp_path / "fresh",
                                    log_root_for={"kids-ai": str(tmp_path / "fl")})
    assert set(applied["folders"]) == {"kids-ai"}
    assert applied["rail"]["focused"] == "kids-ai"
    assert applied["drafts"]["kids-ai"]["policy_paste"]["text"].startswith(
        "Starter policy")


# ---- fail-closed edges ---------------------------------------------------------

def test_unknown_template_refused_with_catalogue(tmp_path):
    out = T.materialize("nope", tmp_path / "env", created=CREATED)
    assert not out["ok"]
    assert "unknown template" in out["error"] and "kids-ai" in out["error"]


def test_never_writes_into_a_used_destination(kids, tmp_path):
    out, lr = kids
    before = len(out["bundle"]["workspaces"][0]["chain"]["log_lines"])
    again = T.materialize("kids-ai", tmp_path / "env", created=CREATED,
                          log_root=lr)
    assert not again["ok"] and "fresh folders" in again["error"]
    # the first environment is untouched — same chain, still verifying
    doc = S.capture_workspace(out["folders"]["kids-ai"], workspace_id="kids-ai",
                              log_root=lr)
    assert len(doc["chain"]["log_lines"]) == before


def test_guard_refuses_before_touching_any_folder(tmp_path):
    """Partial-collision refusal is atomic: the clean sibling folder is not
    created when another recipe folder is already in use."""
    lr = str(tmp_path / "logs")
    busy = tmp_path / "env" / "compliance"
    busy.mkdir(parents=True)
    (busy / "keep.txt").write_text("mine", encoding="utf-8")
    out = T.materialize("enterprise-baseline", tmp_path / "env",
                        created=CREATED, log_root=lr)
    assert not out["ok"]
    assert not (tmp_path / "env" / "operations").exists()
    assert (busy / "keep.txt").read_text(encoding="utf-8") == "mine"


# ---- the workspace_session facade (S12 wiring) ---------------------------------

def test_facade_new_materializes_and_registers_beside(tmp_path):
    from workspaces import mcp_server, workspace_registry as WR
    listed = mcp_server.workspace_session("template_list")
    assert listed["ok"]
    assert {"kids-ai", "enterprise-baseline"} <= {t["id"] for t in listed["templates"]}
    rlr = str(tmp_path / "registry")
    out = mcp_server.workspace_session("template_new", {
        "template_id": "kids-ai", "dest_root": str(tmp_path / "env"),
        "log_root": str(tmp_path / "logs"), "registry_log_root": rlr})
    assert out["ok"], out
    assert out["mode"] == "beside" and out["retired"] == []
    registered = {w["path"] for w in
                  WR.load_registry(log_root=rlr).get("workspaces") or []}
    assert set(out["folders"].values()) <= registered
    assert out["continuation"]["continuable"] is True


def test_facade_mode_none_skips_the_registry(tmp_path):
    from workspaces import mcp_server, workspace_registry as WR
    rlr = str(tmp_path / "registry")
    out = mcp_server.workspace_session("template_new", {
        "template_id": "kids-ai", "dest_root": str(tmp_path / "env"),
        "log_root": str(tmp_path / "logs"), "mode": "none",
        "registry_log_root": rlr})
    assert out["ok"] and out["mode"] == "none"
    assert (WR.load_registry(log_root=rlr).get("workspaces") or []) == []


def test_facade_refuses_unknown_mode_and_template(tmp_path):
    from workspaces import mcp_server
    bad_mode = mcp_server.workspace_session("template_new", {
        "template_id": "kids-ai", "dest_root": str(tmp_path / "env"),
        "mode": "merge"})
    assert not bad_mode["ok"] and "mode" in bad_mode["error"]
    assert not (tmp_path / "env").exists()          # refused before any write
    bad_tid = mcp_server.workspace_session("template_new", {
        "template_id": "nope", "dest_root": str(tmp_path / "env")})
    assert not bad_tid["ok"] and "unknown template" in bad_tid["error"]
