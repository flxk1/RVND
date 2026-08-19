# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Mock backend — deterministic, no model dependency. For tests + onboarding smoke."""

from __future__ import annotations

import re


# Patterns the mock uses to make plausible classifications. NOT a production
# detector — only for deterministic test behaviour + onboarding smoke verify.
_HEALTH_PATTERNS = re.compile(
    r"\b(chemo|diagnosis|surgery|prescribed|illness|patient|hospitalized|hospital(?:ised|isation)?|"
    r"medication|condition|cancer|diabetes|depression|anxiety|therapy)\b",
    re.IGNORECASE,
)
_NAME_PATTERN = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")  # "Maria Schmidt"
_FINANCIAL_PATTERNS = re.compile(
    r"\b(salary|wage|debt|mortgage|bankruptcy|insolvent|loan|owes?)\b",
    re.IGNORECASE,
)


class MockBackend:
    """Deterministic classifier. Same input → same output, always."""

    def __init__(self):
        self._name = "mock"

    def classify(self, text: str, context: str = "") -> dict:
        if not text or not text.strip():
            return {"contains_pii": False, "type": "none", "confidence": 1.0, "reason": "empty"}

        # Confidential-terms match has highest precedence — if the caller
        # supplied KG-sourced terms and any appear in the text, that's a hit.
        if context and context.strip():
            for line in context.splitlines():
                term = line.strip().lstrip("-*•").strip()
                if term and len(term) >= 3 and term.lower() in text.lower():
                    return {
                        "contains_pii": True,
                        "type": "confidential",
                        "confidence": 0.95,
                        "reason": f"mock matched confidential term '{term}' from context",
                    }

        if _HEALTH_PATTERNS.search(text):
            return {
                "contains_pii": True,
                "type": "health",
                "confidence": 0.85,
                "reason": "mock matched health keyword",
            }

        if _FINANCIAL_PATTERNS.search(text):
            return {
                "contains_pii": True,
                "type": "financial",
                "confidence": 0.80,
                "reason": "mock matched financial keyword",
            }

        if _NAME_PATTERN.search(text):
            return {
                "contains_pii": True,
                "type": "name",
                "confidence": 0.75,
                "reason": "mock matched capitalised-pair name pattern",
            }

        return {
            "contains_pii": False,
            "type": "none",
            "confidence": 0.90,
            "reason": "mock found no triggers",
        }

    def is_available(self) -> bool:
        return True

    def describe(self) -> str:
        return "mock backend (deterministic; no model; test/onboarding use only)"
