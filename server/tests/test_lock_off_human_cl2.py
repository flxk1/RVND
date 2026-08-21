# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""CL2 — Privacy Lock OFF must not auto-allow a would-be refuse.

gate_for_cloud() returned `allow` the instant a folder had lock disabled —
before any detection, with no audit — so policy alone turned a would-be refuse
into a silent auto-allow. Off must disable ENFORCEMENT, not DETECTION: a would-be
refuse routes to a person (ask_user at APPROVE+), the bypass is always audited,
and only a clean text passes silently. Lock/Shield panel."""
from __future__ import annotations

import pytest

from rvnd import disable_lock_for_deployment
from rvnd.lock import l0_bridge
from rvnd.lock.core import AuditLog, Mode
from rvnd.lock.gate import gate_for_cloud
from rvnd.lock.oversight import OversightLevel

# A text that the gate genuinely refuses (an email is a Tier-B hit; STRICT mode
# makes any finding a refuse, so the would-be action is unambiguous).
REFUSING = "please forward this to alice\x40example.com right away"


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_L0_TRANSPORT", "inprocess")
    monkeypatch.setenv("WORKSPACE_L0_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    l0_bridge._set_l0_available(True)
    f = tmp_path / "vault"
    f.mkdir()
    return f


def _log_root(folder):
    return str(folder.parent / "logs")


def _bypass_events(audit_path):
    if not audit_path.exists():
        return []
    import json
    return [json.loads(l) for l in audit_path.read_text().splitlines()
            if l.strip() and json.loads(l).get("kind") == "lock_bypass"]


# ── negative control: with lock ON, the text genuinely refuses ───────────────

def test_negative_control_lock_on_refuses(folder):
    d = gate_for_cloud(REFUSING, folder_context=folder,
                       oversight=OversightLevel.APPROVE, mode=Mode.STRICT)
    assert d.action == "ask_user"        # lock on: a real refuse → ask_user at APPROVE
    assert d.lock_bypassed is False      # not a bypass — the lock is doing its job


# ── lock OFF: clean text still passes silently ───────────────────────────────

def test_lockoff_clean_text_allows_without_bypass(folder):
    disable_lock_for_deployment(accepted_by="alex", log_root=_log_root(folder))
    d = gate_for_cloud("perfectly harmless sentence", folder_context=folder,
                       oversight=OversightLevel.APPROVE, mode=Mode.STANDARD)
    assert d.action == "allow"
    assert d.lock_bypassed is False


# ── lock OFF: a would-be refuse routes to a person + is audited (CL2) ─────────

def test_lockoff_would_refuse_routes_to_ask_user_and_audits(folder, tmp_path):
    disable_lock_for_deployment(accepted_by="alex", log_root=_log_root(folder))
    audit = AuditLog(tmp_path / "audit.jsonl")
    d = gate_for_cloud(REFUSING, folder_context=folder,
                       oversight=OversightLevel.APPROVE, mode=Mode.STRICT, audit=audit)
    assert d.action == "ask_user"        # NOT a silent allow
    assert d.lock_bypassed is True
    assert d.would_have == "refuse"
    events = _bypass_events(tmp_path / "audit.jsonl")
    assert len(events) == 1 and events[0]["would_have"] == "refuse"


@pytest.mark.parametrize("ov", [OversightLevel.AUTONOMOUS, OversightLevel.NOTIFY,
                                OversightLevel.REVIEW])
def test_lockoff_would_refuse_low_oversight_allows_but_audits(folder, tmp_path, ov):
    # Below APPROVE: the bypass passes (off = no enforcement) — but it is
    # AUDITED, never silent. (The old code allowed it with no record at all.)
    disable_lock_for_deployment(accepted_by="alex", log_root=_log_root(folder))
    audit = AuditLog(tmp_path / "audit.jsonl")
    d = gate_for_cloud(REFUSING, folder_context=folder,
                       oversight=ov, mode=Mode.STRICT, audit=audit)
    assert d.action == "allow"
    assert d.lock_bypassed is True and d.would_have == "refuse"
    ev = _bypass_events(tmp_path / "audit.jsonl")
    assert len(ev) == 1 and ev[0]["final_action"] == "allow" and ev[0]["oversight"]


def test_bypass_audit_records_oversight_and_final_action(folder, tmp_path):
    disable_lock_for_deployment(accepted_by="alex", log_root=_log_root(folder))
    audit = AuditLog(tmp_path / "audit.jsonl")
    gate_for_cloud(REFUSING, folder_context=folder,
                   oversight=OversightLevel.APPROVE, mode=Mode.STRICT, audit=audit)
    ev = _bypass_events(tmp_path / "audit.jsonl")[0]
    assert ev["would_have"] == "refuse"
    assert ev["final_action"] == "ask_user"   # reconstruct: it went to a person
    assert ev["oversight"]                     # the oversight that drove it


def test_lockoff_bypass_fails_closed_when_audit_write_fails(folder):
    # FAIL-CLOSED: if an audit sink is configured but the bypass write fails, the
    # bypass must NOT proceed — off disables enforcement, never the audit trail.
    disable_lock_for_deployment(accepted_by="alex", log_root=_log_root(folder))

    class BoomAudit:
        def write_text(self, *a, **k):
            pass                          # let detection's audit succeed
        def write_bypass(self, *a, **k):
            raise OSError("disk full")    # the bypass record fails

    d = gate_for_cloud(REFUSING, folder_context=folder,
                       oversight=OversightLevel.APPROVE, mode=Mode.STRICT, audit=BoomAudit())
    assert d.action == "refuse"
    assert "could not be audited" in d.reason


def test_lockoff_pattern_preview_is_redacted(folder):
    # The flagged content must not ride along on the GateDecision (e.g. into an
    # ask_user prompt): pattern_preview is redacted.
    disable_lock_for_deployment(accepted_by="alex", log_root=_log_root(folder))
    d = gate_for_cloud(REFUSING, folder_context=folder,
                       oversight=OversightLevel.APPROVE, mode=Mode.STRICT)
    assert d.action == "ask_user"
    assert "alice\x40example.com" not in d.pattern_preview


def test_write_bypass_rejects_invalid_would_have(tmp_path):
    from rvnd.lock.core import TextDecision
    audit = AuditLog(tmp_path / "a.jsonl")
    td = TextDecision(action="refuse", findings=[], source="document")
    with pytest.raises(ValueError):
        audit.write_bypass("allow", td)   # only 'refuse'/'minimise' are valid
