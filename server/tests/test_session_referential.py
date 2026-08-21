# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""S4 — referential integrity on restore.

Every cross-reference (connector→use_case, use_case→agent, party→connector,
rail→workspace) must resolve WITHIN the bundle. A full-environment session is
complete by construction; a dangling ref is fail-closed with a LOCATED reason
(never a silent drop) — and it's the load-bearing guard for single-workspace
import (S13), where dropping a workspace can leave a ref pointing at nothing.
"""
from __future__ import annotations


import pytest

from rvnd import connectors, parties, use_case, session_io as S


def _ws(tmp_path, wid: str, *, wire=True) -> dict:
    folder = tmp_path / wid
    folder.mkdir()
    lr = str(tmp_path / "logs" / wid)
    parties.register_party(str(folder), "bot-1", "agent", grade="L2", log_root=lr)
    use_case.register_use_case(
        str(folder), use_case_id="uc-1", name="Reply",
        fingerprint={"issue_type": "reply"}, risk="high",
        allowed_agents=["bot-1"] if wire else ["ghost-agent"], actor="bot-1", log_root=lr)
    connectors.register_connector(
        str(folder), connector_id="out", role="egress", channel="email",
        use_cases=["uc-1"] if wire else ["uc-missing"], log_root=lr)
    return S.capture_workspace(str(folder), workspace_id=wid, log_root=lr)


def _bundle(workspaces, rail):
    return S.build_session(workspaces, rail, name="s", created="2026-06-30T00:00:00Z")


def test_complete_environment_resolves(tmp_path):
    b = _bundle([_ws(tmp_path, "a"), _ws(tmp_path, "b")],
                {"order": ["a", "b"], "focused": "a"})
    assert S.check_referential_integrity(b)["ok"]


def test_connector_to_missing_use_case_is_located(tmp_path):
    b = _bundle([_ws(tmp_path, "a", wire=False)], {"order": ["a"], "focused": "a"})
    ref = S.check_referential_integrity(b)
    assert not ref["ok"]
    d = next(x for x in ref["dangling"] if x["from"].startswith("connector"))
    assert d["workspace"] == "a" and d["ref"] == "use_case:uc-missing"
    assert d["reason"] == "missing use_case"


def test_use_case_to_missing_agent_is_located(tmp_path):
    b = _bundle([_ws(tmp_path, "a", wire=False)], {"order": ["a"], "focused": "a"})
    ref = S.check_referential_integrity(b)
    assert any(x["from"] == "use_case:uc-1" and x["ref"] == "agent:ghost-agent"
               and x["reason"] == "missing agent" for x in ref["dangling"])


def test_rail_referencing_missing_workspace(tmp_path):
    b = _bundle([_ws(tmp_path, "a")], {"order": ["a", "gone"], "focused": "a"})
    ref = S.check_referential_integrity(b)
    assert not ref["ok"]
    assert any(x["from"] == "rail:order" and x["ref"] == "workspace:gone"
               for x in ref["dangling"])


def test_full_load_is_fail_closed_on_dangling(tmp_path):
    b = _bundle([_ws(tmp_path, "a", wire=False)], {"order": ["a"], "focused": "a"})
    p = S.save_session(b, tmp_path / "d.rvnd")
    with pytest.raises(S.SessionIntegrityError) as exc:
        S.load_session(p)
    assert exc.value.report["refusal"]["reason"] == S.REFUSAL_DANGLING_REF
    assert "→" in exc.value.report["refusal"]["detail"]      # located


def test_single_workspace_slice_dangles_at_rail(tmp_path):
    """S13 preview: keep only workspace 'a' but a rail that named 'b' → dangles."""
    full = [_ws(tmp_path, "a"), _ws(tmp_path, "b")]
    sliced = _bundle([full[0]], {"order": ["a", "b"], "focused": "a"})
    ref = S.check_referential_integrity(sliced)
    assert not ref["ok"]
    assert any(x["ref"] == "workspace:b" for x in ref["dangling"])


def test_clean_bundle_load_returns_referential_ok(tmp_path):
    b = _bundle([_ws(tmp_path, "a")], {"order": ["a"], "focused": "a"})
    p = S.save_session(b, tmp_path / "ok.rvnd")
    _, report = S.load_session(p)
    assert report["referential"]["ok"]
