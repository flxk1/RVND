# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Shared source identity — the URN spine every governance layer keys on.

A source, an obligation extracted from it, a rule compiled from that obligation,
and a decision that applies the rule all cite one address for the same work, so
the layers compose into a single attributed graph rather than four that happen
to overlap. This module mints and reads those addresses. It holds no catalogue
and reaches no network: callers pass the identifiers they already hold.

Grammar (namespace root ``lg``):

  * canonical (the work)  ``urn:lg:<ns>:<id>``
  * version (a snapshot)  ``<canonical>:version:<token>:file:<suffix>``
    (``:snapshot:`` under the ``source`` namespace, matching the fallback form).

The namespace is **open and neutral**: ``celex`` (EU legislation), ``ecli`` (case
law), ``arxiv``, ``doi``, ``isbn``, a national register — none is privileged in
code, and an unknown namespace is as valid as a known one. A caller passes the
identifiers it holds as ``ids`` (namespace -> value) and, by their order, decides
which is canonical; with none, a neutral ``source`` key is derived from a local
code. ``source`` is the only reserved namespace (the fallback).

The provenance relations name how the layers connect on the graph: a file
``is_snapshot_of`` a work and ``has_version_urn`` a version; a version is a
``version_of`` a work.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Mapping, Optional

ROOT = "lg"

# The only reserved namespace: the neutral fallback when no external id is held.
NS_SOURCE = "source"

# Provenance relations between the identity layers (edge labels on the graph).
IS_SNAPSHOT_OF = "is_snapshot_of"     # file -> work
HAS_VERSION_URN = "has_version_urn"   # file -> version
VERSION_OF = "version_of"             # version -> work

# Cross-layer relations of the governance graph, each validated by the store
# that emits it (the grounding ledger for grounds, the entity corpus for
# decides).
GROUNDS = "grounds"                   # obligation -> the source/work it came from
COMPILES_TO = "compiles_to"           # rule -> the obligation it implements
DECIDES = "decides"                   # decision -> the rule it applied
EVIDENCED_BY = "evidenced_by"         # evidence -> what the sealed record attests

UNDATED = "undated"

_NS_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_ID_UNSAFE = re.compile(r"[^a-z0-9._/~-]+")


def normalize_key(text: str) -> str:
    """A stable slug for the ``source`` namespace: lower-cased, unicode-normalised,
    non-word runs collapsed to a single hyphen. Letters outside ASCII are kept."""
    t = unicodedata.normalize("NFKC", (text or "").strip().lower())
    t = re.sub(r"[^\w]+", "-", t, flags=re.UNICODE).replace("_", "-")
    return re.sub(r"-+", "-", t).strip("-")


def _norm_ns(ns: str) -> str:
    ns = (ns or "").strip().lower()
    if not _NS_RE.fullmatch(ns):
        raise ValueError(f"invalid URN namespace {ns!r}")
    return ns


def _norm_id(value: str) -> str:
    """URN-safe identifier segment: lower-cased, with the segment separator ``:``
    and any other URN-unsafe run folded to ``-`` (so a colon-bearing id such as an
    ECLI stays one addressable segment). Kept: letters, digits and ``. _ / ~ -``,
    which leave CELEX, DOI and arXiv identifiers intact."""
    return _ID_UNSAFE.sub("-", (value or "").strip().lower()).strip("-")


def mint_canonical(code: str = "", *, ids: Optional[Mapping[str, str]] = None,
                   title: str = "") -> str:
    """The canonical address of a work. ``ids`` maps namespace -> external
    identifier (e.g. ``{"celex": "32024R1689"}``, ``{"ecli": "DE:BGH:2024:1"}``);
    the first non-empty entry, in caller order, becomes the canonical namespace —
    no scheme is privileged. With no identifier, a neutral ``source`` key is
    derived from ``code`` (else ``title``). Values may arrive as ``None``."""
    for ns, value in (ids or {}).items():
        value = (value or "").strip()
        if value:
            return f"urn:{ROOT}:{_norm_ns(ns)}:{_norm_id(value)}"
    key = normalize_key(code) or normalize_key(title)
    if not key:
        raise ValueError("mint_canonical needs an identifier in ids, or a code/title")
    return f"urn:{ROOT}:{NS_SOURCE}:{key}"


def mint_version(canonical: str, *, snapshot_token: str = UNDATED,
                 file_suffix: str = "") -> str:
    """A snapshot address under a canonical work. The ``source`` namespace uses
    the ``:snapshot:`` segment; every external namespace uses ``:version:``.
    ``file_suffix`` disambiguates two files of the same snapshot."""
    ns = parse(canonical)["namespace"]
    segment = "snapshot" if ns == NS_SOURCE else "version"
    token = (snapshot_token or UNDATED).strip() or UNDATED
    return f"{canonical}:{segment}:{token}:file:{file_suffix}"


def parse(urn: str) -> dict:
    """Split a URN into ``root``, ``namespace``, ``identifier`` and, when present,
    ``version_token`` / ``file_suffix``. Raises ``ValueError`` on a foreign scheme."""
    parts = (urn or "").split(":")
    if len(parts) < 4 or parts[0] != "urn" or parts[1] != ROOT:
        raise ValueError(f"not an {ROOT!r} URN: {urn!r}")
    out = {"root": parts[1], "namespace": parts[2], "identifier": parts[3],
           "version_token": "", "file_suffix": ""}
    if len(parts) >= 8 and parts[4] in ("version", "snapshot") and parts[6] == "file":
        out["version_token"] = parts[5]
        out["file_suffix"] = parts[7]
    return out
