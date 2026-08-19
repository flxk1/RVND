# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Concept probe #2 — append-after-restore ("reload and keep going").

The flagship promise is not snapshot fidelity (S7 proves that) but CONTINUITY:
after restoring a session you keep working, and the chain stays sound. This
test asks the falsifiable question directly:

  restore → append a NEW governed event → does it link to the verbatim tip,
  does verify_chain stay green (same machine), does re-capture still verify?

If this fails, the local concept is broken and no UI can fix it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rvnd import parties, session_io as S
from rvnd.mutation_log import MutationLog


@pytest.fixture
def restored(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    slr = str(tmp_path / "slog")
    parties.register_party(str(src), "bot-1", "agent", log_root=slr)
    parties.register_party(str(src), "legal-1", "human", competences=["legal"], log_root=slr)
    doc = S.capture_workspace(str(src), workspace_id="ws", log_root=slr)

    dest = str(tmp_path / "dest")
    dlr = str(tmp_path / "dlog")
    S.restore_workspace(doc, dest, log_root=dlr)
    return doc, dest, dlr, str(tmp_path)


def test_restored_chain_verifies_before_continuing(restored):
    _, dest, dlr, _ = restored
    assert MutationLog(dest, log_root=dlr).verify_chain().ok


def test_append_links_to_the_restored_tip(restored):
    """A new event's prev_hash must chain onto the verbatim-written last line."""
    doc, dest, dlr, _ = restored
    before = MutationLog(dest, log_root=dlr).log_file.read_text().splitlines()
    before = [l for l in before if l.strip()]
    # keep going: register another party on the RESTORED chain
    parties.register_party(dest, "bot-2", "agent", log_root=dlr)
    r = MutationLog(dest, log_root=dlr).verify_chain()
    assert r.ok, f"verify_chain broke after append: {r}"
    after = [l for l in MutationLog(dest, log_root=dlr).log_file.read_text().splitlines() if l.strip()]
    assert len(after) == len(before) + 1          # exactly one new event
    # the new event actually projects (the work continued)
    ids = {p["party_id"] for p in parties.list_parties(dest, log_root=dlr)["parties"]}
    assert ids == {"bot-1", "legal-1", "bot-2"}


def test_recapture_after_continue_still_verifies(restored):
    """Reload → keep going → save again: the continued bundle must verify and
    carry the new work (same machine / same key — the local happy path)."""
    doc, dest, dlr, root = restored
    parties.register_party(dest, "bot-2", "agent", log_root=dlr)
    again = S.capture_workspace(dest, workspace_id="ws", log_root=dlr)
    bundle = S.build_session([again], {"order": ["ws"], "focused": "ws"},
                             name="cont", created="2026-07-02T00:00:00Z")
    report = S.verify_session(bundle)
    assert report["ok"], report.get("refusal")
    # the continued chain grew by exactly the one new event vs the original doc
    assert len(again["chain"]["log_lines"]) == len(doc["chain"]["log_lines"]) + 1


def test_continue_then_restore_again_roundtrips(restored):
    """A second generation: restore → continue → capture → restore → continue.
    Proves the loop is stable, not a one-shot."""
    doc, dest, dlr, root = restored
    parties.register_party(dest, "bot-2", "agent", log_root=dlr)
    gen2 = S.capture_workspace(dest, workspace_id="ws", log_root=dlr)

    dest2 = str(Path(root) / "dest2")
    dlr2 = str(Path(root) / "dlog2")
    S.restore_workspace(gen2, dest2, log_root=dlr2)
    assert MutationLog(dest2, log_root=dlr2).verify_chain().ok
    parties.register_party(dest2, "bot-3", "agent", log_root=dlr2)
    assert MutationLog(dest2, log_root=dlr2).verify_chain().ok
    ids = {p["party_id"] for p in parties.list_parties(dest2, log_root=dlr2)["parties"]}
    assert ids == {"bot-1", "legal-1", "bot-2", "bot-3"}


# --- Decision B: foreign-key chains are view-only, not continuable -----------

def _foreign_pubkey_pem():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    return Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def test_continuation_check_flags_foreign_key(tmp_path):
    src = tmp_path / "s"; src.mkdir()
    lr = str(tmp_path / "l")
    parties.register_party(str(src), "bot-1", "agent", log_root=lr)
    doc = S.capture_workspace(str(src), workspace_id="ws", log_root=lr)
    bundle = S.build_session([doc], {"order": ["ws"], "focused": "ws"},
                             name="s", created="2026-07-02T00:00:00Z")
    assert S.continuation_check(bundle)["continuable"] is True     # local key
    # pretend it came from another machine: swap the embedded chain key
    bundle["workspaces"][0]["chain"]["pubkey_pem"] = _foreign_pubkey_pem()
    chk = S.continuation_check(bundle)
    assert chk["continuable"] is False and chk["foreign"][0]["workspace"] == "ws"


def test_restore_refuses_foreign_key_but_forensic_still_reads(tmp_path):
    src = tmp_path / "s"; src.mkdir()
    lr = str(tmp_path / "l")
    parties.register_party(str(src), "bot-1", "agent", log_root=lr)
    doc = S.capture_workspace(str(src), workspace_id="ws", log_root=lr)
    bundle = S.build_session([doc], {"order": ["ws"], "focused": "ws"},
                             name="s", created="2026-07-02T00:00:00Z")
    bundle["workspaces"][0]["chain"]["pubkey_pem"] = _foreign_pubkey_pem()
    # restore-for-continue is refused (would create an unverifiable local chain)
    with pytest.raises(S.SessionIntegrityError) as e:
        S.restore_environment(bundle, str(tmp_path / "dest"))
    assert e.value.report["refusal"]["reason"] == S.REFUSAL_FOREIGN_KEY
    # but LOOKING is always allowed (fail-closed on write, not on looking)
    view = S.forensic_bundle(bundle)
    assert view["readable"] and "ws" in view["workspaces"]
