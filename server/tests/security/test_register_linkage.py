# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Register↔test linkage for the whole attack namespace.

`docs/reviews/red-team-findings.md` claims to be the durable register and
that `A<n>` is one namespace shared with `tests/security/`. This gate makes
both claims structural instead of aspirational:

- every `test_attack_*.py` docstring must open with its `A<n>` ID;
- every such ID must have a `## A<n>` entry in the register;
- no ID may be claimed by two different attack files (the A3/A4 collision
  between the register and the tests existed once; it must not come back).

The existing per-file meta-tests (A5, A6) stay — they pin the *content* of
their entries; this pins the *namespace*.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_REGISTER = _REPO_ROOT / "docs" / "reviews" / "red-team-findings.md"

_DOC_ID = re.compile(r'^"""(A\d+)\s+—', re.MULTILINE)


def _attack_files() -> list[Path]:
    return sorted(_HERE.glob("test_attack_*.py"))


def test_register_exists():
    assert _REGISTER.is_file(), (
        "docs/reviews/red-team-findings.md missing — the attack regressions "
        "have no durable register. Restore it; do not delete history."
    )


def test_every_attack_file_declares_an_id():
    missing = [
        f.name for f in _attack_files()
        if not _DOC_ID.search(f.read_text(encoding="utf-8"))
    ]
    assert not missing, (
        f"attack files without an 'A<n> —' docstring ID: {missing}. Every "
        "attack regression carries the ID of its register entry."
    )


def test_every_attack_id_has_a_register_entry():
    register = _REGISTER.read_text(encoding="utf-8")
    unregistered = []
    for f in _attack_files():
        m = _DOC_ID.search(f.read_text(encoding="utf-8"))
        if m and f"## {m.group(1)} " not in register and f"## {m.group(1)}\n" not in register:
            unregistered.append(f"{f.name} ({m.group(1)})")
    assert not unregistered, (
        f"attack tests with no register entry: {unregistered}. Add the entry "
        "to docs/reviews/red-team-findings.md (Status/Tier/Original gap/"
        "Mitigation/Coverage) — a finding without a register entry is not "
        "durable evidence."
    )


def test_no_id_is_claimed_twice():
    seen: dict[str, str] = {}
    dupes = []
    for f in _attack_files():
        m = _DOC_ID.search(f.read_text(encoding="utf-8"))
        if not m:
            continue
        aid = m.group(1)
        if aid in seen:
            dupes.append(f"{aid}: {seen[aid]} and {f.name}")
        seen[aid] = f.name
    assert not dupes, (
        f"attack ID claimed by two files: {dupes}. One namespace, one ID per "
        "attack class — pick the next free A<n> for the newer finding."
    )
