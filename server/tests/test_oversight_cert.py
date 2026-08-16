# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND consumes oversight-certificate — prove the portable export round-trips.

RVND is a *consumer* of the upstream ``oversight-certificate`` package (its own
repo). These tests exercise ``workspaces.oversight_cert``: issue a certificate
from RVND-side data, then re-check it the way a third-party auditor would —
offline, from the DSSE envelope and a public verify function alone.

Skips unless the ``[oversight-cert]`` extra is installed (``oversight-certificate``
+ ``rfc8785``), so a base install without the extra collects clean.
"""
import base64
import json

import pytest

pytest.importorskip("oversight_certificate")
pytest.importorskip("rfc8785")

from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from workspaces import oversight_cert as oc  # noqa: E402


def _ephemeral_signer():
    """A throwaway Ed25519 keypair as (sign, verify_sig) closures — the certificate
    never sees the key, only these functions (closed I/O)."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()

    def sign(data: bytes) -> bytes:
        return priv.sign(data)

    def verify_sig(data: bytes, sig: bytes) -> bool:
        try:
            pub.verify(sig, data)
            return True
        except (InvalidSignature, ValueError):
            return False

    return sign, verify_sig


def test_decided_certificate_roundtrips():
    sign, verify_sig = _ephemeral_signer()
    env = oc.certify_decision(
        decision_id="dec-1", action="pay-invoice#4200",
        human_id="alice", qualification="controller",
        evidence=("sha256:abc",), at="2026-08-13T10:00:00Z",
        credential_not_after="2027-01-01T00:00:00Z", sign=sign)
    rep = oc.recheck(env, verify_sig=verify_sig, now="2026-08-13T10:05:00Z")
    assert rep.ok, [f.code for f in rep.findings]


def test_credential_lapsed_before_decision_is_caught():
    sign, verify_sig = _ephemeral_signer()
    env = oc.certify_decision(
        decision_id="dec-2", action="supplier-credit-scoring",
        human_id="bob", qualification="reviewer",
        evidence=("sha256:def",), at="2026-08-13T10:00:00Z",
        credential_not_after="2026-08-01T00:00:00Z",  # lapsed BEFORE the decision
        sign=sign)
    rep = oc.recheck(env, verify_sig=verify_sig, now="2026-08-13T10:05:00Z")
    assert not rep.ok
    assert any(f.code == "unqualified-at-decision" for f in rep.findings)


def test_credential_lapse_after_decision_still_valid():
    # The distinctive rule: a credential valid AT decision time stays valid even
    # if it lapses later.
    sign, verify_sig = _ephemeral_signer()
    env = oc.certify_decision(
        decision_id="dec-2b", action="pay-invoice",
        human_id="bob", qualification="reviewer",
        evidence=("sha256:def",), at="2026-08-13T10:00:00Z",
        credential_not_after="2026-08-13T10:00:01Z",  # lapses just AFTER the decision
        sign=sign)
    rep = oc.recheck(env, verify_sig=verify_sig, now="2027-01-01T00:00:00Z")
    assert rep.ok, [f.code for f in rep.findings]


def test_escalation_certificate_roundtrips_without_a_human():
    sign, verify_sig = _ephemeral_signer()
    env = oc.certify_escalation(
        decision_id="esc-1", action="delete-account",
        escalated_to="dpo-queue", evidence=("sha256:ghi",),
        at="2026-08-13T11:00:00Z", sign=sign)
    assert "human" not in json.loads(base64.b64decode(env["payload"]))
    rep = oc.recheck(env, verify_sig=verify_sig, now="2026-08-13T11:01:00Z")
    assert rep.ok, [f.code for f in rep.findings]


def test_tamper_breaks_signature():
    sign, verify_sig = _ephemeral_signer()
    env = oc.certify_decision(
        decision_id="dec-3", action="pay-invoice",
        human_id="alice", qualification="controller",
        evidence=("sha256:abc",), at="2026-08-13T10:00:00Z", sign=sign)
    import rfc8785
    payload = json.loads(base64.b64decode(env["payload"]))
    payload["action"] = "pay-invoice-TAMPERED"
    env["payload"] = base64.b64encode(rfc8785.dumps(payload)).decode("ascii")
    rep = oc.recheck(env, verify_sig=verify_sig, now="2026-08-13T10:05:00Z")
    assert not rep.ok
    assert any(f.code == "bad-signature" for f in rep.findings)


def test_required_basis_mismatch_is_flagged():
    sign, verify_sig = _ephemeral_signer()
    env = oc.certify_decision(
        decision_id="dec-4", action="pay-invoice",
        human_id="alice", qualification="controller",
        evidence=("sha256:abc",), at="2026-08-13T10:00:00Z", sign=sign)
    rep = oc.recheck(env, verify_sig=verify_sig, now="2026-08-13T10:05:00Z",
                     required_basis="gdpr-2016-679-art-22")
    assert not rep.ok
    assert any(f.code == "wrong-basis" for f in rep.findings)


def test_rvnd_identity_key_signs_portable_certificate(tmp_path, monkeypatch):
    # RVND's OWN identity keypair (signing.py) signs the portable certificate:
    # one key anchors both the internal chain and this portable proof.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    sign, verify_sig, keyid = oc.rvnd_ed25519_signer()
    assert keyid, "expected RVND public-key fingerprint as keyid"
    env = oc.certify_decision(
        decision_id="dec-5", action="clear-quarantine",
        human_id="alice", qualification="controls", evidence=("sha256:xyz",),
        at="2026-08-13T12:00:00Z", sign=sign, keyid=keyid)
    assert env["signatures"][0]["keyid"] == keyid
    rep = oc.recheck(env, verify_sig=verify_sig, now="2026-08-13T12:00:01Z")
    assert rep.ok, [f.code for f in rep.findings]


