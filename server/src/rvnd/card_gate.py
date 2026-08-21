# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Card enforcement — the allow/hold/deny gate ANY card can carry.

The family already recurs (connector floor `permit|hold|deny`, policy_matrix cell `go|ask|block`,
lens scope `allow|aggregate_only|forbid`, ingest quarantine `admit|hold|reject`) — same three-state
gate, default-deny, strictest-wins, on the signed chain. This module is the ONE primitive that
unifies them so a card gains the mechanism instead of reimplementing it.

A card's enforcement rules are two composable kinds:
  * ENVELOPE — a robots.txt-style allow/disallow on the *shape* (source · type · size). An
    allowlist has bounded gaps → high assurance.
  * SIGNATURES — a detection ruleset over the *content* (delegated to `ingest_quarantine.scan`).
    A denylist raises the floor against known attacks; coverage-bounded.

``enforce`` composes both to a single verdict in the shared lattice, STRICTEST-WINS and
DEFAULT-DENY: an unrecognised verdict is treated as ``hold``, and a candidate outside a present
allowlist is ``deny``. It DECLARES the verdict; the caller enforces (refuse / hold / admit) and
audits — nothing here opens or runs the candidate.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from . import verdict as _v

# the shared three-state lattice — REUSED from rvnd.verdict (the ONE place it is defined,
# not re-spelled here). ALLOW/HOLD/DENY are the canonical Verdict values (permit/hold/deny).
ALLOW, HOLD, DENY = _v.Verdict.PERMIT.value, _v.Verdict.HOLD.value, _v.Verdict.DENY.value


def normalise(word: str) -> str:
    """Map any surface's verb into the canonical tri-state, default-deny lean (unknown → hold).
    Composes ``verdict.py``'s per-surface tables (ADMISSION/LIGHT/LOCK/GATE/RISK_TIER) — it does
    NOT re-spell them; this is just the 'try every surface' convenience one place needs."""
    w = (word or "").strip().lower()
    for table in (_v.ADMISSION, _v.LIGHT, _v.LOCK, _v.RISK_TIER):
        if w in table:
            return table[w].value
    if w.upper() in _v.GATE:
        return _v.GATE[w.upper()].value
    return _v.coerce(w, default=_v.Verdict.HOLD).value


def strictest(verdicts: Iterable[str]) -> str:
    """Strictest-wins across surface words — delegates to ``verdict.strictest_of``."""
    return _v.strictest_of(_v.Verdict(normalise(v)) for v in verdicts).value


def check_envelope(envelope: dict[str, Any], candidate: dict[str, Any]) -> tuple[str, str]:
    """robots.txt-style allow/disallow on the candidate's shape. ``envelope`` = ``{allow: {facet:
    [values]}, disallow: {facet: [values]}, max_size?: int}``; ``candidate`` = ``{source, type,
    size, …}``. disallow match → deny; an allowlist present but not matched → deny (bounded-gap
    allowlist); else allow."""
    if not envelope:
        return ALLOW, "no envelope"
    dis = envelope.get("disallow") or {}
    for facet, banned in dis.items():
        v = candidate.get(facet)
        if v is not None and v in set(banned):
            return DENY, f"disallow {facet}={v}"
    max_size = envelope.get("max_size")
    if max_size is not None:
        try:
            size = int(candidate.get("size", 0) or 0)
        except (TypeError, ValueError):
            # fail CLOSED: a non-numeric size must not crash a verdict function — a size that
            # cannot be verified against a size cap is a DENY, not an exception.
            return DENY, f"size {candidate.get('size')!r} unverifiable against max {max_size}"
        if size > int(max_size):
            return DENY, f"size {candidate.get('size')} > max {max_size}"
    allow = envelope.get("allow") or {}
    for facet, permitted in allow.items():
        v = candidate.get(facet)
        if v is None or v not in set(permitted):
            return DENY, f"not in allowlist {facet}={v!r}"     # allowlist → default-deny
    return ALLOW, "envelope permits"


def enforce(rules: dict[str, Any], *, candidate: dict[str, Any],
            text: Optional[str] = None, data: Optional[bytes] = None,
            filename: Optional[str] = None) -> dict[str, Any]:
    """Compose a card's ENVELOPE + SIGNATURE rules over a candidate → one strictest-wins verdict.

    ``rules`` = ``{envelope: {...}, signatures: True|False}`` (a card's ``enforcement`` block).
    Returns ``{verdict, reasons, envelope, signatures}`` — declared, not executed. The signature
    layer reuses ``ingest_quarantine.scan`` (its ``admit|hold|reject`` normalises into the same
    lattice), so a card's content-defence is the quarantine, uniformly."""
    rules = rules or {}
    reasons: list[str] = []
    env_verdict, env_reason = check_envelope(rules.get("envelope") or {}, candidate)
    reasons.append(f"envelope:{env_verdict} ({env_reason})")
    sig_verdict = ALLOW
    sig_threats: list[dict[str, Any]] = []
    if rules.get("signatures") and (text or data):
        from . import ingest_quarantine as _iq
        v = _iq.scan(text=text, data=data, filename=filename)
        sig_verdict = normalise(v.admission)
        sig_threats = v.threats
        reasons.append(f"signatures:{sig_verdict} ({v.reason})")
    verdict = strictest([env_verdict, sig_verdict])
    return {"verdict": verdict, "reasons": reasons,
            "envelope": env_verdict, "signatures": sig_verdict, "threats": sig_threats}
