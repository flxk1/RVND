# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Heuristic extractor for mathematical content.

The rule extractor models *normative* text (subject / modal / action). Maths
is shaped differently: a problem states what is **given**, what to **find**,
and in which **domain** it lives; a solution is a **proof** or a computed
**result** reached in steps. This module pulls that structure out of theorem
statements, proofs, and worked problems.

It is heuristic and pattern-based — like the rule extractor, it captures the
common shapes (``Let … Prove that …``, ``Given … Find …``, ``Theorem … Proof
… QED``) rather than parsing arbitrary mathematics. What it cannot place it
leaves empty rather than guessing.

Consumed by domain_nds (the maths domain ND registers extract_math); the
per-domain tests cover the shapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class MathProblem:
    """The typed structure of one mathematical problem."""

    statement: str = ""          # the claim or question, verbatim
    given: list[str] = field(default_factory=list)   # hypotheses / data
    find: str = ""               # the goal: what to prove, find, or compute
    domain: str = "general"      # algebra | calculus | logic | geometry | number-theory | general
    steps: list[str] = field(default_factory=list)   # proof / solution steps in order
    body_format: str = "prose"   # "proof" | "equation" | "prose"
    confidence: float = 0.0      # 0–1; how many slots were filled cleanly


_DOMAIN_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("calculus", ("integral", "∫", "\\int", "derivative", "limit", "\\lim", "differential", "\\sum")),
    ("logic", ("theorem", "lemma", "corollary", "proof", "qed", "∎", "iff", "if and only if", "∀", "∃")),
    ("geometry", ("triangle", "circle", "angle", "polygon", "perpendicular", "parallel", "radius")),
    ("number-theory", ("prime", "divisible", "modulo", "gcd", "congruent", "integer solutions")),
    ("algebra", ("matrix", "vector", "polynomial", "eigen", "determinant", "equation", "factor")),
]

_FIND_RE = re.compile(
    r"\b(?:prove that|show that|find|compute|determine|evaluate|solve for|prove|verify that)\b\s*[:\-]?\s*(.+?)(?:\.|$)",
    re.IGNORECASE | re.MULTILINE,
)
_GIVEN_RE = re.compile(
    r"^\s*(?:let|given|suppose|assume|consider|for any|for all)\b\s*[:\-]?\s*(.+?)(?:\.|$)",
    re.IGNORECASE | re.MULTILINE,
)
_STATEMENT_RE = re.compile(
    r"\b(?:theorem|lemma|corollary|proposition|claim)\b\s*[:\.\-]?\s*(.+?)(?:\.|$)",
    re.IGNORECASE,
)

# A math signal must be present before we extract — otherwise ordinary prose
# ("please find attached") false-triggers on verbs like "find". This mirrors
# the signals the classifier uses to route content to the math ND.
_MATH_SIGNAL_RE = re.compile(
    r"\b(?:theorem|lemma|corollary|proposition|proof|qed|prove|equation"
    r"|integral|derivative|polynomial|matrix|vector|prime|modulo|congruent"
    r"|integer)\b|\$[^\$]+\$|\\int|\\sum|\\sqrt|\\begin\{equation\}|∫|∑|√|∎",
    re.IGNORECASE,
)


def _detect_domain(content: str) -> str:
    low = content.lower()
    for domain, keywords in _DOMAIN_KEYWORDS:
        if any(k.lower() in low for k in keywords):
            return domain
    return "general"


def _extract_steps(content: str) -> list[str]:
    """Pull proof/solution steps: the lines after a 'Proof' marker."""
    m = re.search(r"\bproof\b\s*[:\.\-]?", content, re.IGNORECASE)
    if not m:
        return []
    tail = content[m.end():]
    # Stop at an end-of-proof marker if present.
    tail = re.split(r"\b(?:qed|∎)\b|\\?□", tail, maxsplit=1, flags=re.IGNORECASE)[0]
    steps = [s.strip() for s in re.split(r"(?:\.\s+|\n)", tail) if s.strip()]
    return steps[:20]


def extract_math(content: str) -> list[MathProblem]:
    """Extract the math problem structure from ``content``.

    Returns a list (usually one item). Empty list when no math shape is found.
    """
    if not content or not content.strip():
        return []
    # Require a genuine math signal; do not extract from ordinary prose.
    if not _MATH_SIGNAL_RE.search(content):
        return []

    statement_m = _STATEMENT_RE.search(content)
    statement = statement_m.group(1).strip() if statement_m else ""

    find_m = _FIND_RE.search(content)
    find = find_m.group(1).strip() if find_m else ""

    given = [g.strip() for g in (m.group(1).strip() for m in _GIVEN_RE.finditer(content)) if g][:10]

    steps = _extract_steps(content)
    domain = _detect_domain(content)

    has_latex = bool(re.search(r"\$[^\$]+\$|\\int|\\sum|\\sqrt|\\begin\{equation\}", content))
    if steps:
        body_format = "proof"
    elif has_latex:
        body_format = "equation"
    else:
        body_format = "prose"

    if not (statement or find or given or steps):
        return []

    # Confidence: reward each cleanly-filled slot.
    filled = sum(bool(x) for x in (statement, find, given, steps))
    confidence = round(min(1.0, 0.25 * filled + 0.1 * has_latex), 2)

    return [MathProblem(
        statement=statement or (find and f"Find: {find}") or "math content",
        given=given,
        find=find,
        domain=domain,
        steps=steps,
        body_format=body_format,
        confidence=confidence,
    )]
