# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND's seat in the ingestion plane.

Internal by design: not yet a console or MCP surface.

The plane framework lives in the ``loomground_ingest`` package (currency,
registry, writer, pipeline). RVND contributes the policy ingester — the first
instance — and wires the format-aware extractor as the input role. The
framework symbols are re-exported here so callers reach one module.
"""
from loomground_ingest import (
    CollectingWriter,
    Ctx,
    Ingester,
    IngesterRegistry,
    Predicate,
    Subgraph,
    Writer,
    ingest_artifact,
    ingest_text,
    versum_writer,
    DeonticIngester,
)

from ..adapters.ingest.governance import GovernanceIngester


def default_registry() -> IngesterRegistry:
    """The registry carries only consumed grammar ingesters. RVND has no ingest
    of its own: DeonticIngester lowers general normative prose across the 5D, and
    GovernanceIngester (the governance compiler, now owned by loomground-ingest
    over loomground-governance + loomground-deontic) claims express governance —
    reservations, prohibitions, obligations — and quarantines judgments. Both are
    consumed from loomground_ingest; the RVND-grown twin is retired."""
    reg = IngesterRegistry()
    # Order is dispatch precedence: GovernanceIngester is consulted first so it
    # claims express governance (reservations/prohibitions) and quarantines
    # judgments; general normative prose falls through to DeonticIngester.
    reg.register(GovernanceIngester())
    reg.register(DeonticIngester())
    return reg


def ingest_file(file_path: str, folder_context: str, *,
                registry: IngesterRegistry, writer: Writer,
                ctx=None) -> dict:
    """Bind RVND's format-aware extractor as the plane's input role, then
    ingest. Audio and image are future formats the extractor is defined to
    admit, not yet shipped."""
    from ..format_extractors import FormatAwareExtractor

    def _extract(path: str) -> str:
        extracted = FormatAwareExtractor().extract(path, folder_context)
        parts = [str(p.get("text") or p.get("content") or "")
                 for p in (extracted.pairs or [])]
        return "\n".join(t for t in parts if t) or (extracted.content_preview or "")

    return ingest_artifact(file_path, extract=_extract, registry=registry,
                           writer=writer, ctx=ctx)


__all__ = [
    "default_registry", "ingest_text", "ingest_file", "ingest_artifact",
    "GovernanceIngester", "IngesterRegistry", "Ingester", "Subgraph",
    "Predicate", "Ctx", "Writer", "CollectingWriter", "versum_writer",
    "DeonticIngester",
]
