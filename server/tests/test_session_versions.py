# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""S9 (version DAG) + S10 (provenance) on the session core.

- Versions are CONTENT-ADDRESSED (hash of the signed manifest): identical
  environments → identical version; any change → a new one.
- History is a DAG: reloading an older version and continuing sprouts a
  second child of that version — fork-not-rewind, nothing overwritten.
- Provenance split (no-id wall): meta carries ROLE only; the named signer
  rides the SIGNED manifest, so forging it breaks the bundle signature.
"""
from __future__ import annotations

import json

import pytest

from workspaces import parties, session_io as S


def _ws(tmp_path, wid: str, extra_event: str = "") -> dict:
    folder = tmp_path / wid
    folder.mkdir(exist_ok=True)
    lr = str(tmp_path / "logs" / wid)
    parties.register_party(str(folder), f"bot-{wid}", "agent", log_root=lr)
    if extra_event:
        parties.register_party(str(folder), extra_event, "agent", log_root=lr)
    return S.capture_workspace(str(folder), workspace_id=wid, log_root=lr)


RAIL = {"order": ["a"], "focused": "a"}


def test_version_is_content_addressed(tmp_path):
    ws = _ws(tmp_path, "a")
    b1 = S.build_session([ws], RAIL, name="s", created="2026-06-30T00:00:00Z")
    b2 = S.build_session([ws], RAIL, name="s", created="2026-06-30T00:00:00Z")
    assert S.bundle_version(b1) == S.bundle_version(b2)          # same content
    b3 = S.build_session([ws], {**RAIL, "focused": None}, name="s",
                         created="2026-06-30T00:00:00Z")
    assert S.bundle_version(b3) != S.bundle_version(b1)          # any change → new


def test_lineage_is_a_dag_fork_not_rewind(tmp_path):
    """v1 → v2; then 'reload v1 and keep going' → v3 is a SECOND child of v1."""
    ws_v1 = _ws(tmp_path, "a")
    v1 = S.build_session([ws_v1], RAIL, name="s", created="2026-06-30T00:00:00Z")

    ws_v2 = _ws(tmp_path, "a", extra_event="bot-later")          # work continued
    v2 = S.next_session(v1, [ws_v2], RAIL, created="2026-07-01T00:00:00Z")

    ws_v3 = _ws(tmp_path, "a")                                   # branched from v1
    v3 = S.next_session(v1, [ws_v3], {**RAIL, "focused": None},
                        created="2026-07-02T00:00:00Z")

    assert v2["meta"]["parent_version"] == S.bundle_version(v1)
    assert v3["meta"]["parent_version"] == S.bundle_version(v1)  # two children of v1
    assert S.bundle_version(v2) != S.bundle_version(v3)
    assert v1["meta"]["parent_version"] is None                  # root
    for b in (v1, v2, v3):
        assert S.verify_session(b)["ok"]                         # every version signed


def test_provenance_split_role_vs_signer(tmp_path):
    ws = _ws(tmp_path, "a")
    b = S.build_session([ws], RAIL, name="s", created="2026-06-30T00:00:00Z",
                        origin_role="admin", signed_by="alex")
    # language side: role only — the named signer NEVER appears in meta
    assert b["meta"]["origin_role"] == "admin"
    assert "alex" not in json.dumps(b["meta"])
    # identity side: the signer rides the signed manifest
    assert b["manifest"]["signer"]["label"] == "alex"
    assert b["manifest"]["signer"]["key_fingerprint"]
    # and never inside any chain event as actor or payload (the no-id wall
    # holds on-chain too; folder_path may contain it as a path substring,
    # which is the machine's business, not the language's)
    for line in b["workspaces"][0]["chain"]["log_lines"]:
        obj = json.loads(line)
        assert obj.get("actor") != "alex"
        assert "alex" not in json.dumps(obj.get("extra") or {})


def test_forged_signer_breaks_the_signature(tmp_path):
    ws = _ws(tmp_path, "a")
    b = S.build_session([ws], RAIL, name="s", created="2026-06-30T00:00:00Z",
                        signed_by="alex")
    b["manifest"]["signer"]["label"] = "mallory"                 # forge accountability
    report = S.verify_session(b)
    assert not report["ok"]
    assert report["refusal"]["reason"] == S.REFUSAL_INVALID_SIGNATURE


def test_describe_session_renders_the_card(tmp_path):
    ws = _ws(tmp_path, "a")
    v1 = S.build_session([ws], RAIL, name="support-desk",
                         created="2026-06-30T00:00:00Z",
                         origin_role="admin", signed_by="alex")
    v2 = S.next_session(v1, [ws], RAIL, created="2026-07-01T00:00:00Z",
                        signed_by="alex")
    card = S.describe_session(v2)
    assert card["name"] == "support-desk"
    assert card["signed_by"] == "alex" and card["origin_role"] == "admin"
    assert card["parent_version"] == S.bundle_version(v1)        # lineage visible
    assert card["workspaces"][0]["id"] == "a"
    assert card["workspaces"][0]["events"] > 0
