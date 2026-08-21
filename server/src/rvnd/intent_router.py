# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Intent router — ONE input, context-aware, → the right governance operation.

Not three chat UIs; one box. The same sentence means different things by shape:

    a question           → ASK     (governance_ask → matching cards)
    a self-description    → INTAKE  (fill the subject card via fact_intake)
    a policy document     → POLICY  (policy_ingest → rules)

Deterministic-first (the heuristics below); an optional LLM proposer resolves the ambiguous
middle. Either way the router only CLASSIFIES intent and ECHOES it — the caller dispatches to the
existing op, and the inferred intent is shown so the user can correct a wrong guess (same fence as
everywhere: the model proposes, you can override, each op stays deterministic + audited)."""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

#: intent → the op that handles it (the caller dispatches; the router does not execute)
OPS = {"ask": "governance_ask", "intake": "subject_card", "policy": "policy_ingest"}

_QUESTION = re.compile(r"^(which|what|who|where|when|how|show|find|list|does|do|is|are|can|why|search)\b", re.I)
_FIRST_PERSON = re.compile(
    r"\b(i am|i'?m|we are|we'?re|our (?:system|company|org|organisation|organization|tool|product)|"
    r"my (?:system|company|org|tool|product)|we (?:deploy|use|provide|build|operate|process)|"
    r"i (?:deploy|use|provide|build|operate|process))\b", re.I)
_ARTICLE = re.compile(r"(?m)^\s*(?:article|art\.|§)\s*\d", re.I)
_NORMATIVE = re.compile(r"\b(?:shall not|must not|may not|shall|must|is prohibited|are required to)\b", re.I)


def _result(intent: str, text: str, *, why: str) -> dict[str, Any]:
    return {"intent": intent, "dispatch": OPS[intent], "text": text,
            "echo": f"inferred: {intent}", "why": why}


def route(text: str, *, context: Optional[dict[str, Any]] = None,
          llm: Optional[Callable[[str, dict], dict]] = None) -> dict[str, Any]:
    """Classify one input into an intent. Order: question → self-description → policy document →
    (ambiguous) optional LLM → default ASK (search is the safe default — it reads, never writes)."""
    t = (text or "").strip()
    low = t.lower()
    if not t:
        return _result("ask", t, why="empty")
    if t.endswith("?") or _QUESTION.match(low):
        return _result("ask", t, why="question-shaped")
    if _FIRST_PERSON.search(low):
        return _result("intake", t, why="first-person self-description")
    normative = len(_NORMATIVE.findall(low))
    if _ARTICLE.search(t) or normative >= 2 or (normative >= 1 and len(t) > 120):
        return _result("policy", t, why="document/normative-shaped")
    if normative >= 1:
        return _result("policy", t, why="a single normative sentence (one rule)")
    if llm:                                          # ambiguous middle → optional proposer
        try:
            proposed = (llm(t, context or {}) or {}).get("intent")
            if proposed in OPS:
                return _result(proposed, t, why="llm-proposed (ambiguous)")
        except Exception:
            pass
    return _result("ask", t, why="ambiguous → default to search (read-only, safe)")
