# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""RVND consumes the loomground-legal GRAMMAR through the ``adapters.legal`` seam.

The legal engine — the canonical ``LegalStatement`` + the ``analyse`` front door
(recognition → adjudication over the algebra) — is reached only through the seam,
never by importing ``loomground_legal`` directly. These exercise it end-to-end so
the consumption is real, not a dangling pin: a well-formed norm is applied, and
the honesty spine holds (ill-formed law is excluded → the analysis escalates).
"""
from rvnd.adapters.legal import LegalStatement, analyse, source_classes

from loomground_solver.cross_subsumption import FactSpace, Verdict


def test_rvnd_analyses_a_well_formed_norm_through_the_seam() -> None:
    duty = LegalStatement(
        source="reg", source_class=source_classes.SourceClass.NATIONAL_STATUTE,
        claimed_effect=source_classes.Effect.BINDING, operative_content="duty")
    result = analyse([duty], FactSpace())
    # an unconditional, well-formed BINDING duty fires vacuously → SATISFIED
    assert result.verdict is Verdict.SATISFIED
    assert not result.ill_formed and result.adjudication is not None


def test_rvnd_grammar_excludes_ill_formed_law_and_escalates() -> None:
    # a technical standard claiming BINDING is ill-formed (a standard is never
    # binding) → excluded from the reasoning; nothing recognisable to apply → OPEN
    standard = LegalStatement(
        source="EN-303", source_class=source_classes.SourceClass.TECHNICAL_STANDARD,
        claimed_effect=source_classes.Effect.BINDING, operative_content="duty")
    result = analyse([standard], FactSpace())
    assert result.verdict is Verdict.OPEN
    assert standard in result.ill_formed
