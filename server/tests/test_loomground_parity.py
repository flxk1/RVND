# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Gate: Solver and RVND consume the installed Loomground language vocabulary.

RVND owns no vocabulary copy. The compatibility asset facade and Solver's runtime
alphabets must resolve to the same installed ``loomground-governance`` package:

  1. facade parity — every exposed vocabulary file is the installed package's file
  2. engine parity — the engine's hardcoded vocab equals the live data files

No sibling checkout is used.
"""
from __future__ import annotations

import json

import pytest

from workspaces import loomground_assets as A
from workspaces import loomground_lang as L

_LIVE = A.live_root()
pytestmark = pytest.mark.skipif(
    _LIVE is None, reason="loomground-governance artifacts are unavailable")


def _live_vocab(name: str) -> dict:
    return json.loads((_LIVE / "vocabulary" / f"{name}.json").read_text())


def test_facade_resolves_installed_vocabulary():
    live_dir = _LIVE / "vocabulary"
    live = {p.name for p in live_dir.glob("*.json")}
    bundled = {p.name for p in A.bundle_vocabulary_dir().glob("*.json")}
    assert bundled == live, f"bundle/live file-set drift: {bundled ^ live}"
    for name in sorted(live):
        b = json.loads((A.bundle_vocabulary_dir() / name).read_text())
        l = json.loads((live_dir / name).read_text())
        assert b == l, f"bundled vocabulary/{name} has drifted from live Loomground"


def test_engine_node_classes_match_live():
    assert L.NODE_CLASSES == {n["class"] for n in _live_vocab("node-classes")}


def test_engine_cord_types_match_live():
    assert L.CORD_TYPES == {c["type"] for c in _live_vocab("cords")["permitted"]}


def test_engine_guard_domain_matches_live():
    gd = _live_vocab("guard-domain")
    assert L.GUARD_FIELDS == set(gd["ranges_over"])
    assert {k: set(v) for k, v in L.GUARD_OPS.items()} == \
        {k: set(v) for k, v in gd["operators"].items()}


def test_engine_alphabets_match_live():
    assert L.RISKS == _live_vocab("risk")["levels"]
    assert L.GRADES == _live_vocab("grades")["levels"]
    assert L.VERDICTS == _live_vocab("verdicts")["restrictiveness_order"]
