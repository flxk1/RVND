# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""PartyResolver — the one seam where identity may enter governance.

RVND keeps identity OUT of the Loomground language (the no-id wall). Where the
runtime must know *who holds a competence* it goes through this port, never an
external identity concept directly. The default resolver is the local, chain-
backed party registry (``parties.py``); an enterprise deployment may install an
adapter (OIDC/Auth0 SSO groups -> competences, SPIFFE workload identity, SCIM
roster sync) by calling :func:`set_resolver`. :class:`GroupMapResolver` is the
shipped adapter: it answers competences from the identity-map config.

An adapter's ONLY job is to answer "who holds competence X" — it returns
role/competence, which is what the language already speaks. No identity ever
reaches the ``.lg`` or the §1.5 engine; only the named-signer engine may
persist a named id. This module is therefore the entire identity attack surface
of the governance path: everything else sees role.

Writes (register/kill a party) stay local by design — RVND does not push back
into an IdP — so only the read side is on the port.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Protocol, runtime_checkable

from . import parties as _local


@runtime_checkable
class PartyResolver(Protocol):
    """Read-side identity port. ``resolve_competences`` is the claim->competence seam."""

    def list_parties(self, folder_context: str, kind: str = "",
                     competence: str = "", log_root: Optional[str] = None) -> dict[str, Any]:
        ...

    def route_approvers(self, folder_context: str, competence: str,
                        log_root: Optional[str] = None) -> dict[str, Any]:
        ...

    def resolve_competences(self, folder_context: str, principal: str,
                            log_root: Optional[str] = None) -> list[str]:
        ...


class LocalPartyResolver:
    """Default resolver: the chain-backed registry. Local, file-based, air-gap."""

    def list_parties(self, folder_context, kind="", competence="", log_root=None):
        return _local._list_parties_local(folder_context, kind, competence, log_root)

    def route_approvers(self, folder_context, competence, log_root=None):
        return _local._route_approvers_local(folder_context, competence, log_root)

    def resolve_competences(self, folder_context, principal, log_root=None):
        for r in self.list_parties(folder_context, log_root=log_root)["parties"]:
            if r.get("party_id") == principal and r.get("status") == "active":
                return list(r.get("competences") or [])
        return []


class GroupMapResolver:
    """Adapter: the declared identity map answers the claim->competence seam.

    ``groups_for`` names the deployment's source of a principal's groups (the
    trusted front's groups claim, a directory lookup). Competences are computed
    from the identity-map config at query time, so a map edit takes effect on
    the next resolution — no re-registration. No map, unknown principal, or
    unmapped group resolves to ``[]``: fail closed. Roster reads delegate to
    the local chain registry; this adapter registers nothing and never writes.
    Install with :func:`set_resolver` when a groups source exists; the local
    resolver stays the default.
    """

    def __init__(self, groups_for: Callable[[str], list[str]],
                 map_path: Optional[str] = None) -> None:
        self._groups_for = groups_for
        self._map_path = map_path
        self._roster = LocalPartyResolver()

    def list_parties(self, folder_context, kind="", competence="", log_root=None):
        return self._roster.list_parties(folder_context, kind, competence, log_root)

    def route_approvers(self, folder_context, competence, log_root=None):
        return self._roster.route_approvers(folder_context, competence, log_root)

    def resolve_competences(self, folder_context, principal, log_root=None):
        from .identity_map import competences_for, load_map
        mapping = load_map(self._map_path)
        if not mapping or not principal:
            return []
        try:
            groups = list(self._groups_for(principal) or [])
        except Exception:                                    # noqa: BLE001
            return []                # a failing groups source resolves nothing
        return competences_for(groups, mapping)


_ACTIVE: PartyResolver = LocalPartyResolver()


def get_resolver() -> PartyResolver:
    """The active resolver — local by default, an adapter if one was installed."""
    return _ACTIVE


def set_resolver(resolver: Optional[PartyResolver]) -> None:
    """Install an adapter, or pass None to restore the local default."""
    global _ACTIVE
    _ACTIVE = resolver if resolver is not None else LocalPartyResolver()
