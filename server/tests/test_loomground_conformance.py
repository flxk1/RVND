# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Gate: Rvnd's engine must pass every published Loomground conformance vector.

The vectors + manifest come from the ONE source — the `loomground-governance` package
(the same one the engine reads its vocabulary from), not a vendored/adjacent copy.
"""
from __future__ import annotations

import json

import pytest

from rvnd import loomground_lang as L

try:
    _LG = L._loomground_core()
    _CONF = _LG.conformance_dir()
    _VECTORS = _CONF / "vectors"
    _HAVE = _CONF.exists()
except Exception:        # loomground-governance not importable in this environment
    _LG = None
    _CONF = _VECTORS = None
    _HAVE = False

# loomground-governance is a REQUIRED, SHA-pinned runtime dependency (pyproject
# `dependencies`). If it is not importable, the install is broken and the
# standards-conformance gate has silently vanished — that must be a loud
# failure, never a skip. The vector cases below still guard on _HAVE so the
# sentinel is the single, precise failure signal.
_requires_source = pytest.mark.skipif(
    not _HAVE, reason="loomground-governance not importable — see sentinel failure")


def test_conformance_source_is_importable():
    """Sentinel: the conformance gate must never disappear silently."""
    assert _HAVE, (
        "loomground-governance (the conformance source) is not importable. It is "
        "a pinned runtime dependency, so this environment is broken — reinstall "
        "with `make venv`. A skipped conformance suite is not a passed one."
    )


def _manifest():
    return _LG.manifest()["vectors"]


def _input(vdir):
    """Canonical `.lg`; `.loom` accepted as the deprecated alias (reader rule)."""
    lg = vdir / "input.lg"
    return lg if lg.exists() else vdir / "input.loom"


@_requires_source
@pytest.mark.parametrize("vec", _manifest() if _HAVE else [], ids=lambda v: v["name"])
def test_conformance_vector(vec):
    vdir = _VECTORS / vec["name"]
    kind = vec["kind"]
    if kind == "patch":
        patch = L.parse(_input(vdir).read_text())
        assert L.validate(patch)["ok"], L.validate(patch)["errors"]
        assert L.project(patch) == json.loads((vdir / "expected.json").read_text())
        tp = vdir / "transport.json"
        if tp.exists():
            transport = json.loads(tp.read_text())
            assert L.evaluate(patch, transport) == transport["expected"]
            if "log" in transport:
                # §7.4 ordered log trace: effective verdicts, evaluation order
                assert L.evaluate_log(patch, transport) == transport["log"]
    elif kind == "negative":
        stage = json.loads((vdir / "reject.json").read_text())["stage"]
        text = _input(vdir).read_text()
        if stage == "parse":
            with pytest.raises(L.ParseError):
                L.parse(text)
        else:
            patch = L.parse(text)  # must parse
            assert not L.validate(patch)["ok"]
    elif kind == "token":
        for case in json.loads((vdir / "tokens.json").read_text()):
            assert L.validate_token(case["token"]) == case["valid"]
    else:
        pytest.fail(f"unknown vector kind {kind}")


# ── v0.8 normative points with no dedicated vector ─────────────────────────────

@_requires_source
def test_quorum_by_canonical_spacing():
    """SPEC §9 (v0.8): a quorum `by` target is in canonical form — exactly one
    space after `of` and after each comma, none adjacent to the braces — so two
    spellings of the same target compare equal."""
    canon = "2 of {legal, finance}"
    for spelling in ("2 of {legal, finance}",
                     "2 of { legal, finance }",
                     "2 of {legal,finance}",
                     "2 of { legal ,  finance }"):
        patch = L.parse("actor a\ngate g risk low grant a\n"
                        f"reserve k by {spelling}\ncord a -> g\ncord g -> master\n")
        assert patch["reservations"][0]["by"] == canon, spelling


@_requires_source
def test_evaluate_does_not_mutate_token():
    """SPEC §7 (v0.8): evaluation MUST NOT mutate the token — provenance is
    transport-supplied, never appended by the engine."""
    import copy

    patch = L.parse("actor a\ngate g risk low grant a\n"
                    "obligation disclosure on g\ncord a -> g\ncord g -> master\n")
    assert L.validate(patch)["ok"]
    transport = {"activations": [{"actor": "a", "source": "g", "token": {
        "id": "t", "kind": "act", "risk": "low", "party": "p", "provenance": []}}]}
    before = copy.deepcopy(transport)
    L.evaluate(patch, transport)
    L.evaluate_log(patch, transport)
    assert transport == before
