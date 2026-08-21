# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Tier D — ingest-time prompt-injection scan (A5 mitigation).

The egress lock (Tier B / B+) catches PII *leaving* the boundary, but it
does not classify *instructions* embedded in an ingested document — the
``IGNORE THE ABOVE. NEW INSTRUCTIONS: ...`` pattern that rides along when a
document body is later forwarded into a cloud-LLM prompt.

This module is the structural-pattern scan that runs at the ingest boundary,
*before* a document body can enter a prompt. It does not execute or rewrite
anything; it flags injection-shaped spans as ``Finding`` objects with
``type="prompt_injection"`` so the ingest path (and any caller forwarding the
body downstream) can refuse, quarantine, or surface them.

It is pattern-bound by design — like the Tier B regex layer, it catches the
known shapes, not every possible phrasing. It is a tripwire, not a guarantee;
that limitation is documented in ``docs/reviews/red-team-findings.md`` (A5).

Public surface:
    scan_text(text)      -> list[Finding]
    scan_document(text)  -> list[Finding]   (alias; the name the ingest path uses)
    contains_injection(text) -> bool
"""

from __future__ import annotations

import re

from .core import Finding

TIER = "D"

# Each rule: (compiled pattern, short label, severity).
# Patterns are case-insensitive and matched against the raw document body.
# Severity reflects how unambiguous the injection signal is:
#   high   — an explicit instruction override / exfil directive
#   medium — a role-hijack or trust-subversion phrasing that is often benign
#            in prose but is the canonical lead-in to an injection.
_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    # Instruction-override family
    (re.compile(r"\bignore\s+(?:the\s+)?(?:above|previous|prior|preceding|earlier)\b", re.I),
     "ignore_previous_instructions", "high"),
    (re.compile(r"\bdisregard\s+(?:the\s+)?(?:above|previous|prior|all)\b", re.I),
     "disregard_previous_instructions", "high"),
    (re.compile(r"\bnew\s+instructions?\s*[:\-]", re.I),
     "new_instructions_directive", "high"),
    (re.compile(r"\bforget\s+(?:everything|all|the\s+above|previous)\b", re.I),
     "forget_previous", "high"),
    # Role / system-prompt hijack
    (re.compile(r"\byou\s+are\s+now\b", re.I),
     "role_reassignment", "medium"),
    (re.compile(r"\b(?:system\s+prompt|developer\s+message|system\s+message)\b", re.I),
     "system_prompt_reference", "medium"),
    (re.compile(r"\btreat\s+(?:any|all|the)\s+.*\b(?:system\s+prompts?|instructions?)\b.*\b(?:as\s+)?untrusted\b", re.I),
     "trust_subversion", "high"),
    (re.compile(r"\bact\s+as\s+(?:an?\s+)?(?:unrestricted|jailbroken|DAN)\b", re.I),
     "jailbreak_persona", "high"),
    # Exfiltration directives
    (re.compile(r"\b(?:repeat|reveal|print|output|exfiltrate|send)\b.{0,60}\b(?:home\s+address|password|api[\s_-]?key|secret|ssn|credit\s+card|full\s+name)\b", re.I),
     "exfiltration_directive", "high"),
    (re.compile(r"\bbase64[\s-]?encod", re.I),
     "base64_exfil_hint", "medium"),
    # Tool/MCP subversion
    (re.compile(r"\b(?:call|invoke|use)\s+the\s+\w+\s+tool\s+(?:to|and)\b", re.I),
     "tool_invocation_directive", "medium"),
)


def scan_text(text: str) -> list[Finding]:
    """Return ``Finding`` objects for every injection pattern matched in ``text``.

    Whole-document findings (``field=None``). The detail string names the
    matched rule label and the offending span so a reviewer can locate it.
    """
    if not text:
        return []
    findings: list[Finding] = []
    for pattern, label, severity in _RULES:
        m = pattern.search(text)
        if m:
            span = m.group(0)
            if len(span) > 80:
                span = span[:77] + "..."
            findings.append(
                Finding(
                    tier=TIER,
                    type="prompt_injection",
                    severity=severity,
                    field=None,
                    detail=f"ingest-time injection pattern '{label}' matched: {span!r}",
                    confidence=0.9 if severity == "high" else 0.6,
                )
            )
    return findings


# The ingest path imports this name.
def scan_document(text: str) -> list[Finding]:
    """Alias for :func:`scan_text` — the entry point the ingest boundary calls."""
    return scan_text(text)


def contains_injection(text: str) -> bool:
    """True iff at least one injection pattern is present."""
    return bool(scan_text(text))
