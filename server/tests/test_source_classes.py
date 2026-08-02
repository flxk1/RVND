# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Universal source-class map + the applicable-law resolver.

Two layers under test:
  * source_classes.py — universal invariants (a standard is never binding; a
    non-self-executing source needs an incorporation edge to bind).
  * legal_systems.applicable_law — the pack instances: selecting DE pulls in EU,
    treaty/standard incorporation rules surface, UK does not pull in EU.
"""

from __future__ import annotations

import pytest

from workspaces.source_classes import (Effect, Relation, SourceClass, max_effect,
                                  self_executes, requires_incorporation,
                                  check_source, is_relation, catalogue)
from workspaces import legal_systems as ls


# ── universal: effect ceilings ────────────────────────────────────────────────

def test_a_technical_standard_can_never_be_binding():
    assert max_effect(SourceClass.TECHNICAL_STANDARD) is Effect.PRESUMPTION
    findings = check_source(SourceClass.TECHNICAL_STANDARD,
                            claimed_effect=Effect.BINDING)
    assert any(f.invariant == "SC-2" and f.level == "violation" for f in findings)


def test_soft_law_ceiling_is_interpretive():
    assert max_effect(SourceClass.SOFT_LAW) is Effect.INTERPRETIVE
    assert check_source(SourceClass.SOFT_LAW, claimed_effect=Effect.PRESUMPTION)


def test_effect_is_ordered():
    assert Effect.INTERPRETIVE < Effect.BINDING
    assert Effect.PRESUMPTION <= Effect.PRESUMPTION


def test_a_statute_binding_is_clean():
    assert check_source(SourceClass.NATIONAL_STATUTE,
                        claimed_effect=Effect.BINDING) == []


# ── universal: incorporation invariant (SC-3) ─────────────────────────────────

def test_directive_is_not_self_executing_and_needs_incorporation():
    assert requires_incorporation(SourceClass.SUPRANATIONAL_DIRECTIVE)
    bad = check_source(SourceClass.SUPRANATIONAL_DIRECTIVE,
                       claimed_effect=Effect.BINDING, has_incorporation_edge=False)
    assert any(f.invariant == "SC-3" for f in bad)
    ok = check_source(SourceClass.SUPRANATIONAL_DIRECTIVE,
                      claimed_effect=Effect.BINDING, has_incorporation_edge=True)
    assert ok == []


def test_regulation_self_executes_so_no_incorporation_needed():
    assert self_executes(SourceClass.SUPRANATIONAL_REGULATION)
    assert check_source(SourceClass.SUPRANATIONAL_REGULATION,
                        claimed_effect=Effect.BINDING) == []


def test_pack_can_widen_self_execution_but_not_universally():
    # customary international law is NOT self-executing by universal default …
    assert requires_incorporation(SourceClass.CUSTOMARY_INTERNATIONAL)
    # … but the DE pack self-executes it (Art. 25 GG)
    de = ls.get("DE")
    assert self_executes(SourceClass.CUSTOMARY_INTERNATIONAL, de.self_executing_extra)


# ── universal: relation vocabulary ────────────────────────────────────────────

def test_relation_vocabulary_is_closed():
    assert is_relation("member_of") and is_relation("presumes_conformity")
    assert not is_relation("vibes_with")


def test_catalogue_self_describes():
    cat = catalogue()
    assert "technical_standard" in cat["source_classes"]
    assert cat["ceilings"]["technical_standard"] == "PRESUMPTION"
    assert any(i["id"] == "SC-3" for i in cat["invariants"])


# ── pack instances: applicable_law ────────────────────────────────────────────

def test_selecting_DE_pulls_in_EU():
    assert ls.applicable_systems("DE") == ["DE", "EU"]
    al = ls.applicable_law("DE")
    origins = {s.origin for s in al.sources}
    assert origins == {"DE", "EU"}                      # EU law governs too
    classes = {s.source_class for s in al.sources}
    assert SourceClass.SUPRANATIONAL_REGULATION in classes   # GDPR/AI Act room exists


def test_UK_does_not_pull_in_EU():
    assert ls.applicable_systems("UK") == ["UK"]
    al = ls.applicable_law("UK")
    assert {s.origin for s in al.sources} == {"UK"}


def test_DE_records_member_of_and_primacy_relations():
    al = ls.applicable_law("DE")
    rels = {(r.subject, r.relation, r.object) for r in al.relations}
    assert ("DE", Relation.MEMBER_OF, "EU") in rels
    # EU primacy edge present, but annotated as escalate-not-resolve
    primacy = [r for r in al.relations if r.relation is Relation.OUTRANKS]
    assert primacy and "escalates" in primacy[0].note


def test_DE_treaty_and_standard_incorporation_rules_surface():
    al = ls.applicable_law("DE")
    de = ls.get("DE")
    assert "Art. 59(2) GG" in de.incorporation_rule(SourceClass.INTERNATIONAL_TREATY)
    assert "presumption" in de.incorporation_rule(SourceClass.TECHNICAL_STANDARD).lower()


def test_directive_binds_via_transposition_note():
    de = ls.get("DE")
    note = de.incorporation_rule(SourceClass.SUPRANATIONAL_DIRECTIVE)
    assert "transpos" in note.lower()


def test_companion_scope_filter_narrows_the_source_set():
    # companion scoping previewed: a folder that only cares about enacted law
    scope = {SourceClass.NATIONAL_STATUTE, SourceClass.SUPRANATIONAL_REGULATION}
    al = ls.applicable_law("DE", in_scope=scope)
    assert {s.source_class for s in al.sources} <= scope
    assert all(s.source_class in scope for s in al.sources)


def test_unknown_system_still_raises():
    with pytest.raises(KeyError):
        ls.get("ZZ")
