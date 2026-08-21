# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The PartyResolver port — the sole seam where identity enters governance.

Pins three things: (1) the default resolver is the local chain registry and
behaviour is unchanged, (2) an installed adapter overrides resolution globally
(every caller of parties.list_parties / route_approvers reaches it), and (3) the
seam returns competence, not identity — an adapter maps a claim to a competence
the language already speaks, so the no-id wall holds.
"""
from __future__ import annotations

import tempfile

import pytest

from rvnd import parties as P
from rvnd import party_resolver as PR


@pytest.fixture
def ws():
    d = tempfile.mkdtemp()
    org = d + "/org"
    import os
    os.makedirs(org)
    yield org, d + "/log"
    PR.set_resolver(None)  # always restore the local default


def test_default_is_local_and_unchanged(ws):
    org, lr = ws
    assert isinstance(PR.get_resolver(), PR.LocalPartyResolver)
    P.register_party(org, "alice", "human", competences=["legal"], log_root=lr)
    rows = P.list_parties(org, log_root=lr)["parties"]
    assert [r["party_id"] for r in rows] == ["alice"]
    assert P.route_approvers(org, "legal", log_root=lr)["count"] == 1
    assert PR.get_resolver().resolve_competences(org, "alice", log_root=lr) == ["legal"]


def test_local_resolver_respects_status(ws):
    org, lr = ws
    P.register_party(org, "bob", "human", competences=["finance"], log_root=lr)
    P.set_party_status(org, "bob", "killed", log_root=lr)
    # killed party holds no live competence and routes to nobody
    assert PR.get_resolver().resolve_competences(org, "bob", log_root=lr) == []
    assert P.route_approvers(org, "finance", log_root=lr)["count"] == 0


def test_adapter_overrides_globally(ws):
    org, lr = ws
    # No local party holds "risk"...
    assert P.route_approvers(org, "risk", log_root=lr)["count"] == 0

    class _SSOAdapter:
        """Maps an external group to the 'risk' competence — claim -> competence."""
        def list_parties(self, folder, kind="", competence="", log_root=None):
            row = {"party_id": "ext-quinn", "party_kind": "human", "name": "Quinn",
                   "role": "", "competences": ["risk"], "channels": [], "owner": "",
                   "purpose": "", "grade": "", "status": "active"}
            rows = [row]
            if kind:
                rows = [r for r in rows if r["party_kind"] == kind]
            if competence:
                rows = [r for r in rows if competence in r["competences"]]
            return {"ok": True, "count": len(rows), "parties": rows}

        def route_approvers(self, folder, competence, log_root=None):
            res = self.list_parties(folder, kind="human", competence=competence)
            return {"ok": True, "competence": competence,
                    "count": res["count"], "approvers": res["parties"]}

        def resolve_competences(self, folder, principal, log_root=None):
            return ["risk"] if principal == "ext-quinn" else []

    PR.set_resolver(_SSOAdapter())
    # ...but the adapter resolves it, and parties.route_approvers reaches it
    # without any call-site change — the seam is global.
    routed = P.route_approvers(org, "risk", log_root=lr)
    assert routed["count"] == 1
    assert routed["approvers"][0]["party_id"] == "ext-quinn"
    assert PR.get_resolver().resolve_competences(org, "ext-quinn") == ["risk"]


def test_set_resolver_none_restores_local(ws):
    PR.set_resolver(PR.LocalPartyResolver())
    PR.set_resolver(None)
    assert isinstance(PR.get_resolver(), PR.LocalPartyResolver)
