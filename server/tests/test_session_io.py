# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Session I/O core (S1 schema + S6 verify + S7 round-trip) — the un-gated slice.

Pins the session I/O contract:
- byte-preserved chain embed (configs are chain projections; no re-serialization)
- three ordered checks, short-circuit ("not reached"), located errors,
  refusal taxonomy, NOT overridable (load_session raises)
- portable verification (embedded key — no reliance on the local folder)
- fail-closed on write, not on looking (forensic view never raises)
- save→load→save round-trip identity excluding volatile meta
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspaces import draft_store, session_io as S
from workspaces.mutation_log import LogEvent, MutationLog


def _make_workspace(tmp: str, wid: str, n_events: int = 4) -> dict:
    ws_dir = Path(tmp) / wid
    ws_dir.mkdir(parents=True)
    log_root = str(Path(tmp) / "logs" / wid)
    log = MutationLog(str(ws_dir), log_root=log_root)
    for i in range(n_events):
        log.append(LogEvent(event="system", folder_path=str(ws_dir),
                            pair_id=f"p{i}", channel="system", actor="user",
                            extra={"kind": "X", "n": i}))
    # capture reads drafts from the workspace's store, not from a param
    draft_store.save(str(ws_dir), "policy_paste", {"text": f"draft for {wid}"},
                     log_root=log_root)
    return S.capture_workspace(
        str(ws_dir), workspace_id=wid, name=wid.title(), log_root=log_root,
        presentation={"positions": {"a": [1, 2]}, "view": "patch"},
    )


@pytest.fixture
def session(tmp_path):
    workspaces = [_make_workspace(str(tmp_path), "support", 4),
                  _make_workspace(str(tmp_path), "billing", 3)]
    rail = {"order": ["support", "billing"], "focused": "support",
            "global_view": {"ticker": False}}
    bundle = S.build_session(workspaces, rail, name="support-desk",
                             created="2026-06-30T12:00:00Z")
    return bundle, tmp_path


# ---- happy path -------------------------------------------------------------

def test_bundle_shape_and_verify_pass(session):
    bundle, _ = session
    assert bundle["format"] == "rvnd-session"
    assert bundle["schema_version"] == "1.0"
    assert bundle["meta"]["workspace_count"] == 2
    report = S.verify_session(bundle)
    assert report["ok"] and report["refusal"] is None
    assert [c["status"] for c in report["checks"]] == ["pass", "pass", "pass"]


def test_chain_lines_are_byte_preserved(session, tmp_path):
    bundle, _ = session
    ws = bundle["workspaces"][0]
    log = MutationLog(str(Path(str(tmp_path)) / ws["id"]),
                      log_root=str(Path(str(tmp_path)) / "logs" / ws["id"]))
    on_disk = [l for l in log.log_file.read_text(encoding="utf-8").splitlines()
               if l.strip()]
    assert ws["chain"]["log_lines"] == on_disk   # verbatim, no re-serialization


def test_save_load_roundtrip_exact_state(session, tmp_path):
    bundle, _ = session
    p = S.save_session(bundle, tmp_path / "s.rvnd")
    loaded, report = S.load_session(p)
    assert report["ok"]
    # S7: identity across chains + presentation + drafts + rail (volatile
    # meta — modified/version — excluded by comparing the stable parts).
    assert loaded["workspaces"] == bundle["workspaces"]
    assert loaded["rail"] == bundle["rail"]
    assert loaded["meta"]["name"] == bundle["meta"]["name"]
    # save again: stable parts identical
    p2 = S.save_session(loaded, tmp_path / "s2.rvnd")
    again, _ = S.load_session(p2)
    assert again["workspaces"] == bundle["workspaces"]
    assert again["rail"] == bundle["rail"]


def test_verifies_without_local_folder(session, tmp_path):
    """Portability: verification uses only the embedded key + bytes."""
    bundle, _ = session
    text = json.dumps(bundle)
    reparsed = json.loads(text)
    assert S.verify_session(reparsed)["ok"]


# ---- the S6 contract: tamper battery, all fail-closed -----------------------

def _tamper_event(bundle, ws_idx=0, line_idx=1):
    obj = json.loads(bundle["workspaces"][ws_idx]["chain"]["log_lines"][line_idx])
    obj["extra"]["n"] = 999
    bundle["workspaces"][ws_idx]["chain"]["log_lines"][line_idx] = json.dumps(obj)
    return bundle


