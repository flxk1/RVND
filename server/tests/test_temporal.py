# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tests for the typed temporal/monetary layer (temporal.py).

Discipline under test: validate at write, reject malformed input, never coerce,
never guess. RelativeDeadline.resolve returns None for unknown events instead
of inventing a date (NT-2 extended to the type layer)."""

from decimal import Decimal

import pytest

from rvnd.temporal import (Date, Duration, Money, RelativeDeadline,
                            RenewalRule, TemporalError, Term)


# ── Date ──────────────────────────────────────────────────────────────────────

class TestDate:
    def test_valid_date(self):
        assert Date("2026-06-04").iso == "2026-06-04"

    def test_leap_day_valid(self):
        assert Date("2028-02-29").iso == "2028-02-29"

    def test_non_leap_feb29_rejected(self):
        with pytest.raises(TemporalError):
            Date("2026-02-29")

    @pytest.mark.parametrize("bad", [
        "2026-6-4", "04.06.2026", "2026/06/04", "26-06-04",
        "2026-06-04T12:00:00Z", "", "tomorrow", "2026-13-01", "2026-00-10",
    ])
    def test_malformed_rejected(self, bad):
        with pytest.raises(TemporalError):
            Date(bad)

    def test_non_string_rejected(self):
        with pytest.raises(TemporalError):
            Date(20260604)  # type: ignore[arg-type]

    def test_ordering(self):
        assert Date("2026-01-01") < Date("2026-01-02")
        assert Date("2026-01-01") <= Date("2026-01-01")

    def test_parse_passthrough(self):
        d = Date("2026-06-04")
        assert Date.parse(d) is d
        assert Date.parse("2026-06-04") == d

    def test_roundtrip(self):
        d = Date("2026-06-04")
        assert Date.from_dict(d.to_dict()) == d


# ── Duration ──────────────────────────────────────────────────────────────────

class TestDuration:
    @pytest.mark.parametrize("iso,fields", [
        ("P30D", {"days": 30}),
        ("P1Y", {"years": 1}),
        ("P2W", {"weeks": 2}),
        ("P1Y2M10D", {"years": 1, "months": 2, "days": 10}),
        ("PT72H", {"hours": 72}),
        ("P1DT12H", {"days": 1, "hours": 12}),
    ])
    def test_parse_valid(self, iso, fields):
        d = Duration.parse(iso)
        for k, v in fields.items():
            assert getattr(d, k) == v
        assert d.iso == iso

    @pytest.mark.parametrize("bad", ["P", "PT", "30D", "P30", "1M", "", "P-3D", "30 days"])
    def test_parse_invalid(self, bad):
        with pytest.raises(TemporalError):
            Duration.parse(bad)

    def test_zero_duration_rejected(self):
        with pytest.raises(TemporalError):
            Duration()

    def test_negative_component_rejected(self):
        with pytest.raises(TemporalError):
            Duration(days=-1)

    def test_add_days(self):
        assert Duration.parse("P30D").add_to(Date("2026-07-01")) == Date("2026-07-31")

    def test_add_across_month_boundary(self):
        assert Duration.parse("P30D").add_to(Date("2026-01-15")) == Date("2026-02-14")

    def test_add_weeks(self):
        assert Duration.parse("P2W").add_to(Date("2026-06-01")) == Date("2026-06-15")

    def test_add_month_clamps(self):
        # Jan 31 + 1 month → Feb 28 (2026 not a leap year)
        assert Duration.parse("P1M").add_to(Date("2026-01-31")) == Date("2026-02-28")

    def test_add_month_clamps_leap(self):
        assert Duration.parse("P1M").add_to(Date("2028-01-31")) == Date("2028-02-29")

    def test_add_year(self):
        assert Duration.parse("P1Y").add_to(Date("2026-03-01")) == Date("2027-03-01")

    def test_subtract_days(self):
        assert Duration.parse("P90D").add_to(Date("2026-07-01"), sign=-1) == Date("2026-04-02")

    def test_subday_remainder_does_not_shift_date(self):
        assert Duration.parse("PT12H").add_to(Date("2026-06-04")) == Date("2026-06-04")

    def test_whole_day_hours_shift_date(self):
        # 72 hours = 3 days — the GDPR Art. 33 case
        assert Duration.parse("PT72H").add_to(Date("2026-08-10")) == Date("2026-08-13")

    def test_roundtrip(self):
        d = Duration.parse("P1Y2M3D")
        assert Duration.from_dict(d.to_dict()) == d


# ── RelativeDeadline ──────────────────────────────────────────────────────────

class TestRelativeDeadline:
    def test_resolve_after(self):
        rd = RelativeDeadline(event="signing", offset=Duration.parse("P30D"))
        assert rd.resolve({"signing": Date("2026-07-01")}) == Date("2026-07-31")

    def test_resolve_before(self):
        rd = RelativeDeadline(event="term_end", offset=Duration.parse("P3M"),
                              direction="before")
        assert rd.resolve({"term_end": Date("2026-12-31")}) == Date("2026-09-30")

    def test_unknown_event_returns_none_never_guesses(self):
        rd = RelativeDeadline(event="delivery", offset=Duration.parse("P30D"))
        assert rd.resolve({"signing": Date("2026-07-01")}) is None

    def test_bad_direction_rejected(self):
        with pytest.raises(TemporalError):
            RelativeDeadline(event="signing", offset=Duration.parse("P30D"),
                             direction="around")

    def test_empty_event_rejected(self):
        with pytest.raises(TemporalError):
            RelativeDeadline(event="", offset=Duration.parse("P30D"))

    def test_offset_must_be_duration(self):
        with pytest.raises(TemporalError):
            RelativeDeadline(event="signing", offset="P30D")  # type: ignore[arg-type]

    def test_derivation_resolved(self):
        rd = RelativeDeadline(event="signing", offset=Duration.parse("P30D"))
        s = rd.derivation({"signing": Date("2026-07-01")})
        assert "P30D after signing" in s and "2026-07-31" in s

    def test_derivation_unresolved(self):
        rd = RelativeDeadline(event="delivery", offset=Duration.parse("P30D"))
        assert "unresolved" in rd.derivation({})

    def test_roundtrip(self):
        rd = RelativeDeadline(event="signing", offset=Duration.parse("P30D"),
                              direction="before")
        assert RelativeDeadline.from_dict(rd.to_dict()) == rd


# ── Term + RenewalRule ────────────────────────────────────────────────────────

class TestTerm:
    def test_end_xor_duration_enforced(self):
        with pytest.raises(TemporalError):
            Term(start=Date("2026-01-01"), end=Date("2027-01-01"),
                 duration=Duration.parse("P1Y"))

    def test_end_before_start_rejected(self):
        with pytest.raises(TemporalError):
            Term(start=Date("2026-06-01"), end=Date("2026-01-01"))

    def test_end_date_explicit(self):
        t = Term(start=Date("2026-01-01"), end=Date("2026-12-31"))
        assert t.end_date() == Date("2026-12-31")

    def test_end_date_from_duration(self):
        t = Term(start=Date("2026-01-01"), duration=Duration.parse("P1Y"))
        assert t.end_date() == Date("2027-01-01")

    def test_end_date_unknown_is_none(self):
        assert Term(start=Date("2026-01-01")).end_date() is None
        assert Term().end_date() is None

    def test_notice_deadline(self):
        t = Term(start=Date("2026-01-01"), end=Date("2026-12-31"),
                 renewal=RenewalRule(kind="auto", period=Duration.parse("P1Y"),
                                     notice=Duration.parse("P3M")))
        assert t.notice_deadline() == Date("2026-09-30")

    def test_notice_deadline_none_without_renewal(self):
        assert Term(start=Date("2026-01-01"), end=Date("2026-12-31")).notice_deadline() is None

    def test_renewal_bad_kind_rejected(self):
        with pytest.raises(TemporalError):
            RenewalRule(kind="forever", period=Duration.parse("P1Y"))

    def test_empty_term_is_valid_cold_start(self):
        t = Term()
        assert t.to_dict() == {"start": None, "end": None,
                               "duration": None, "renewal": None}

    def test_roundtrip(self):
        t = Term(start=Date("2026-01-01"), duration=Duration.parse("P2Y"),
                 renewal=RenewalRule(kind="option", period=Duration.parse("P1Y")))
        assert Term.from_dict(t.to_dict()) == t


# ── Money ─────────────────────────────────────────────────────────────────────

class TestMoney:
    def test_decimal_amount(self):
        m = Money(amount=Decimal("1500.50"), currency="EUR")
        assert m.amount == Decimal("1500.50") and m.known

    def test_string_amount_accepted(self):
        assert Money(amount="99.99", currency="USD").amount == Decimal("99.99")  # type: ignore[arg-type]

    def test_float_rejected(self):
        with pytest.raises(TemporalError):
            Money(amount=1500.50, currency="EUR")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", ["eur", "EU", "EURO", "€", "", "123"])
    def test_bad_currency_rejected(self, bad):
        with pytest.raises(TemporalError):
            Money(amount=Decimal("1"), currency=bad)

    def test_exotic_but_wellformed_currency_passes(self):
        m = Money(amount=Decimal("1"), currency="XDR")
        assert not m.known    # format gate passes, curated set just flags

    def test_garbage_amount_rejected(self):
        with pytest.raises(TemporalError):
            Money(amount="a lot", currency="EUR")  # type: ignore[arg-type]

    def test_roundtrip_preserves_precision(self):
        m = Money(amount=Decimal("0.1"), currency="EUR")
        assert Money.from_dict(m.to_dict()).amount == Decimal("0.1")
