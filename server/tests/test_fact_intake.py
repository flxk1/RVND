# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Fact intake — ask only the genuine unknowns; never re-ask standing facts.

Models a high-volume workflow (issue an invoice / process a licence run, many
times). Standing facts are answered once and reused; per-case facts come from the
run's data; the form shrinks to nothing once the entity is known."""

from __future__ import annotations

from workspaces.fact_intake import FactNeed, build_form, record_standing


# A licence/invoice workflow's fact needs.
NEEDS = [
    FactNeed("tax_status", "VAT status of the counterparty?", scope="standing"),
    FactNeed("vat_scheme", "Which VAT scheme applies?", scope="standing"),
    FactNeed("jurisdiction", "Governing jurisdiction?", scope="standing"),
    FactNeed("line_items", "Line items for this delivery?", scope="per_case"),
    FactNeed("delivery_date", "Delivery date?", scope="per_case"),
]


def test_first_run_asks_everything_unknown():
    form = build_form(NEEDS, standing={}, per_case_data={})
    asked = {q.key for q in form.questions}
    assert asked == {"tax_status", "vat_scheme", "jurisdiction", "line_items", "delivery_date"}
    assert not form.complete


def test_standing_answers_are_recorded_then_never_re_asked():
    # Run 1: user answers the standing facts.
    answers = {"tax_status": "reverse-charge", "vat_scheme": "B2B-EU",
               "jurisdiction": "DE", "line_items": "x", "delivery_date": "2026-06-01"}
    standing = record_standing(NEEDS, answers, standing={})
    # standing persisted; per-case NOT persisted
    assert standing == {"tax_status": "reverse-charge", "vat_scheme": "B2B-EU", "jurisdiction": "DE"}

    # Run 2 (next invoice, same counterparty): per-case data comes from the feed.
    form = build_form(NEEDS, standing=standing,
                      per_case_data={"line_items": "y", "delivery_date": "2026-06-08"})
    assert form.complete                       # nothing left to ask
    assert form.questions == []
    assert form.provenance["tax_status"] == "standing"
    assert form.provenance["line_items"] == "this-run"


def test_only_the_missing_per_case_datum_is_asked():
    standing = {"tax_status": "reverse-charge", "vat_scheme": "B2B-EU", "jurisdiction": "DE"}
    # the feed supplies line_items but not the date
    form = build_form(NEEDS, standing=standing, per_case_data={"line_items": "z"})
    asked = {q.key for q in form.questions}
    assert asked == {"delivery_date"}          # exactly the one genuine unknown


def test_per_case_facts_are_not_reused_from_the_entity_store():
    # even if a per-case key sits in standing, it is not treated as reusable
    standing = {"tax_status": "x", "vat_scheme": "y", "jurisdiction": "DE",
                "delivery_date": "STALE"}
    form = build_form(NEEDS, standing=standing, per_case_data={"line_items": "z"})
    assert any(q.key == "delivery_date" for q in form.questions)   # still asked, not reused


def test_thousandth_run_asks_nothing_when_data_feeds_the_per_case():
    standing = {"tax_status": "x", "vat_scheme": "y", "jurisdiction": "DE"}
    for _ in range(1000):
        form = build_form(NEEDS, standing=standing,
                          per_case_data={"line_items": "auto", "delivery_date": "auto"})
        assert form.complete and form.questions == []
