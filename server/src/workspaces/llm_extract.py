# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""LLM extractor — a WORKSPACE OPERATION (not a vertical one).

The regex NDs read EU legislative prose well and arbitrary text poorly. This is
the general mechanism that reads *any* register into the SAME typed schema:

    extract(text, target, domain_profile) -> ExtractionResult

- ``target`` says what shape to emit: "obligations" | "facets" | "placement".
- ``domain_profile`` is the only domain-specific input: a vocabulary + a few
  worked exemplars + a confidence floor. The MECHANISM is built once and lives
  in the substrate; a vertical never owns an extractor, it hands the shared one
  a profile. Domain-specificity is therefore data, not code.

Substrate guarantees inherited here (not re-implemented):
- **Local-first / Lock.** The model call goes to the configured *local*
  endpoint (``local_llm.complete``) — no cloud egress — so confidential system /
  evidence text never leaves the machine. An optional ``lock`` callable can
  scrub/refuse before the call for defence in depth.
- **Candidates, never answers.** Every emitted item carries a confidence and an
  ``extractor: "llm"`` provenance tag. Items below the profile's floor are
  marked ``below_floor=True`` so the Oversight module routes them to a human.
  The matcher / coverage arithmetic downstream stay deterministic.
- **Graceful fallback.** With no model configured the call returns ``ok=False``
  and an empty result, so the caller keeps its regex extraction — the LLM is an
  upgrade path, never a hard dependency.

The output schema is identical to the regex NDs' pairs, so everything
downstream (enrich → match → house → coverage → memo) is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional


# A backend is anything with the local_llm.complete shape:
#   (prompt: str) -> {"ok": bool, "response": str, ...}
Backend = Callable[[str], dict[str, Any]]


@dataclass
class DomainProfile:
    """The only domain-specific input to the shared extractor.

    ``vocabulary`` constrains what the model may emit (facet names/values, or
    operator set). ``exemplars`` are few-shot worked examples in the target
    schema. ``confidence_floor`` gates which items ship as auto-usable vs.
    surface-to-human. ``instructions`` is optional extra domain guidance.
    """
    domain: str
    vocabulary: dict[str, Any] = field(default_factory=dict)
    exemplars: list[dict[str, Any]] = field(default_factory=list)
    confidence_floor: float = 0.7
    instructions: str = ""


