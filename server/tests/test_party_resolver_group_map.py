# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""GroupMapResolver — the identity-map adapter for the PartyResolver port.

Pins: (1) resolve_competences answers from the declared groups->competences
map at query time (a map edit takes effect on the next call), (2) no map,
unknown principal, unmapped group, or a failing groups source resolve to []
— fail closed, (3) roster reads delegate to the local chain registry and the
adapter registers nothing, (4) set_resolver installs it globally and
set_resolver(None) restores the local default untouched.

Run: python -m pytest server/tests/test_party_resolver_group_map.py -q
"""
from __future__ import annotations

import pytest

from workspaces import parties as P
from workspaces import party_resolver as PR

MAP_YML = """\
groups:
  sg-dpo-team:
    competences: [data-protection]
  sg-engineering:
    competences: [engineering, security]
"""

GROUPS = {
    "dana\x40corp.example": ["sg-dpo-team"],
    "eng\x40corp.example": ["sg-engineering", "sg-unmapped"],
    "lost\x40corp.example": ["sg-unmapped"],
}


@pytest.fixture
def ws(tmp_path):
    org = tmp_path / "org"
    org.mkdir()
    m = tmp_path / "identity-map.yml"
    m.write_text(MAP_YML, encoding="utf-8")
    yield str(org), str(tmp_path / "log"), str(m)
    PR.set_resolver(None)  # always restore the local default


def _adapter(map_path):
    return PR.GroupMapResolver(lambda p: GROUPS.get(p, []), map_path=map_path)


def test_resolves_competences_per_map(ws):
    org, lr, m = ws
    r = _adapter(m)
    assert r.resolve_competences(org, "dana\x40corp.example", log_root=lr) == [
        "data-protection"]
    # mapped and unmapped groups mix: only the mapped one contributes
    assert r.resolve_competences(org, "eng\x40corp.example") == [
        "engineering", "security"]


def test_fail_closed(ws):
    org, lr, m = ws
    r = _adapter(m)
    assert r.resolve_competences(org, "lost\x40corp.example") == []   # unmapped group
    assert r.resolve_competences(org, "nobody\x40corp.example") == []  # unknown principal
    assert r.resolve_competences(org, "") == []                     # empty principal
    assert _adapter(m + ".missing").resolve_competences(
        org, "dana\x40corp.example") == []                             # no map declared

    def boom(_p):
        raise RuntimeError("directory down")
    assert PR.GroupMapResolver(boom, map_path=m).resolve_competences(
        org, "dana\x40corp.example") == []                             # failing source


def test_map_is_read_at_query_time(ws):
    org, lr, m = ws
    r = _adapter(m)
    assert r.resolve_competences(org, "dana\x40corp.example") == ["data-protection"]
    with open(m, "w", encoding="utf-8") as f:
        f.write("groups:\n  sg-dpo-team:\n    competences: [legal]\n")
    assert r.resolve_competences(org, "dana\x40corp.example") == ["legal"]


def test_roster_reads_delegate_to_local_registry(ws):
    org, lr, m = ws
    P.register_party(org, "alice", "human", competences=["legal"], log_root=lr)
    r = _adapter(m)
    rows = r.list_parties(org, log_root=lr)["parties"]
    assert [p["party_id"] for p in rows] == ["alice"]
    assert r.route_approvers(org, "legal", log_root=lr)["count"] == 1
    # resolving a principal registers nothing
    r.resolve_competences(org, "dana\x40corp.example", log_root=lr)
    ids = {p["party_id"] for p in P.list_parties(org, log_root=lr)["parties"]}
    assert ids == {"alice"}


def test_set_resolver_installs_and_restores(ws):
    org, lr, m = ws
    P.register_party(org, "alice", "human", competences=["legal"], log_root=lr)
    PR.set_resolver(_adapter(m))
    # the seam is global: get_resolver answers from the map...
    assert PR.get_resolver().resolve_competences(
        org, "dana\x40corp.example") == ["data-protection"]
    # ...while parties.* callers still reach the local roster through it
    assert P.route_approvers(org, "legal", log_root=lr)["count"] == 1
    PR.set_resolver(None)
    assert isinstance(PR.get_resolver(), PR.LocalPartyResolver)
    assert PR.get_resolver().resolve_competences(
        org, "dana\x40corp.example") == []  # local default: no such party


def test_env_declared_map(ws, monkeypatch):
    org, lr, m = ws
    monkeypatch.setenv("WORKSPACE_IDENTITY_MAP", m)
    r = PR.GroupMapResolver(lambda p: GROUPS.get(p, []))  # no explicit path
    assert r.resolve_competences(org, "dana\x40corp.example") == ["data-protection"]
