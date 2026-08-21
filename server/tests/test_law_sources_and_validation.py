# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""EU law-source adapters (EUR-Lex normalisation → currency registry) and the
two-layer subsumption validator (universal + regional norm theory)."""

from __future__ import annotations

from datetime import date

import pytest

from rvnd import law_sources as lsrc
from rvnd import subsumption_path as sp
from rvnd import subsumption_validator as sv
from rvnd.norm_contract import Level


# ── Law sources: coverage + EUR-Lex adapter ──────────────────────────────────

def test_sources_cover_every_legal_system():
    from rvnd import legal_systems as ls
    for js in ls.available():
        assert lsrc.sources_for(js), f"no law source declared for {js}"
    # EU has EUR-Lex; US has CourtListener; DE has Gesetze im Internet
    assert any(s.id == "eur-lex" for s in lsrc.sources_for("EU"))


def test_eurlex_adapter_normalises_real_shaped_records_into_currency_rows():
    # EUR-Lex-shaped metadata for GDPR + the repealed Directive 95/46.
    records = [
        {"celex": "32016R0679", "dateOfEffect": "2018-05-25", "title": "GDPR"},
        {"celex": "31995L0046", "dateOfEffect": "1995-12-13",
         "dateEndValidity": "2018-05-25", "repealedBy": "32016R0679"},
    ]
    reg = lsrc.build_registry_from_records(records)
    import rvnd.currency as cur
    assert cur.validity_status(reg.get("32016R0679"), date(2024, 1, 1)) == "in-force"
    assert cur.validity_status(reg.get("32016R0679"), date(2017, 1, 1)) == "not-yet-in-force"
    assert cur.validity_status(reg.get("31995L0046"), date(2024, 1, 1)) == "superseded"


def test_eurlex_adapter_never_invents_a_missing_date():
    rec = lsrc.EurLexAdapter().normalise({"celex": "32099R9999"})  # no dates supplied
    assert rec.in_force_from is None and rec.superseded_from is None


def test_adapter_without_a_fetcher_refuses_to_pretend():
    with pytest.raises(NotImplementedError):
        lsrc.EurLexAdapter().fetch_instrument("32016R0679")


# ── Two-layer validation: universal + regional norm theory ───────────────────

def _good_chain():
    return sp.build([
        {"role": "norm", "ref": "Art.9", "source": "CELEX:32024R1689 Art. 9", "authority_tier": 2},
        {"role": "tatbestand", "ref": "tb", "source": "CELEX:32024R1689", "authority_tier": 2},
        {"role": "subsumtion", "ref": "sub", "source": "CELEX:32024R1689", "authority_tier": 2},
        {"role": "ergebnis", "ref": "erg", "source": "CELEX:32024R1689", "authority_tier": 2},
    ])


def test_clean_chain_passes_both_layers_under_eu():
    rep = sv.validate(_good_chain(), legal_system="EU")
    assert rep.ok and not rep.escalations
    layers = {f.layer for f in rep.findings if f.level is Level.PASS}
    assert layers == {"universal", "regional"}


def test_unsourced_step_violates_universal_provenance():
    chain = sp.build([
        {"role": "norm", "ref": "Art.9"},        # no source
        {"role": "tatbestand", "ref": "tb", "source": "CELEX:..."},
        {"role": "subsumtion", "ref": "sub", "source": "CELEX:..."},
        {"role": "ergebnis", "ref": "erg", "source": "CELEX:..."},
    ])
    rep = sv.validate(chain, legal_system="EU")
    assert not rep.ok
    assert any(f.code == "U1-provenance" for f in rep.violations)


def test_ergebnis_without_subsumtion_violates_universal():
    chain = sp.build([
        {"role": "norm", "ref": "n", "source": "CELEX:x"},
        {"role": "tatbestand", "ref": "tb", "source": "CELEX:x"},
        {"role": "ergebnis", "ref": "erg", "source": "CELEX:x"},
    ])
    rep = sv.validate(chain, legal_system="EU")
    assert any(f.code == "U4-subsumtion" for f in rep.violations)


def test_wrong_citation_form_violates_regional_layer():
    # A US-style citation under the DE pack is not a recognised DE form.
    chain = sp.build([
        {"role": "norm", "ref": "n", "source": "42 U.S.C. 1983", "authority_tier": 2},
        {"role": "tatbestand", "ref": "tb", "source": "§ 1 BGB", "authority_tier": 2},
        {"role": "subsumtion", "ref": "sub", "source": "§ 1 BGB", "authority_tier": 2},
        {"role": "ergebnis", "ref": "erg", "source": "§ 1 BGB", "authority_tier": 2},
    ])
    rep = sv.validate(chain, legal_system="DE")
    assert any(f.code == "R1-citation-form" for f in rep.violations)


def test_conflict_escalates_under_regional_principles_never_auto_resolved():
    chain = sp.build(
        [{"role": "norm", "ref": "n", "source": "CELEX:x"},
         {"role": "tatbestand", "ref": "tb", "source": "CELEX:x"},
         {"role": "subsumtion", "ref": "sub", "source": "CELEX:x"},
         {"role": "ergebnis", "ref": "erg", "source": "CELEX:x"}],
        conflicts=[{"a": "Urteil2018", "b": "Urteil2024", "detail": "divergent Auslegung"}])
    rep = sv.validate(chain, legal_system="DE")
    reg = [f for f in rep.escalations if f.code == "R2-conflict-principle"]
    assert reg and "lex-specialis" in reg[0].message     # DE principles named, escalated
