# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Sanctioned RVND host adapter for the Ingestor to Versum write edge."""
from __future__ import annotations

import hashlib
from pathlib import Path

from loomground_ingest import versum_writer

from ..adapters.versum import DimensionedSubgraphSink
from . import default_registry, ingest_file

FEDERATION_AXES = [
    "structural", "causal", "intentional", "temporal", "relational",
]


def ingest_into_versum(file_path: str, folder_context: str) -> dict:
    """Lower one acquired local artifact into its workspace-owned Versum store."""
    workspace = Path(folder_context).expanduser().resolve(strict=True)
    source = Path(file_path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        locator = source.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise PermissionError("ingest source is outside the workspace") from exc
    digest_hex = hashlib.sha256(source.read_bytes()).hexdigest()
    digest = "sha256:" + digest_hex
    source_id = "source:" + digest_hex
    evidence_id = "evidence:" + hashlib.sha256(
        (source_id + ":artifact").encode("utf-8")
    ).hexdigest()
    writer = versum_writer(
        DimensionedSubgraphSink(
            workspace / ".versum", authorized_store_root=workspace
        ),
        idempotency_key="rvnd:" + digest_hex,
        source={"source_id": source_id, "content_digest": digest},
        evidence=[{
            "evidence_id": evidence_id,
            "source_id": source_id,
            "locator": locator,
            "content_digest": digest,
        }],
        nd={
            "facet": "nD",
            "system_id": "system:federation-5d",
            "dimension_count": len(FEDERATION_AXES),
            "axes": FEDERATION_AXES,
        },
    )
    return ingest_file(
        str(source), str(workspace), registry=default_registry(), writer=writer,
        ctx={"source_id": source_id},
    )
