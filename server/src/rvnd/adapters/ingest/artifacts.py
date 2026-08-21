# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Seam re-export of the required-artifact catalogue now owned by loomground-ingest.

Internal by design: a seam re-export, not a console or MCP surface.

The curated compliance-artifact catalogue (trigger phrases → canonical artifact +
category) and the obligation-cued scan are the ingest plane's, in
``loomground_ingest.artifacts``. RVND consumes them here; the required-artifact
ND-dispatcher (``instrument_obligation_extractor``) reaches them through this seam,
never through the upstream package directly.
"""
from __future__ import annotations

from loomground_ingest import (  # noqa: F401
    RequiredArtifact,
    extract_required_artifacts,
)

__all__ = ["RequiredArtifact", "extract_required_artifacts"]
