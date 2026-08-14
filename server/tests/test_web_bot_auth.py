# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Web Bot Auth / RFC 9421 verifier core (P2): a valid signature verifies, and
every tamper, staleness, key, and identity-binding failure is refused
fail-closed. The signer here is the round-trip mirror of the verifier."""
from __future__ import annotations

import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from workspaces import agent_keys as AK
from workspaces import web_bot_auth as W

COVERED = ["@authority", "signature-agent"]


def _key():
    priv = Ed25519PrivateKey.generate()
    pem = priv.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pem


def _lookup(tmp_path):
    return lambda k: AK.get_agent_key(k, root=str(tmp_path))


def _signed(tmp_path, *, agent="crawler-x", authority="api.example.com",
            created=None, expires=None, priv=None, keyid=None):
    """Register a key (unless one is supplied) and return (headers, ctx, keyid)."""
    created = int(time.time()) if created is None else created
    if priv is None:
        priv, pem = _key()
        keyid = AK.register_agent_key(agent, pem, root=str(tmp_path))["keyid"]
    ctx = W.RequestContext(authority=authority,
                           headers={"signature-agent": f'"{agent}"'})
    hdrs = W.sign(priv, agent=agent, keyid=keyid, covered=COVERED, ctx=ctx,
                  created=created, expires=expires)
    return {k.lower(): v for k, v in hdrs.items()}, ctx, keyid


def test_valid_signature_verifies(tmp_path):
    now = int(time.time())
    vh, ctx, keyid = _signed(tmp_path, created=now)
    v = W.verify(vh, ctx=ctx, key_lookup=_lookup(tmp_path), now=now)
    assert v.verified and v.agent == "crawler-x" and v.keyid == keyid


def test_tampered_component_fails(tmp_path):
    now = int(time.time())
    vh, _, _ = _signed(tmp_path, authority="api.example.com", created=now)
    evil = W.RequestContext(authority="evil.example.com",
                            headers={"signature-agent": '"crawler-x"'})
    assert not W.verify(vh, ctx=evil, key_lookup=_lookup(tmp_path), now=now).verified


def test_wrong_key_fails(tmp_path):
    now = int(time.time())
    _, pem_a = _key()
    keyid_a = AK.register_agent_key("crawler-x", pem_a, root=str(tmp_path))["keyid"]
    priv_b, _ = _key()                       # sign with B, claim A's keyid
    vh, ctx, _ = _signed(tmp_path, priv=priv_b, keyid=keyid_a, created=now)
    v = W.verify(vh, ctx=ctx, key_lookup=_lookup(tmp_path), now=now)
    assert not v.verified and "did not verify" in v.reason


def test_expired_and_stale_fail(tmp_path):
    now = int(time.time())
    vh, ctx, _ = _signed(tmp_path, created=now - 10, expires=now - 1)
    assert not W.verify(vh, ctx=ctx, key_lookup=_lookup(tmp_path), now=now).verified
    vh2, ctx2, _ = _signed(tmp_path, created=now - 10_000)   # no expires -> too old
    assert not W.verify(vh2, ctx=ctx2, key_lookup=_lookup(tmp_path), now=now).verified


def test_future_created_fails(tmp_path):
    now = int(time.time())
    vh, ctx, _ = _signed(tmp_path, created=now + 10_000)
    assert not W.verify(vh, ctx=ctx, key_lookup=_lookup(tmp_path), now=now).verified


def test_unknown_and_revoked_key_fail(tmp_path):
    now = int(time.time())
    vh, ctx, keyid = _signed(tmp_path, created=now)
    assert not W.verify(vh, ctx=ctx, key_lookup=lambda k: None, now=now).verified
    AK.revoke_agent_key(keyid, root=str(tmp_path))
    assert not W.verify(vh, ctx=ctx, key_lookup=_lookup(tmp_path), now=now).verified


def test_agent_binding_mismatch_fails(tmp_path):
    now = int(time.time())
    vh, ctx, _ = _signed(tmp_path, agent="crawler-x", created=now)
    v = W.verify(vh, ctx=ctx, key_lookup=_lookup(tmp_path), now=now,
                 expected_agent="impostor")
    assert not v.verified and "does not match" in v.reason


def test_missing_headers_fail_closed(tmp_path):
    ctx = W.RequestContext(authority="api.example.com")
    assert not W.verify({}, ctx=ctx, now=int(time.time())).verified
    assert not W.verify({"signature-input": "sig1=()"}, ctx=ctx).verified


def test_unsupported_alg_fails(tmp_path):
    now = int(time.time())
    vh, ctx, _ = _signed(tmp_path, created=now)
    vh["signature-input"] = vh["signature-input"].replace(
        'alg="ed25519"', 'alg="rsa-v1_5-sha256"')
    v = W.verify(vh, ctx=ctx, key_lookup=_lookup(tmp_path), now=now)
    assert not v.verified and "unsupported alg" in v.reason


def test_verify_never_raises_on_garbage(tmp_path):
    ctx = W.RequestContext(authority="x")
    for bad in ({"signature-input": "!!!", "signature": ":::"},
                {"signature-input": "sig1=(", "signature": "sig1=:@@@:"},
                {"signature-input": None, "signature": 123}):
        assert W.verify(bad, ctx=ctx).verified is False


def test_signature_base_is_rfc9421_shaped(tmp_path):
    """The base is the covered-component lines then @signature-params, newline-
    joined — the exact structure a third-party RFC 9421 signer would produce."""
    ctx = W.RequestContext(authority="api.example.com",
                           headers={"signature-agent": '"crawler-x"'})
    params = '("@authority" "signature-agent");created=1;keyid="k";alg="ed25519";tag="web-bot-auth"'
    base = W.build_signature_base(COVERED, params, ctx).decode()
    assert base == (
        '"@authority": api.example.com\n'
        '"signature-agent": "crawler-x"\n'
        f'"@signature-params": {params}'
    )


def test_verify_accepts_a_conformant_third_party_signature():
    """Interop conformance — a golden vector produced by the CONFORMANT
    ``http-message-signatures`` library (RFC 9421 profile, Ed25519), NOT by this
    module's own signer. Proves ``verify`` interoperates with an independent,
    spec-conformant signer (the same core cloudflare/web-bot-auth and pyauth use),
    guarding against a hand-rolled base/parse divergence that a self-round-trip
    would miss. The vector is baked in (fixed key + fixed ``created``), so there is
    no runtime dependency; ``now`` is pinned to the signature's ``created`` so the
    freshness window holds. Regenerate with ``http-message-signatures`` if the
    profile changes."""
    pub_pem = (
        "-----BEGIN PUBLIC KEY-----\n"
        "MCowBQYDK2VwAyEAA6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=\n"
        "-----END PUBLIC KEY-----\n"
    )
    headers = {
        "signature-input": (
            'sig1=("@authority" "signature-agent");created=1700000000;'
            'keyid="k1";alg="ed25519"'
        ),
        "signature": (
            "sig1=:QL91idKX1FczmlUMBAfkNsoqarhXFPoCRkHORyc4HGnjuWrGM7S99Hqo"
            "MvTv92RMCI1eHsT3LTy7LMe6SPtaAg==:"
        ),
        "signature-agent": '"crawler-x"',
    }
    ctx = W.RequestContext(authority="api.example.com", method="POST",
                           path="/v1/messages",
                           headers={"signature-agent": '"crawler-x"'})
    v = W.verify(headers, ctx=ctx,
                 key_lookup=lambda k: {"agent": "crawler-x", "public_key_pem": pub_pem},
                 now=1700000000)
    assert v.verified, v.reason
    assert v.keyid == "k1" and v.agent == "crawler-x"
