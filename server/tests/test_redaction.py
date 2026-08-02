# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The shared matching contract: case-insensitive in the original string
space, blind inside existing sentinels, dict keys included. The unicode and
sentinel cases pin erasure convergence — a rewrite removes exactly what the
scan counts, and a re-run finds nothing and changes nothing."""
from __future__ import annotations

from workspaces.redaction import (
    REDACTED,
    count_ci,
    redact_value,
    replace_ci,
    walk_strings,
)


def test_replace_is_case_insensitive_and_counts():
    out, n = replace_ci("Ada, ADA and ada", "ada")
    assert out == f"{REDACTED}, {REDACTED} and {REDACTED}"
    assert n == 3


def test_empty_inputs_are_no_ops():
    assert replace_ci("", "ada") == ("", 0)
    assert replace_ci("text", "") == ("text", 0)
    assert count_ci("", "ada") == 0
    assert count_ci("text", "") == 0


def test_indices_stay_aligned_when_lowercasing_changes_length():
    # U+0130 lowers to two code points; matching in lowered space used to
    # splice the original at shifted offsets, leaving the subject in a
    # file reported clean.
    out, n = replace_ci("İİİİ Ada Lovelace", "Ada Lovelace")
    assert n == 1
    assert out == "İİİİ " + REDACTED
    assert "Ada" not in out


def test_existing_sentinels_are_not_rematched():
    # "Ted" is a substring of the sentinel; a re-run must converge, not
    # corrupt the sentinel or count phantom hits.
    once, n1 = replace_ci("call Ted", "Ted")
    assert once == f"call {REDACTED}"
    assert n1 == 1
    again, n2 = replace_ci(once, "Ted")
    assert again == once
    assert n2 == 0
    assert count_ci(once, "Ted") == 0


def test_dict_keys_are_walked_and_redacted():
    data = {"facets": {"Ada Lovelace": "primary contact"}}
    assert "Ada Lovelace" in walk_strings(data)
    out, n = redact_value(data, "Ada Lovelace")
    assert n == 1
    assert out == {"facets": {REDACTED: "primary contact"}}


def test_count_matches_rewrite_across_nesting():
    data = {"attachments": ["specs/ada lovelace.pdf",
                            {"note": ["ADA LOVELACE and Ada Lovelace"]}]}
    counted = sum(count_ci(t, "Ada Lovelace") for t in walk_strings(data))
    out, n = redact_value(data, "Ada Lovelace")
    assert n == counted == 3
    assert not any(count_ci(s, "Ada Lovelace") for s in walk_strings(out))