@dataclass
class ExtractionResult:
    ok: bool
    target: str
    items: list[dict[str, Any]] = field(default_factory=list)
    model_used: str = ""
    error: str = ""
    locked: bool = False
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hash(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Prompt construction (per target schema)
# ---------------------------------------------------------------------------

_TARGET_INSTRUCTIONS = {
    "obligations": (
        "Extract every normative rule as a JSON array. Each item: "
        '{"operator": one of O|P|F|R (Obligation/Permission/proHibition/Right), '
        '"bearer": who it binds, "action": what, "condition": when (or ""), '
        '"exception": carve-out (or ""), "confidence": 0..1}. '
        "Transcribe only what the text states; do not infer duties."
    ),
    "facets": (
        "Read the description and fill ONLY facets from the provided vocabulary "
        'as a JSON object {facet_name: value_or_list}. Omit any facet you cannot '
        "determine from the text (do not guess). Add "
        '"_confidence": {facet: 0..1} for each filled facet.'
    ),
    "placement": (
        "Decide which requirement room (from the provided list) this document "
        'furnishes. Return JSON {"room_id": id or null, "confidence": 0..1, '
        '"reason": short}. Return null room_id if it fits none.'
    ),
}


def _build_prompt(text: str, target: str, profile: DomainProfile) -> str:
    parts: list[str] = []
    parts.append(f"You are a precise extraction tool for the {profile.domain!r} "
                 f"domain. Output VALID JSON ONLY, no prose.")
    if profile.instructions:
        parts.append(profile.instructions)
    parts.append(_TARGET_INSTRUCTIONS.get(target, _TARGET_INSTRUCTIONS["obligations"]))
    if profile.vocabulary:
        parts.append("Vocabulary (use only these names/values):\n"
                     + json.dumps(profile.vocabulary, ensure_ascii=False))
    if profile.exemplars:
        parts.append("Examples:\n" + json.dumps(profile.exemplars, ensure_ascii=False))
    parts.append("TEXT:\n" + text)
    parts.append("JSON:")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Robust JSON extraction from a model response
# ---------------------------------------------------------------------------

def _parse_json(response: str) -> Optional[Any]:
    """Pull the first JSON array/object out of a model response.

    Tolerates code fences and leading prose. Returns None on failure (the
    caller treats that as "no items", never crashes)."""
    if not response:
        return None
    # strip ```json ... ``` fences
    fenced = re.search(r"```(?:json)?\s*(.+?)```", response, re.DOTALL)
    candidate = fenced.group(1) if fenced else response
    # try whole, then the first {...} or [...] span
    for attempt in (candidate, _first_span(candidate, "[", "]"),
                    _first_span(candidate, "{", "}")):
        if not attempt:
            continue
        try:
            return json.loads(attempt)
        except Exception:
            continue
    return None


def _first_span(s: str, open_c: str, close_c: str) -> Optional[str]:
    start = s.find(open_c)
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == open_c:
            depth += 1
        elif s[i] == close_c:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


# ---------------------------------------------------------------------------
# Shaping parsed JSON into the canonical pair schema (target=obligations)
# ---------------------------------------------------------------------------

_VALID_OPS = {"O", "P", "F", "R"}


def _to_obligation_pairs(items: Any, profile: DomainProfile,
                         source_document: Optional[str]) -> list[dict[str, Any]]:
    """Shape LLM obligation items into the same pair dict the regex NDs emit,
    so enrich/match/house/coverage consume them unchanged."""
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    floor = profile.confidence_floor
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        op = str(it.get("operator", "O")).upper()[:1]
        if op not in _VALID_OPS:
            op = "O"
        bearer = str(it.get("bearer", "")).strip()
        action = str(it.get("action", "")).strip()
        if not bearer and not action:
            continue
        conf = float(it.get("confidence", 0.6) or 0.6)
        pid = _hash(f"{profile.domain}|{bearer}|{action}|{idx}")
        out.append({
            "id": pid,
            "problem": {
                "id": f"{pid}-p", "kind": "deontic-formula", "scope": profile.domain,
                "type": "mental-model",
                "summary": f"{op}({bearer} : {action[:60]})",
                "facets": {"operator": op, "bearer": bearer},
                "source_document": source_document or profile.domain,
            },
            "solution": {
                "id": pid, "problem_id": f"{pid}-p",
                "operator": op, "bearer": bearer, "action": action,
                "condition": str(it.get("condition", "")).strip(),
                "exception": str(it.get("exception", "")).strip(),
                "body_format": "structured-deontic",
                "authority_tier": 1,
                "confidence": round(conf, 3),
                "extractor": "llm",
                "below_floor": conf < floor,
            },
        })
    return out


# ---------------------------------------------------------------------------
# The operation
# ---------------------------------------------------------------------------

def extract(
    text: str,
    target: str,
    profile: DomainProfile,
    *,
    backend: Optional[Backend] = None,
    lock: Optional[Callable[[str], dict[str, Any]]] = None,
    model: Optional[str] = None,
) -> ExtractionResult:
    """Extract ``target`` from ``text`` using a local model + domain profile.

    ``backend`` defaults to ``local_llm.complete`` (the configured LOCAL
    endpoint — no cloud egress). Pass a mock for tests. ``lock``, if given, is
    called on the text first; if it refuses (``{"action": "refuse"}``) the call
    is aborted and ``locked=True`` is returned with no items.
    """
    if lock is not None:
        verdict = lock(text)
        if verdict.get("action") == "refuse":
            return ExtractionResult(ok=False, target=target, locked=True,
                                    error="lock refused egress of this text")

    if backend is None:
        from .local_llm import complete as _complete
        backend = lambda p: _complete(p, model=model)  # noqa: E731

    prompt = _build_prompt(text, target, profile)
    resp = backend(prompt)
    if not resp.get("ok"):
        # graceful fallback — caller keeps its regex result
        return ExtractionResult(ok=False, target=target,
                                error=resp.get("error", "backend unavailable"))

    raw = resp.get("response", "")
    parsed = _parse_json(raw)
    model_used = resp.get("model_used", "")

    if target == "obligations":
        items = _to_obligation_pairs(parsed, profile,
                                     source_document=profile.domain)
    elif target in ("facets", "placement"):
        # facets/placement return the parsed JSON as-is (caller maps to card /
        # room); attach nothing structural — they are not pairs.
        items = parsed if isinstance(parsed, list) else ([parsed] if parsed else [])
    else:
        items = parsed if isinstance(parsed, list) else ([parsed] if parsed else [])

    return ExtractionResult(ok=True, target=target, items=items,
                            model_used=model_used, raw=raw[:2000])


# ---------------------------------------------------------------------------
# Hybrid helper: regex first, LLM to fill the gap
# ---------------------------------------------------------------------------

def extract_obligations_hybrid(
    text: str,
    profile: DomainProfile,
    *,
    regex_pairs: Optional[list[dict[str, Any]]] = None,
    backend: Optional[Backend] = None,
    min_regex: int = 1,
    lock: Optional[Callable[[str], dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Cheap path first, LLM only when regex is thin.

    If the regex extractor already returned >= ``min_regex`` pairs, use those
    (cheap, deterministic). Otherwise call the LLM to read a register the regex
    could not. This is the cost discipline: pay for the model only on hard input.
    Returns pairs in the canonical schema either way.
    """
    if regex_pairs and len(regex_pairs) >= min_regex:
        return regex_pairs
    res = extract(text, "obligations", profile, backend=backend, lock=lock)
    if res.ok and res.items:
        return res.items
    return regex_pairs or []
