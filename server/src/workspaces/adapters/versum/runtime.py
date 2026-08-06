# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Runtime knowledge-append seam over versum — the only door RVND writes through.

``workspace_remember`` / ``reason`` record runtime knowledge (asserted facts,
derived inferences) into the folder's versum store. Consuming versum's
runtime-append API is confined here, at the ``adapters/versum`` boundary, so no
application module imports ``versum`` directly. The ``versum`` import is lazy —
inside each call — so a versum build without the runtime-append surface raises
at call time (the caller's best-effort guard handles it) rather than at
seam-import time; the knowledge adapter therefore never fails to load on an
older versum pin.
"""
from __future__ import annotations

from typing import Any


def append_fact(store: Any, *, subject: str, predicate: str, object: str,
                dimension: str, actor: str) -> Any:
    """Append an asserted triple as first-class versum knowledge."""
    import versum
    return versum.append_fact(
        str(store), subject=subject, predicate=predicate, object=object,
        dimension=dimension, actor=actor)


def append_inference(store: Any, *, path: list, dimension: str, actor: str) -> Any:
    """Append a derived inference path as first-class versum knowledge."""
    import versum
    return versum.append_inference(
        str(store), path=path, dimension=dimension, actor=actor)


def append_record(store: Any, *, record: Any, dimension: str, actor: str,
                  observed_at: Any = None, captures: Any = None,
                  identity: bool = False, version: Any = None) -> Any:
    """Append a full runtime record — an RVND-style problem/solution pair — as
    first-class versum knowledge. The rich analogue of ``append_fact``: the whole
    pair body (every domain facet) is preserved losslessly in the versum node's
    ``properties.record``. This is the write door the memory-split routes
    knowledge-channel ``remember()`` through.

    ``identity=True`` (with a monotonic ``version``) upserts a MUTABLE record in
    place — a stable node id whose latest ``version`` wins on read — the door the
    grounder-store retirement writes works/claims/provenance through. Default
    (``identity=False``) is the content-addressed append every other caller uses."""
    import versum
    return versum.append_record(
        str(store), record=record, dimension=dimension, actor=actor,
        observed_at=observed_at, captures=captures,
        identity=identity, version=version)


def append_records(store: Any, *, records: Any, dimension: str, actor: str,
                   observed_at: Any = None, captures: Any = None) -> Any:
    """Batch identity-upsert — persist many mutable records in ONE versum
    transaction (one fsync). Each item is ``{"record": <body>, "version": <str>}``.
    The write door the grounder-store retirement flushes changed works/claims/
    provenance through, so a bulk import / batch pays one durable write, not N."""
    import versum
    return versum.append_records(
        str(store), records=records, dimension=dimension, actor=actor,
        observed_at=observed_at, captures=captures)


def iter_records(store: Any, *, exclude_erased: bool = True) -> list:
    """Enumerate the full records in a folder's versum sink (erasure honored).

    The read side of the memory split: knowledge bodies live in versum, so
    ``by_id`` / ``all_pairs`` / the search union enumerate them through here. A
    store directory that does not exist yet yields nothing (a folder with no
    versum knowledge)."""
    import versum
    from pathlib import Path as _Path
    if not _Path(str(store)).is_dir():
        return []
    return list(versum.iter_records(str(store), exclude_erased=exclude_erased))


def erase_record(store: Any, node_id: str, *, physical: bool = False,
                 actor: str = "", reason: str = "") -> Any:
    """Erase one sink record, keeping versum consistent with a log delete/purge.

    Logical delete (a tombstone — hidden from every read but recoverable) unless
    ``physical`` (GDPR Art.17 purge — content stripped, not recoverable). The sink
    erasure API addresses a dimensioned-subgraph node under the ``sink:`` prefix;
    the raw ``node_id`` (as minted by ``append_record``) is prefixed here."""
    from versum.store import erasure
    sink_id = str(node_id) if str(node_id).startswith("sink:") else "sink:" + str(node_id)
    if physical:
        return erasure.purge(str(store), sink_id, reason=reason, actor=actor)
    return erasure.delete(str(store), sink_id, reason=reason, actor=actor)