def test_emit_decision_certificate_end_to_end(tmp_path, monkeypatch):
    # RVND emits + persists a portable certificate for a recorded decision, signed
    # by its own identity key, then re-checks it the way the app's verify op would.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    folder = tmp_path / "ws"
    folder.mkdir()
    logs = tmp_path / "logs"
    env = oc.emit_decision_certificate(
        str(folder), actor="alice", action="pay-invoice#4200",
        evidence_refs=["sha256:abc"], at="2026-08-13T10:00:00Z",
        audit_id="evt-1", log_root=str(logs))
    assert env is not None
    assert env["signatures"][0]["keyid"], "expected RVND keyid stamped on the envelope"
    # persisted BESIDE the chain, not on it
    assert list(logs.rglob("oversight_certs.jsonl")), "expected the sidecar to be written"
    # re-check via the app-facing verifier (this host's identity pubkey)
    rep = oc.verify_certificate(env, now="2026-08-13T10:05:00Z")
    assert rep["ok"], rep["findings"]


def test_emit_returns_none_without_evidence():
    # No evidence and no audit_id anchor -> cannot form a coherent DECIDED cert ->
    # None, never an exception (the decision must not break).
    assert oc.emit_decision_certificate(
        "/nonexistent", actor="alice", action="x", evidence_refs=[],
        at="2026-08-13T10:00:00Z", audit_id="") is None


def test_verify_op_through_workspace_workflow():
    # The app-facing op: verify a certificate through the workspace_workflow facade,
    # against a supplied PEM public key (the offline third-party path).
    import rfc8785
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from workspaces import mcp_server
    priv = Ed25519PrivateKey.generate()
    pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    env = oc.certify_decision(
        decision_id="op-1", action="pay-invoice", human_id="alice",
        qualification="controller", evidence=("sha256:abc",),
        at="2026-08-13T10:00:00Z", sign=lambda b: priv.sign(b))
    r = mcp_server.workspace_workflow(
        "oversight_cert_verify",
        {"envelope": env, "now": "2026-08-13T10:05:00Z", "public_key_pem": pem})
    assert r["ok"], r["findings"]
    # a tampered envelope fails through the same op
    payload = json.loads(base64.b64decode(env["payload"]))
    payload["action"] = "pay-invoice-TAMPERED"
    bad = {**env, "payload": base64.b64encode(rfc8785.dumps(payload)).decode("ascii")}
    r2 = mcp_server.workspace_workflow(
        "oversight_cert_verify",
        {"envelope": bad, "now": "2026-08-13T10:05:00Z", "public_key_pem": pem})
    assert not r2["ok"]
    assert any(f["code"] == "bad-signature" for f in r2["findings"])


def test_govlive_board_carries_certificates(tmp_path, monkeypatch):
    # A returned decision's certificate is tracked on the EXISTING govlive board
    # (a separate `certificates` field), not a new surface / second courier.
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("WORKSPACES_ALLOW_UNREGISTERED", "1")
    folder = tmp_path / "ws"
    folder.mkdir()
    logs = tmp_path / "logs"
    env = oc.emit_decision_certificate(
        str(folder), actor="alice", action="pay-invoice",
        evidence_refs=["sha256:abc"], at="2026-08-13T10:00:00Z",
        audit_id="evt-9", log_root=str(logs))
    assert env is not None
    from workspaces.governance_live import governance_live
    board = governance_live(str(folder), log_root=str(logs))
    assert board["ok"]
    assert len(board.get("certificates", [])) == 1
    assert any(c.get("audit_id") == "evt-9" for c in board.get("certificates", []))


# ── 0.2.0: assistance / independence — reviewer-declared, never runtime-inferred ──

def test_assistance_undeclared_reads_undeclared_never_unaided():
    # The runtime NEVER infers how the human reviewed: with no declaration the
    # certificate is UNDECLARED, never UNAIDED. A system-minted "unaided" would be an
    # unfalsifiable claim about a human's private conduct — worse than absent.
    sign, verify_sig = _ephemeral_signer()
    env = oc.certify_decision(
        decision_id="dec-u", action="act", human_id="h", qualification="controller",
        evidence=("sha256:x",), at="2026-08-13T10:00:00Z", sign=sign)
    rep = oc.recheck(env, verify_sig=verify_sig, now="2026-08-13T10:05:00Z")
    assert rep.ok
    assert rep.independence.value == "undeclared"
    assert rep.independence.value != "unaided"          # the load-bearing guarantee


def test_reviewer_declared_assistance_is_recorded():
    sign, verify_sig = _ephemeral_signer()

    def indep(assistance):
        env = oc.certify_decision(
            decision_id="dec-a", action="act", human_id="h", qualification="controller",
            evidence=("sha256:x",), at="2026-08-13T10:00:00Z", sign=sign,
            assistance=assistance)
        return oc.recheck(env, verify_sig=verify_sig,
                          now="2026-08-13T10:05:00Z").independence.value

    assert indep({"aid": "unaided"}) == "unaided"                     # reviewer said unaided
    assert indep({"aid": "model", "system": "gpt",
                  "same_model_family": True}) == "model-correlated"    # correlated failure
    assert indep({"aid": "model", "system": "gpt"}) == "model-undetermined"  # not a guess
