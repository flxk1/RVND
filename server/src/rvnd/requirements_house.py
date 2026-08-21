# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Split shim — the requirements house is the vertical plane's; the EXTRACTION
that fills it stays here.

The house itself (``Room``, ``RequirementsHouse``, and ``build_house``, which
arranges obligation and artifact atoms into rooms) is a VERTICAL's surface: pure
structure over data it is handed, importing nothing from the engines. It now
lives in ``loomground-vertical`` (``loomground_vertical.requirements_house``) and
is consumed here through the ``adapters.vertical`` seam. ``evidence_coverage``,
``music_domain`` and the external verticals keep the historical
``workspaces.requirements_house`` import path.

**What deliberately did NOT move: :func:`build_house_from_text`.** Turning
instrument TEXT into atoms runs RVND's extraction pipeline — ``nd_routing``
classification, ``deontic_facets``, ``instrument_obligation_extractor``,
``crossref_extractor``, ``applicability``. That is engine work. A copy of it out
in the vertical plane would be the parallel structure the split exists to avoid,
and it would drag five engine modules across a boundary that is meant to have no
engine dependency in either direction. So the plane assembles a house from atoms
it is HANDED — which also frees a vertical to supply atoms from any source — and
the single implementation of the extraction lives here, in the engine, calling
the plane's ``build_house`` for the assembly.
"""
from __future__ import annotations

from .adapters.vertical import (
    RequirementsHouse,
    Room,
    build_house,
)

__all__ = ["Room", "RequirementsHouse", "build_house", "build_house_from_text"]


def build_house_from_text(instrument_text: str, domain: str,
                          *, title: str = "") -> RequirementsHouse:
    """Convenience: run the NDs over instrument text, then assemble the house.

    The extraction half of the requirements house — the half that cannot move to
    the vertical plane, because it IS the engine.
    """
    from .nd_routing import DefaultClassifier
    from .deontic_facets import extract_deontic_pairs
    from .instrument_obligation_extractor import RequiredArtifactExtractor
    from .crossref_extractor import extract_cross_references
    from .applicability import enrich_pairs

    cls = DefaultClassifier().classify(instrument_text)
    # TODO(flow): consume the deontic facet as a patchbay relation
    # (versum → solver → patchbay → rvnd) rather than re-reading the surface here.
    obligations = extract_deontic_pairs(instrument_text, source_document=domain)
    enrich_pairs(obligations, domain)
    artifacts = RequiredArtifactExtractor().extract(instrument_text, cls, source_document=domain)
    refs = [r.to_dict() for r in extract_cross_references(instrument_text, host_key=domain)]
    return build_house(obligations=obligations, artifacts=artifacts,
                       cross_refs=refs, domain=domain, title=title)