def test_content_tamper_is_broken_chain_with_location(session):
    bundle, _ = session
    report = S.verify_session(_tamper_event(bundle))
    assert not report["ok"]
    assert report["refusal"]["reason"] == S.REFUSAL_BROKEN_CHAIN
    assert "support" in report["refusal"]["detail"]          # WHICH workspace
    assert "#" in report["refusal"]["detail"]                # WHERE (event #)
    # short-circuit: later checks not reached
    assert report["checks"][1]["status"] == "not_reached"
    assert report["checks"][2]["status"] == "not_reached"


def test_deleted_event_detected(session):
    bundle, _ = session
    del bundle["workspaces"][0]["chain"]["log_lines"][1]
    report = S.verify_session(bundle)
    assert not report["ok"] and report["refusal"]["reason"] == S.REFUSAL_BROKEN_CHAIN


def test_reordered_events_detected(session):
    bundle, _ = session
    lines = bundle["workspaces"][0]["chain"]["log_lines"]
    lines[1], lines[2] = lines[2], lines[1]
    report = S.verify_session(bundle)
    assert not report["ok"] and report["refusal"]["reason"] == S.REFUSAL_BROKEN_CHAIN


def test_stripped_signature_detected(session):
    bundle, _ = session
    lines = bundle["workspaces"][0]["chain"]["log_lines"]
    obj = json.loads(lines[-1])
    obj["signature"] = ""
    # keep hash-chain consistent so ONLY the stripped signature can catch it
    lines[-1] = json.dumps(obj)
    report = S.verify_session(bundle)
    assert not report["ok"] and report["refusal"]["reason"] == S.REFUSAL_BROKEN_CHAIN
    ws = report["checks"][0]["workspaces"]["support"]
    assert any(f["reason"] == "unsigned_event_after_signing_epoch"
               for f in ws["failures"])


def test_offchain_tamper_is_altered_content(session):
    bundle, _ = session
    bundle["workspaces"][1]["presentation"]["view"] = "hacked"
    report = S.verify_session(bundle)
    assert not report["ok"]
    assert report["refusal"]["reason"] == S.REFUSAL_ALTERED_CONTENT
    assert "billing" in report["refusal"]["detail"]
    assert report["checks"][0]["status"] == "pass"           # chains were fine
    assert report["checks"][2]["status"] == "not_reached"


def test_rail_tamper_is_altered_content(session):
    bundle, _ = session
    bundle["rail"]["focused"] = "billing"
    report = S.verify_session(bundle)
    assert not report["ok"] and report["refusal"]["reason"] == S.REFUSAL_ALTERED_CONTENT


def test_forged_manifest_fails_signature(session):
    bundle, _ = session
    # adversary edits rail AND recomputes the manifest hash — only the
    # signature over the manifest can catch this
    bundle["rail"]["focused"] = "billing"
    bundle["manifest"]["rail_hash"] = S._hash_obj(bundle["rail"])
    report = S.verify_session(bundle)
    assert not report["ok"]
    assert report["refusal"]["reason"] == S.REFUSAL_INVALID_SIGNATURE
    assert report["checks"][0]["status"] == "pass"
    assert report["checks"][1]["status"] == "pass"


def test_unknown_major_schema_refused(session):
    bundle, _ = session
    bundle["schema_version"] = "2.0"
    report = S.verify_session(bundle)
    assert not report["ok"] and report["refusal"]["reason"] == S.REFUSAL_UNKNOWN_SCHEMA


def test_not_a_session_refused():
    report = S.verify_session({"format": "something-else"})
    assert not report["ok"] and report["refusal"]["reason"] == S.REFUSAL_NOT_A_SESSION


def test_load_is_not_overridable(session, tmp_path):
    """No 'open anyway': a tampered file RAISES; nothing is returned."""
    bundle, _ = session
    p = S.save_session(_tamper_event(bundle), tmp_path / "bad.rvnd")
    with pytest.raises(S.SessionIntegrityError) as exc:
        S.load_session(p)
    assert exc.value.report["refusal"]["reason"] == S.REFUSAL_BROKEN_CHAIN


# ---- fail-closed on write, not on looking -----------------------------------

def test_forensic_view_shows_salvageable(session, tmp_path):
    bundle, _ = session
    p = S.save_session(_tamper_event(bundle), tmp_path / "bad.rvnd")
    view = S.read_session_forensic(p)                        # must NOT raise
    assert view["readable"] and not view["ok"]
    assert view["workspaces"]["support"]["salvageable"] is False
    assert view["workspaces"]["billing"]["salvageable"] is True
    assert view["meta"]["name"] == "support-desk"


def test_forensic_view_on_garbage_file(tmp_path):
    p = tmp_path / "junk.rvnd"
    p.write_text("not json at all{{{")
    view = S.read_session_forensic(p)
    assert view["readable"] is False
