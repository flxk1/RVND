# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Per-track binding, step 4 — the access binding: a credential *reference* on the
egress track, resolved fail-closed at call time, with the secret NEVER on the
chain.

Two units under test:
  * credential_resolver — parse/resolve/status, all fail-closed;
  * connectors.register_connector — the new credential_ref field (egress-only,
    known-scheme-only, reference-not-secret), surfaced by list_connectors.
"""
from __future__ import annotations

import os

import pytest

from workspaces import connectors
from workspaces.lock import credential_resolver as C


# ---- credential_resolver: parsing --------------------------------------------

@pytest.mark.parametrize("ref,expect", [
    ("env:JIRA_TOKEN", ("env", "JIRA_TOKEN")),
    ("keydir:github/pat", ("keydir", "github/pat")),
    ("oidc:legal-sso", ("oidc", "legal-sso")),
    ("spiffe://cluster/agent", ("spiffe", "//cluster/agent")),
    ("  env:X  ", ("env", "X")),
])
def test_parse_ref_valid(ref, expect):
    assert C.parse_ref(ref) == expect
    assert C.is_valid_ref(ref)


@pytest.mark.parametrize("ref", [
    None, "", "   ", "no-colon", "env:", ":locator", "vault:secret", "RAWSECRET", 123,
])
def test_parse_ref_rejects_malformed_or_unknown(ref):
    assert C.parse_ref(ref) is None
    assert not C.is_valid_ref(ref)


# ---- credential_resolver: env scheme -----------------------------------------

def test_env_resolves_when_present(monkeypatch):
    monkeypatch.setenv("MY_EGRESS_TOK", "s3cr3t")
    assert C.resolve_secret("env:MY_EGRESS_TOK") == "s3cr3t"
    assert C.arm_status("env:MY_EGRESS_TOK") == C.ARMED


def test_env_fail_closed_when_absent(monkeypatch):
    monkeypatch.delenv("MISSING_TOK", raising=False)
    assert C.resolve_secret("env:MISSING_TOK") is None
    assert C.arm_status("env:MISSING_TOK") == C.UNPLUGGED


# ---- credential_resolver: keydir scheme (mode + traversal fail-closed) --------

def test_keydir_resolves_only_when_0600(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path))
    secret = tmp_path / "github" / "pat"
    secret.parent.mkdir(parents=True)
    secret.write_text("ghp_abc\n")
    os.chmod(secret, 0o600)
    assert C.resolve_secret("keydir:github/pat") == "ghp_abc"
    assert C.arm_status("keydir:github/pat") == C.ARMED
    # loosen the mode -> fail-closed (a group/world-readable secret cannot arm)
    os.chmod(secret, 0o644)
    assert C.resolve_secret("keydir:github/pat") is None
    assert C.arm_status("keydir:github/pat") == C.UNPLUGGED


def test_keydir_missing_is_unplugged(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path))
    assert C.resolve_secret("keydir:nope") is None
    assert C.arm_status("keydir:nope") == C.UNPLUGGED


def test_keydir_refuses_path_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_KEY_DIR", str(tmp_path / "keys"))
    (tmp_path / "keys").mkdir()
    outside = tmp_path / "outside"
    outside.write_text("nope"); os.chmod(outside, 0o600)
    assert C.resolve_secret("keydir:../outside") is None


# ---- credential_resolver: no adapter yet, and status/describe ----------------

def test_oidc_and_spiffe_unplugged_no_adapter():
    assert C.resolve_secret("oidc:legal-sso") is None
    assert C.arm_status("oidc:legal-sso") == C.UNPLUGGED
    assert C.arm_status("spiffe://x") == C.UNPLUGGED


def test_status_no_cable_when_no_ref():
    assert C.arm_status(None) == C.NO_CABLE
    assert C.arm_status("") == C.NO_CABLE


def test_describe_never_leaks_secret(monkeypatch):
    monkeypatch.setenv("TOK", "leak-me")
    d = C.describe("env:TOK")
    assert d == {"credential_ref": "env:TOK", "scheme": "env",
                 "status": C.ARMED, "enforceable": True}
    assert "leak-me" not in repr(d)
    # oidc is a valid ref but not locally enforceable (attested, not brokered)
    assert C.describe("oidc:sso")["enforceable"] is False


# ---- connectors: the credential_ref field ------------------------------------

def test_egress_connector_stores_reference_not_secret(tmp_path):
    f = str(tmp_path); lr = str(tmp_path / "l")
    connectors.register_connector(f, connector_id="out", role="egress",
                                  channel="api", credential_ref="env:JIRA_TOKEN",
                                  log_root=lr)
    got = {c["connector_id"]: c for c in connectors.list_connectors(f, log_root=lr)}
    assert got["out"]["credential_ref"] == "env:JIRA_TOKEN"
    # the chain carries the REFERENCE, never a secret value
    from workspaces.mutation_log import MutationLog
    raw = MutationLog(f, log_root=lr).log_file.read_bytes()
    assert b"env:JIRA_TOKEN" in raw and b"JIRA_TOKEN" in raw   # the ref, fine
    # (there is no secret to leak — the point is the field is a ref by construction)


def test_credential_ref_rejected_on_non_egress(tmp_path):
    f = str(tmp_path); lr = str(tmp_path / "l")
    with pytest.raises(ValueError, match="only valid on an egress"):
        connectors.register_connector(f, connector_id="in", role="ingress",
                                      channel="api", credential_ref="env:X", log_root=lr)


def test_malformed_credential_ref_rejected(tmp_path):
    f = str(tmp_path); lr = str(tmp_path / "l")
    with pytest.raises(ValueError, match="known-scheme reference"):
        connectors.register_connector(f, connector_id="out", role="egress",
                                      channel="api", credential_ref="ghp_rawsecret",
                                      log_root=lr)


def test_no_credential_ref_is_none(tmp_path):
    f = str(tmp_path); lr = str(tmp_path / "l")
    connectors.register_connector(f, connector_id="out", role="egress",
                                  channel="api", log_root=lr)
    got = {c["connector_id"]: c for c in connectors.list_connectors(f, log_root=lr)}
    assert got["out"]["credential_ref"] is None
