# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Governed orchestration: find companions in a workspace tree, gate each, record."""
import os
from pathlib import Path

from rvnd import cli
from rvnd.workspace_orchestrate import orchestrate, turn_governance


def test_turn_governance_grounding_is_conditional():
    # casual / pure-reasoning turn: floor only, no grounding
    plain = turn_governance()
    assert plain["tools"] == ["audit-chain", "oversight"]
    assert plain["grounding"] is False
    assert "grounder" not in plain["conditional"]

    # a turn that rests on works AND hits the cloud: floor + lock + grounder
    research = turn_governance(egress_to_cloud=True, uses_works=True)
    assert research["grounding"] is True
    assert "grounder" in research["conditional"]
    assert "lock" in research["conditional"]

    # composing workspaces pulls in cross-workspace, still no grounding if no works
    compose = turn_governance(crosses_workspaces=True)
    assert "cross-workspace" in compose["conditional"]
    assert compose["grounding"] is False

os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


def _ingest(folder: Path, name: str, text: str, lr: Path) -> None:
    (folder / "Inbox").mkdir(parents=True, exist_ok=True)
    f = folder / "Inbox" / name
    f.write_text(text)
    cli.main(["--log-root", str(lr), "ingest", str(f), "--folder", str(folder)])


def test_orchestrate_routes_gates_records(tmp_path):
    lr = tmp_path / "log"
    root = tmp_path / "work"
    comp = tmp_path / "work" / "legalbot"        # a child workspace
    _ingest(root, "a.txt", "root note", lr)
    _ingest(comp, "b.txt", "contract clauses", lr)
    # make `legalbot` a companion by pinning a skill to it
    cli.main(["--log-root", str(lr), "pin", "vertical:legalbot/review",
              "--folder", str(comp)])

    res = orchestrate("review this contract", root, log_root=lr)

    assert res["governed"] is True
    assert res["scope_workspaces"] >= 2               # root + child in scope
    assert res["audit_id"]                        # plan recorded on the chain
    entries = {c["name"]: c for c in res["companions"]}
    assert "legalbot" in entries, res["companions"]
    e = entries["legalbot"]
    assert e["verdict"] in ("GO", "CONDITIONAL", "NO-GO")
    assert "vertical:legalbot/review" in e["skills"]


def _fake_ok(url, model, prompt, *, api_key="", temperature=0.0,
             max_tokens=512, timeout=30.0):
    return {"ok": True, "response": "the answer [cited]",
            "usage": {"total_tokens": 6}}


def test_ask_workspace_governs_a_turn(tmp_path, monkeypatch):
    from rvnd.workspace_orchestrate import ask_workspace
    lr = tmp_path / "log"
    c = tmp_path / "c"
    _ingest(c, "a.txt", "a creator's work", lr)        # sources present → grounding applies
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:9/v1")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_MODEL", "dummy-local")
    for k in ("WORKSPACE_CLOUD_LLM_URL", "WORKSPACE_CLOUD_LLM_MODEL", "WORKSPACE_CLOUD_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    r = ask_workspace("what does this say?", c, completer=_fake_ok, log_root=lr)
    assert r["ok"] is True
    assert r["served_by"] == "local"
    assert r["governance"]["grounding"] is True        # works present
    assert r["grounding"]["applied"] is True and r["grounding"]["sources"]
    assert r["audit_id"]


def test_ask_workspace_fuses_orchestrate_dispatch_and_fold(tmp_path, monkeypatch):
    from rvnd.workspace_orchestrate import ask_workspace
    lr = tmp_path / "log"
    root = tmp_path / "work"
    comp = tmp_path / "work" / "coder"            # a companion workspace under root
    _ingest(root, "a.txt", "root note", lr)
    _ingest(comp, "b.txt", "code context", lr)
    cli.main(["--log-root", str(lr), "pin", "vertical:coder/solve", "--folder", str(comp)])
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_URL", "http://localhost:9/v1")
    monkeypatch.setenv("WORKSPACE_LOCAL_LLM_MODEL", "dummy-local")
    for k in ("WORKSPACE_CLOUD_LLM_URL", "WORKSPACE_CLOUD_LLM_MODEL", "WORKSPACE_CLOUD_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    seen = {}

    def fake_dispatch(*, folder, query, skills):
        seen["skills"] = skills
        return {"ok": True, "response": "def solution(): pass"}

    def fake_completer(url, model, prompt, *, api_key="", temperature=0.0,
                       max_tokens=512, timeout=30.0):
        seen["prompt"] = prompt
        return {"ok": True, "response": "final answer", "usage": {"total_tokens": 8}}

    r = ask_workspace("solve this", root, dispatcher=fake_dispatch,
                 completer=fake_completer, log_root=lr)

    assert r["ok"] is True
    names = [d["name"] for d in r["companions"]]
    assert "coder" in names                                  # routed to the companion
    assert any(d["output"].get("ok") for d in r["companions"])  # it was dispatched (GO)
    assert "def solution" in seen["prompt"]                  # output folded into the cascade
    assert r["grounding"]["applied"] is True                 # works present → creators credited
    assert r["audit_id"]


