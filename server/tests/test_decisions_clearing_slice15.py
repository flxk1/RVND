# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Privacy Lock clearing/approval store fixes.

CL1 a "block always" must be persisted (not only allow) — via the egress helper.
CL4 pattern_preview must be redacted before persist + erasable on GDPR purge.
CL5 ALLOW clearances expire (TTL); BLOCK never expires (lapsing = fail-open).
CL6 on a recall tie, BLOCK wins over a later ALLOW (safety). Lock/Shield panel."""
from __future__ import annotations

import json

import pytest

from workspaces.lock.decisions import DecisionsStore
from workspaces.lock.egress_proxy import _persist_scope_decision


def _store(tmp_path, **kw):
    return DecisionsStore(tmp_path / "decisions.jsonl", session_id="s1", **kw)


# ── CL6: block-precedence on recall ──────────────────────────────────────────

def test_block_wins_over_later_allow(tmp_path):
    s = _store(tmp_path)
    s.remember("send X to cloud", "allow", scope="always", actor="tester")
    s.remember("send X to cloud", "block", scope="always", actor="tester")   # later allow must NOT win
    assert s.recall("send X to cloud") == "block"


def test_block_wins_even_when_block_came_first(tmp_path):
    s = _store(tmp_path)
    s.remember("send Y", "block", scope="always", actor="tester")
    s.remember("send Y", "allow", scope="always", actor="tester")
    assert s.recall("send Y") == "block"


def test_allow_only_still_recalls_allow(tmp_path):
    s = _store(tmp_path)
    s.remember("benign string", "allow", scope="always", actor="tester")
    assert s.recall("benign string") == "allow"


# ── CL5: TTL on allow; block never expires ───────────────────────────────────

def test_allow_expires_past_ttl(tmp_path):
    s = _store(tmp_path, ttl_seconds=100)
    rec = s.remember("aging clearance", "allow", scope="always", actor="tester")
    assert s.recall("aging clearance", now=rec.ts + 50) == "allow"     # still fresh
    assert s.recall("aging clearance", now=rec.ts + 101) is None       # expired → re-prompt


def test_block_never_expires(tmp_path):
    s = _store(tmp_path, ttl_seconds=100)
    rec = s.remember("forbidden", "block", scope="always", actor="tester")
    assert s.recall("forbidden", now=rec.ts + 10**9) == "block"        # a block can't lapse


def test_ttl_zero_disables_expiry(tmp_path):
    s = _store(tmp_path, ttl_seconds=0)
    rec = s.remember("kept", "allow", scope="always", actor="tester")
    assert s.recall("kept", now=rec.ts + 10**9) == "allow"


# ── CL4: preview redaction before persist + erase on purge ───────────────────

def test_preview_is_redacted_before_persist(tmp_path):
    s = _store(tmp_path)
    secret = "please email alice\x40secret.example with the key"
    rec = s.remember(secret, "allow", scope="always", actor="tester")
    assert "alice\x40secret.example" not in rec.pattern_preview          # in-memory record
    assert "alice\x40secret.example" not in (tmp_path / "decisions.jsonl").read_text("utf-8")
    assert s.recall(secret) == "allow"                               # hash-based recall unaffected


def test_erase_scrubs_all_previews_but_keeps_recall(tmp_path):
    s = _store(tmp_path)
    s.remember("a plain note", "allow", scope="always", actor="tester")
    assert s.all_decisions()[0].pattern_preview                       # a preview exists
    n = s.erase()
    assert n >= 1
    assert all(d.pattern_preview == "" for d in s.all_decisions())    # in-memory scrubbed
    assert "a plain note" not in (tmp_path / "decisions.jsonl").read_text("utf-8")  # on-disk scrubbed
    assert s.recall("a plain note") == "allow"                        # hash retained → recall works


def test_erase_subject_scrubs_only_matching_previews(tmp_path):
    # erase_subject (the GDPR-by-subject wiring) blanks previews that still
    # contain a term the redactor can't catch (a plain name), leaving others.
    s = _store(tmp_path)
    s.remember("approve report for Jane Quibble please", "allow", scope="always", actor="tester")
    s.remember("unrelated routine note", "allow", scope="always", actor="tester")
    n = s.erase_subject("Jane Quibble")
    assert n == 1
    on_disk = (tmp_path / "decisions.jsonl").read_text("utf-8")
    assert "Jane Quibble" not in on_disk
    assert "unrelated routine note" in on_disk            # other record untouched


def test_split_token_secret_does_not_leak_in_preview(tmp_path):
    # A credential straddling the 80-char truncation boundary must NOT leave a
    # partial token: redaction runs on the FULL text before truncation.
    s = _store(tmp_path)
    pad = "x" * 70
    s.remember(pad + " reach me at alice\x40secret.example now", "allow", scope="always", actor="tester")
    on_disk = (tmp_path / "decisions.jsonl").read_text("utf-8")
    assert "alice\x40secret.example" not in on_disk
    assert "alice@secr" not in on_disk                    # no partial token either


def test_erase_preserves_unparseable_lines(tmp_path):
    # A corrupt/partial line (e.g. a crash mid-write) must survive a purge —
    # erase must not silently drop other records.
    p = tmp_path / "decisions.jsonl"
    s = _store(tmp_path)
    s.remember("good record", "allow", scope="always", actor="tester")
    with p.open("a", encoding="utf-8") as fh:
        fh.write("{ this is not valid json\n")
    s.erase()
    text = p.read_text("utf-8")
    assert "{ this is not valid json" in text             # corrupt line preserved


def test_erase_targets_a_single_hash(tmp_path):
    s = _store(tmp_path)
    s.remember("keep me", "allow", scope="always", actor="tester")
    rec_b = s.remember("scrub me", "allow", scope="always", actor="tester")
    s.erase(pattern_hash=rec_b.pattern_hash)
    on_disk = (tmp_path / "decisions.jsonl").read_text("utf-8")
    assert "scrub me" not in on_disk
    assert "keep me" in on_disk                                       # other record untouched


# ── CL1: a durable "block" persists through the egress helper ────────────────

def test_persist_block_always_is_durable(tmp_path):
    s = _store(tmp_path)
    # CL3: a durable always-clearance now carries the operator identity.
    _persist_scope_decision(s, "block this forever", "block", ["scope:always"],
                            "user blocked", actor="operator")
    assert s.recall("block this forever") == "block"


def test_persist_anonymous_always_is_not_durable(tmp_path):
    # CL3 fail-closed: without an operator identity, an always-clearance is not
    # persisted by the egress helper (it re-prompts) rather than silently saved.
    s = _store(tmp_path)
    _persist_scope_decision(s, "anon forever", "allow", ["scope:always"], "no actor")
    assert s.recall("anon forever") is None


def test_persist_allow_session_is_durable(tmp_path):
    s = _store(tmp_path)
    _persist_scope_decision(s, "ok this session", "allow", ["scope:session"], "user ok")
    assert s.recall("ok this session") == "allow"


def test_persist_once_marker_is_not_durable(tmp_path):
    s = _store(tmp_path)
    _persist_scope_decision(s, "just once", "allow", ["scope:once"], "r")
    assert s.recall("just once") is None                             # 'once' is not remembered
