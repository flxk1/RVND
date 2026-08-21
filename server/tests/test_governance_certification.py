# SPDX-License-Identifier: AGPL-3.0-only
"""The GovernanceCertification predicate — shape, and what backs each claim.

`governance_cert` calls itself "the one owned artifact of the ecosystem" and its
docstring pointed at `scratchpad/governance-certification-v1.schema.json`, a file
that existed nowhere and was untracked. So the shape of the predicate was
whatever the code happened to emit, with nothing to drift against.

The schema now lives in `docs/evidence/` and is derived from real output. These
tests keep the two together, and — more importantly — pin what each pillar is
actually entitled to claim.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from rvnd.governance_cert import build_predicate

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "docs" / "evidence"
     / "governance-certification-v1.schema.json").read_text(encoding="utf-8"))


def _marker(**over):
    m = {
        "verdict": "hold-approved", "action_class": "shell.exec",
        "at": "2026-08-21T12:00:00Z", "qualification": "operator",
        "grounded": True, "traffic_light": "green",
        "mechanism": "claude-code:PreToolUse", "audit_id": "evt-123",
        "folder": "/ws", "policy_digest": "sha256:abc",
        "evidence": [{"span": "rm -rf", "at": 0}],
    }
    m.update(over)
    return m


def test_the_minted_predicate_matches_the_committed_schema():
    jsonschema.validate(build_predicate(_marker()), SCHEMA)


def test_a_permit_verdict_also_matches():
    """The other minting path: a governed action the gate allowed outright."""
    jsonschema.validate(build_predicate(_marker(verdict="permit")), SCHEMA)


def test_the_schema_refuses_a_predicate_that_lost_a_pillar():
    """A schema that accepts anything proves nothing."""
    broken = build_predicate(_marker())
    del broken["enforced"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, SCHEMA)


def test_a_permitted_action_does_not_claim_human_oversight():
    """`permit` means the gate cleared it with no human step. Claiming a human
    decided would be the certificate asserting something that did not happen."""
    pred = build_predicate(_marker(verdict="permit"))
    assert pred["overseen"]["required"] is False
    assert "disposition" not in pred["overseen"], (
        "a permitted action recorded a human disposition — no human acted")


def test_a_held_action_records_that_a_human_decided():
    pred = build_predicate(_marker(verdict="hold-approved"))
    assert pred["overseen"]["required"] is True
    assert pred["overseen"]["disposition"] == "DECIDED"


def test_the_enforced_pillar_is_backed_by_the_marker_not_by_itself():
    """The honest statement of what this pillar proves.

    `blocked_unless_permitted` is emitted as a constant `true` — it is not
    derived from the marker and cannot come out false, so on its own it carries
    no information. What makes it true is the CALL PATH: hook.py mints only when
    a PreToolUse marker file exists, and consumes it.

    That marker is unsigned JSON at a predictable path under
    `<log_root>/hook-pending/`. Anyone who can write there can cause a validly
    signed certificate to be minted for an action that was never held. This test
    exists to keep that visible: the pillar's strength is the marker's
    authenticity, and today the marker has none. If the marker ever becomes
    signed, this test should assert that instead.
    """
    pred = build_predicate(_marker(mechanism="anything", audit_id=""))
    assert pred["enforced"]["blocked_unless_permitted"] is True, (
        "still a constant — if this became derived, tighten this test")

    import inspect

    from rvnd import hook
    src = inspect.getsource(hook)
    assert "_marker_path" in src and "unlink" in src, (
        "the enforcement claim rests on a marker being present and consumed "
        "exactly once; if that changed, what backs the pillar changed too")
    assert "sign" not in inspect.getsource(hook._mark_held), (
        "the marker is now signed — update this test and the schema note: the "
        "enforced pillar is finally backed by something unforgeable")


def test_grounded_digest_covers_the_evidence_it_names():
    """The digest must be of THIS evidence, or it certifies someone else's."""
    a = build_predicate(_marker(evidence=[{"span": "x", "at": 0}]))
    b = build_predicate(_marker(evidence=[{"span": "y", "at": 0}]))
    assert a["grounded"]["digest"]["sha256"] != b["grounded"]["digest"]["sha256"]
