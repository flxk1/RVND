# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Declared upstream seam for Governance and Deontic language contracts.

Internal by design: callers consume the published-policy-pack boundary, while
this module solely confines dependency imports to the adapter layer.
"""
from __future__ import annotations

import deontic
import loomground_governance


def installed_policy_language_packages() -> tuple[tuple[str, object, str], ...]:
    return (
        (
            "governance",
            loomground_governance,
            "authoritative policy grammar and vocabulary",
        ),
        ("deontic", deontic, "normative classification language"),
    )


_GRADES_VOCAB: dict | None = None


def _grades_vocab() -> dict:
    global _GRADES_VOCAB
    if _GRADES_VOCAB is None:
        _GRADES_VOCAB = loomground_governance.vocabulary("grades")
    return _GRADES_VOCAB


def grade_levels() -> tuple[str, ...]:
    """The ordered autonomy grade levels (L0..L4) — consumed from governance's
    grammar (``vocabulary/grades.json``), never re-declared in RVND."""
    return tuple(_grades_vocab()["levels"])


def grade_index() -> dict[str, int]:
    """Grade -> rank map (the lattice order), consumed from governance's grammar."""
    return {grade: rank for rank, grade in enumerate(grade_levels())}


_VERDICTS_VOCAB: dict | None = None


def _verdicts_vocab() -> dict:
    global _VERDICTS_VOCAB
    if _VERDICTS_VOCAB is None:
        _VERDICTS_VOCAB = loomground_governance.vocabulary("verdicts")
    return _VERDICTS_VOCAB


def verdict_order() -> tuple[str, ...]:
    """Verdicts least-to-most restrictive (the join is the maximum) — consumed from
    governance's grammar (``vocabulary/verdicts.json``), never re-declared."""
    return tuple(_verdicts_vocab()["restrictiveness_order"])
