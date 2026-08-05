# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over loomground-norm's Phase-2 LLM rule extractor.

RVND's parallel Phase-2 extractor is RETIRED. The strict-output, grounded,
defined-term-resolving LLM seam now lives in ``loomground-norm``
(``loomground_norm.rule_extractor_llm``); this module re-exports it behind the
historical import names (``Phase2Result``, ``extract_rules_llm``,
``PHASE2_CONFIDENCE_CAP``) through the ``adapters/norm`` seam.

The seam maps the plane's incident label (``duty``) back to RVND's vocabulary
(``claim-duty``) on returned facets; no extraction logic lives here. Callers are
unchanged; this shim is deleted once they migrate to the plane directly.
"""
from __future__ import annotations

from .adapters.norm import (
    Phase2Result,
    extract_rules_llm,
    PHASE2_CONFIDENCE_CAP,
)

__all__ = ["Phase2Result", "extract_rules_llm", "PHASE2_CONFIDENCE_CAP"]
