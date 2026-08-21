# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Validate the legal-entity corpus — the reachability/authority/currency/
provenance pass that keeps a corpus of URLs from rotting silently.

A corpus of pointers is only trustworthy if the pointers are checked. This module
applies the ``source-validator`` methodology to every entity in the map, scoring
four independent dimensions:

  * **authority** — the 5-tier hierarchy (primary law / institutional / supporting
    / secondary / general), inferred from kind + host. An instrument on EUR-Lex is
    primary law; a regulator on an official domain is institutional; a standards
    body is supporting.
  * **reachability** — structural by default (scheme + known-official host, or a
    well-formed ELI), with an optional ``probe`` callable for a *live* HTTP check
    when the caller has (allow-listed) network. The module never fetches on its
    own — recording the pointer and fetching it are separate concerns.
  * **currency** — an instrument that is the object of a ``supersedes`` edge is
    flagged ``superseded`` (e.g. the 1995 Directive, NIS1); others ``current``.
  * **provenance** — source (seed / user / ingest) and first/last-seen presence.

The output is a per-entity finding plus a corpus-health summary that surfaces the
three failure modes worth acting on: missing URLs, superseded-but-still-listed
instruments, and unverified hosts. Pure stdlib; the live probe is injected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

from ..legal_connection import Connection
from ..legal_world import EntityKind, WorldMap


# Hosts/suffixes we treat as official primary or institutional sources.
_OFFICIAL_EXACT = frozenset({
    "eur-lex.europa.eu", "curia.europa.eu", "www.coe.int", "coe.int",
    "www.oecd.org", "oecd.ai", "www.iso.org", "www.iec.ch",
    "www.cencenelec.eu", "www.etsi.org", "www.nist.gov", "www.icann.org",
    "www.w3.org", "www.ietf.org", "www.cnil.fr", "www.bfdi.bund.de",
    "www.dataprotection.ie", "www.gesetze-im-internet.de",
    "www.legislation.gov.uk", "www.un.org",
})
_OFFICIAL_SUFFIX = (".europa.eu", ".gov", ".gov.uk", ".bund.de", ".int",
                    ".gouv.fr")

# 5-tier authority labels (citation-grounding-verifier hierarchy).
PRIMARY_LAW = "primary-law"
INSTITUTIONAL = "institutional"
SUPPORTING = "supporting"
SECONDARY = "secondary"
GENERAL = "general"


@dataclass
class Finding:
    code: str
    kind: str
    url: Optional[str]
    authority: str
    reachability: str        # ok | missing | malformed | unverified-host | reachable | unreachable
    currency: str            # current | superseded | n/a
    provenance: str          # seed | user | ingest | unknown
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "kind": self.kind, "url": self.url,
                "authority": self.authority, "reachability": self.reachability,
                "currency": self.currency, "provenance": self.provenance,
                "issues": self.issues}


def _host_ok(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.netloc:
        return False
    host = p.netloc.lower()
    if host in _OFFICIAL_EXACT:
        return True
    return any(host.endswith(suf) for suf in _OFFICIAL_SUFFIX)


def _is_eli(url: str) -> bool:
    return "eur-lex.europa.eu/eli/" in (url or "")


def _authority(kind: EntityKind, url: Optional[str]) -> str:
    if kind is EntityKind.INSTRUMENT:
        return PRIMARY_LAW if (url and (_is_eli(url) or _host_ok(url))) else SECONDARY
    if kind in (EntityKind.REGULATOR, EntityKind.PUBLIC_BODY,
                EntityKind.SUPRANATIONAL, EntityKind.STATE,
                EntityKind.INTERNATIONAL_REGIME):
        return INSTITUTIONAL if (url and _host_ok(url)) else SECONDARY
    if kind is EntityKind.STANDARDS_BODY:
        return SUPPORTING
    return GENERAL


def _superseded_set(world: WorldMap) -> set[str]:
    return {ed.object for ed in world.edges if ed.connection is Connection.SUPERSEDES}


def validate_corpus(world: WorldMap, *,
                    probe: Optional[Callable[[str], bool]] = None) -> dict:
    """Validate every entity in ``world``. If ``probe`` is given it is called per
    URL for a live reachability check; otherwise reachability is structural.
    Returns ``{findings, summary}``."""
    superseded = _superseded_set(world)
    findings: list[Finding] = []

    for code, e in world.entities.items():
        issues: list[str] = []
        # reachability
        if not e.url:
            reach = "missing"
            if e.kind in (EntityKind.INSTRUMENT, EntityKind.REGULATOR,
                          EntityKind.STANDARDS_BODY):
                issues.append("no URL for a corpus entity")
        elif probe is not None:
            ok = False
            try:
                ok = bool(probe(e.url))
            except Exception:                                  # noqa: BLE001
                ok = False
            reach = "reachable" if ok else "unreachable"
            if not ok:
                issues.append("URL did not resolve on live probe")
        elif _host_ok(e.url) or _is_eli(e.url):
            reach = "ok"
        else:
            p = urlparse(e.url)
            reach = "malformed" if p.scheme not in ("http", "https") or not p.netloc \
                else "unverified-host"
            issues.append(f"reachability {reach}")
        # currency
        if e.kind is EntityKind.INSTRUMENT:
            currency = "superseded" if code in superseded else "current"
            if currency == "superseded":
                issues.append("instrument is superseded but still listed")
        else:
            currency = "n/a"
        # provenance
        provenance = e.source or "unknown"
        findings.append(Finding(
            code=code, kind=e.kind.value, url=e.url,
            authority=_authority(e.kind, e.url), reachability=reach,
            currency=currency, provenance=provenance, issues=issues))

    summary = {
        "entities": len(findings),
        "with_url": sum(1 for f in findings if f.url),
        "missing_url": sorted(f.code for f in findings if f.reachability == "missing"),
        "superseded": sorted(f.code for f in findings if f.currency == "superseded"),
        "unverified_hosts": sorted(f.code for f in findings
                                   if f.reachability in ("unverified-host", "malformed", "unreachable")),
        "by_authority": {t: sorted(f.code for f in findings if f.authority == t)
                         for t in (PRIMARY_LAW, INSTITUTIONAL, SUPPORTING, SECONDARY, GENERAL)},
        "clean": sum(1 for f in findings if not f.issues),
    }
    return {"findings": [f.to_dict() for f in findings], "summary": summary}


def validate_registry(registry, *, probe: Optional[Callable[[str], bool]] = None) -> dict:
    """Validate a persisted corpus (``legal_corpus.EntityRegistry``)."""
    return validate_corpus(registry.to_world_map(), probe=probe)
