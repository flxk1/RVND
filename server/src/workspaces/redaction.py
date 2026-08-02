# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Subject-redaction helpers shared by the stores the erasure sweep reaches.

A store that persists free-form user payloads as plain JSON files beside the
chain (drafts, cards) must rewrite them the same way on an erasure: walk every
string in the payload, count subject occurrences, replace them with
:data:`REDACTED`. This module is that single implementation, so card and draft
erasure can never count or rewrite differently for the same subject.

Matching contract: case-insensitive in the ORIGINAL string (a regex, not
``str.lower()`` indices — lowercasing can change string length and misalign
the splice), non-overlapping, and blind inside existing :data:`REDACTED`
sentinels, so an erasure converges: a re-run neither counts phantom hits nor
corrupts the sentinel when the subject is a substring of it. Dict keys are
walked and rewritten like values; keys that redact to the same sentinel
collapse to one entry, losing only data keyed on the erased subject.
"""

from __future__ import annotations

import re
from typing import Any, Callable

REDACTED = "[REDACTED]"


def _pattern(needle: str) -> "re.Pattern[str]":
    return re.compile(re.escape(needle), re.IGNORECASE)


def _over_segments(text: str,
                   fn: Callable[[str], tuple[str, int]]) -> tuple[str, int]:
    """Apply ``fn`` to the stretches between existing sentinels."""
    parts = text.split(REDACTED)
    if len(parts) == 1:
        return fn(text)
    outs: list[str] = []
    total = 0
    for p in parts:
        o, n = fn(p)
        outs.append(o)
        total += n
    return REDACTED.join(outs), total


def count_ci(text: str, needle: str) -> int:
    """Occurrences of ``needle`` in ``text`` under the matching contract."""
    if not text or not needle:
        return 0
    pat = _pattern(needle)
    return _over_segments(text, lambda p: (p, len(pat.findall(p))))[1]


def replace_ci(text: str, needle: str) -> tuple[str, int]:
    """Rewrite every occurrence to :data:`REDACTED`; returns (text, count)."""
    if not text or not needle:
        return text, 0
    pat = _pattern(needle)
    return _over_segments(text, lambda p: pat.subn(REDACTED, p))


def contains_word_ci(text: str, needle: str) -> bool:
    """True when ``needle`` occurs in ``text`` delimited by non-word
    characters. Deletion decisions key on this rather than plain substring
    so a short subject inside an unrelated word does not destroy data."""
    if not text or not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text,
                     re.IGNORECASE) is not None


def walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            if isinstance(k, str):
                out.append(k)
            out.extend(walk_strings(v))
        return out
    if isinstance(value, list):
        return [s for v in value for s in walk_strings(v)]
    return []


def redact_value(value: Any, needle: str) -> tuple[Any, int]:
    if isinstance(value, str):
        return replace_ci(value, needle)
    if isinstance(value, dict):
        total = 0
        out_d: dict = {}
        for k, v in value.items():
            nk = k
            if isinstance(k, str):
                nk, kn = replace_ci(k, needle)
                total += kn
            out_d[nk], n = redact_value(v, needle)
            total += n
        return out_d, total
    if isinstance(value, list):
        total = 0
        out_l: list = []
        for v in value:
            rv, n = redact_value(v, needle)
            out_l.append(rv)
            total += n
        return out_l, total
    return value, 0
