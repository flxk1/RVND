# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Consumer shim over the deontic Hohfeld layer via loomground-norm.

RVND's parallel juridical-primitive layer is RETIRED. The incident vocabulary,
the correlative/opposite pairs and the deterministic classifiers are
``loomground-deontic``'s; the facet-enrichment adapter that writes them onto a
batch of ``RuleFacet`` objects is ``loomground-norm``'s
(``loomground_norm.hohfeld.attach_incidents``). This module re-exports both
behind the historical import names (``INCIDENTS``, ``attach_incidents``,
``classify_incident``, ``extract_counterparty``, ``classify_condition_kind``)
through the ``adapters/norm`` seam.

The seam maps deontic's ``duty`` label back to RVND's ``claim-duty`` — the label
the consumed solver's NT-14 closed vocabulary validates against — so the
enrichment behavior is unchanged while the classification engine is deontic's,
consumed whole. No classification logic lives here. Callers are unchanged; this
shim is deleted once they migrate to the plane directly.
"""
from __future__ import annotations

from .adapters.norm import (
    INCIDENTS,
    attach_incidents,
    classify_incident,
    extract_counterparty,
    classify_condition_kind,
)

__all__ = ["INCIDENTS", "attach_incidents", "classify_incident",
           "extract_counterparty", "classify_condition_kind"]
