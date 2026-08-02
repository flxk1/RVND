# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The shared source-identity URN grammar (mint, read, normalise)."""

from __future__ import annotations

import pytest

from workspaces import urn


# ── canonical minting: any namespace, none privileged ────────────────────────

def test_celex_is_lowercased_under_the_celex_namespace():
    assert urn.mint_canonical("gdpr", ids={"celex": "32016R0679"}) == "urn:lg:celex:32016r0679"


def test_arxiv_and_doi_namespaces():
    assert urn.mint_canonical(ids={"arxiv": "2505.21808"}) == "urn:lg:arxiv:2505.21808"
    assert urn.mint_canonical(ids={"doi": "10.1000/XYZ"}) == "urn:lg:doi:10.1000/xyz"


def test_ecli_case_law_namespace_folds_its_colons():
    # a colon-bearing identifier (case law) stays a single addressable segment
    assert urn.mint_canonical(ids={"ecli": "ECLI:DE:BGH:2024:0101"}) == \
        "urn:lg:ecli:ecli-de-bgh-2024-0101"


def test_an_arbitrary_namespace_is_accepted():
    # no allow-list: a national register (or any scheme) works without a code change
    assert urn.mint_canonical(ids={"de-bgbl": "2021 I 1982"}) == "urn:lg:de-bgbl:2021-i-1982"


def test_falls_back_to_source_key_from_code():
    assert urn.mint_canonical("ai-act") == "urn:lg:source:ai-act"
    assert urn.mint_canonical("", title="§ 286 BGB") == "urn:lg:source:286-bgb"


def test_a_strong_identifier_takes_precedence_over_the_code():
    assert urn.mint_canonical("ai-act", ids={"celex": "32024R1689"}) == "urn:lg:celex:32024r1689"


def test_caller_order_decides_which_identifier_is_canonical():
    # first non-empty entry in caller order wins; the scheme itself is not ranked
    assert urn.mint_canonical(ids={"ecli": "X:Y", "celex": "32016R0679"}) == "urn:lg:ecli:x-y"
    assert urn.mint_canonical(ids={"celex": "", "doi": "10.1/x"}) == "urn:lg:doi:10.1/x"


def test_minting_needs_at_least_one_identifier():
    with pytest.raises(ValueError):
        urn.mint_canonical("")


def test_none_values_are_tolerated():
    # optional extraction fields arrive as None, not "" — mint on the code instead
    assert urn.mint_canonical("bgb", ids={"celex": None}) == "urn:lg:source:bgb"


def test_a_malformed_namespace_is_rejected():
    with pytest.raises(ValueError):
        urn.mint_canonical(ids={"Bad NS": "x"})


# ── slug normalisation keeps non-ASCII letters ───────────────────────────────

def test_normalize_key_collapses_punctuation_and_keeps_unicode_letters():
    assert urn.normalize_key("Roos 2021: Hochrisiko-KI") == "roos-2021-hochrisiko-ki"
    assert urn.normalize_key("für_eine  KI") == "für-eine-ki"


# ── version snapshots and round-trip parsing ─────────────────────────────────

def test_version_uses_version_segment_for_external_namespaces():
    canon = "urn:lg:celex:32016r0679"
    v = urn.mint_version(canon, snapshot_token="2016", file_suffix="a1b2")
    assert v == "urn:lg:celex:32016r0679:version:2016:file:a1b2"


def test_version_uses_snapshot_segment_for_the_source_namespace():
    canon = "urn:lg:source:ai-act"
    v = urn.mint_version(canon, file_suffix="deadbeef")
    assert v == "urn:lg:source:ai-act:snapshot:undated:file:deadbeef"


def test_parse_reads_namespace_identifier_and_snapshot():
    p = urn.parse("urn:lg:celex:32016r0679:version:2016:file:a1b2")
    assert p["namespace"] == "celex"
    assert p["identifier"] == "32016r0679"
    assert p["version_token"] == "2016"
    assert p["file_suffix"] == "a1b2"


def test_parse_rejects_a_foreign_scheme():
    with pytest.raises(ValueError):
        urn.parse("urn:dls:celex:32016r0679")


# ── cross-layer relations of the governance graph ────────────────────────────

def test_cross_layer_relation_constants():
    assert urn.GROUNDS == "grounds"
    assert urn.COMPILES_TO == "compiles_to"
    assert urn.DECIDES == "decides"
    assert urn.EVIDENCED_BY == "evidenced_by"
    # the identity-layer and cross-layer vocabularies stay disjoint
    identity = {urn.IS_SNAPSHOT_OF, urn.HAS_VERSION_URN, urn.VERSION_OF}
    cross = {urn.GROUNDS, urn.COMPILES_TO, urn.DECIDES, urn.EVIDENCED_BY}
    assert not identity & cross
