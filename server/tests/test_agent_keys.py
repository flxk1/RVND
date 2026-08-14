# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Agent public-key registry (P1): register, resolve, rotate, revoke, expire, and
refuse a bad trust root — fail-closed and path-traversal-safe."""
from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from workspaces import agent_keys as AK


def _ed25519_pem() -> str:
    priv = Ed25519PrivateKey.generate()
    return priv.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()


def test_register_then_resolve_live(tmp_path):
    pem = _ed25519_pem()
    rec = AK.register_agent_key("crawler-x", pem, root=str(tmp_path))
    assert rec["agent"] == "crawler-x" and rec["alg"] == "ed25519"
    got = AK.get_agent_key(rec["keyid"], root=str(tmp_path))
    assert got is not None and got["public_key_pem"] == pem


def test_keyid_is_deterministic(tmp_path):
    pem = _ed25519_pem()
    assert AK.key_id_for(pem) == AK.key_id_for(pem)
    rec = AK.register_agent_key("a", pem, root=str(tmp_path))
    assert rec["keyid"] == AK.key_id_for(pem)


def test_non_ed25519_key_is_refused(tmp_path):
    rsa = generate_private_key(public_exponent=65537, key_size=2048)
    rsa_pem = rsa.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    with pytest.raises(ValueError):
        AK.register_agent_key("a", rsa_pem, root=str(tmp_path))
    with pytest.raises(ValueError):
        AK.register_agent_key("a", "not a pem", root=str(tmp_path))


def test_empty_agent_is_refused(tmp_path):
    with pytest.raises(ValueError):
        AK.register_agent_key("   ", _ed25519_pem(), root=str(tmp_path))


def test_expiry_makes_key_dead(tmp_path):
    now = time.time()
    rec = AK.register_agent_key("a", _ed25519_pem(), expires=now + 10, now=now,
                                root=str(tmp_path))
    assert AK.get_agent_key(rec["keyid"], now=now, root=str(tmp_path)) is not None
    assert AK.get_agent_key(rec["keyid"], now=now + 20, root=str(tmp_path)) is None


def test_revoke_makes_key_dead_but_kept(tmp_path):
    rec = AK.register_agent_key("a", _ed25519_pem(), root=str(tmp_path))
    assert AK.revoke_agent_key(rec["keyid"], root=str(tmp_path)) is True
    assert AK.get_agent_key(rec["keyid"], root=str(tmp_path)) is None
    dead = AK.list_agent_keys(include_dead=True, root=str(tmp_path))
    assert any(r["keyid"] == rec["keyid"] and r["revoked"] for r in dead)
    assert AK.revoke_agent_key("does-not-exist", root=str(tmp_path)) is False


def test_rotation_is_additive(tmp_path):
    a1 = AK.register_agent_key("a", _ed25519_pem(), root=str(tmp_path))
    a2 = AK.register_agent_key("a", _ed25519_pem(), root=str(tmp_path))
    assert a1["keyid"] != a2["keyid"]
    live = AK.list_agent_keys(agent="a", root=str(tmp_path))
    assert {r["keyid"] for r in live} == {a1["keyid"], a2["keyid"]}


def test_list_filters_by_agent(tmp_path):
    AK.register_agent_key("a", _ed25519_pem(), root=str(tmp_path))
    AK.register_agent_key("b", _ed25519_pem(), root=str(tmp_path))
    agents = {r["agent"] for r in AK.list_agent_keys(agent="a", root=str(tmp_path))}
    assert agents == {"a"}


def test_traversal_keyid_is_safe(tmp_path):
    assert AK.get_agent_key("../../etc/passwd", root=str(tmp_path)) is None
    assert AK.get_agent_key("a/b", root=str(tmp_path)) is None
    assert AK.revoke_agent_key("../x", root=str(tmp_path)) is False


def test_reads_never_raise_on_corrupt_file(tmp_path):
    rec = AK.register_agent_key("a", _ed25519_pem(), root=str(tmp_path))
    (AK._keys_dir(str(tmp_path)) / f"{rec['keyid']}.json").write_text(
        "{ not json", encoding="utf-8")
    assert AK.get_agent_key(rec["keyid"], root=str(tmp_path)) is None
    assert AK.list_agent_keys(root=str(tmp_path)) == []


def test_missing_dir_is_empty(tmp_path):
    absent = str(tmp_path / "nope")
    assert AK.list_agent_keys(root=absent) == []
    assert AK.get_agent_key("whatever", root=absent) is None
