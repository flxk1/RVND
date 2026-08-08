# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""T-cons wiring — session-scoped Versum working memory + solver consistency.

The thin RVND seam the reasoning-integrity DoD test needs: each session gets
its own Versum store (working memory), claims are captured into it, and
``check_session`` runs the solver's consistency audit over what is recorded,
returning a fail-closed :class:`ConsistencyVerdict`:

* ``CONSISTENT``  — every recorded claim is grounded, every one survived the
  solver's parser, and the audited closure has no clashing atoms.
* ``INCONSISTENT`` — the closure clashes (C and ¬C). Dominates OPEN: a real ⊥
  among the grounded claims is reported even while other claims are open.
* ``OPEN``        — anything that prevents a full check: no claims recorded,
  ungrounded claims, claims the parser dropped, or any error from the store
  or the solver. "Couldn't check" is NEVER reported as consistent — the
  solver's ``audit`` says ``consistent: True`` over an empty fact set
  (verified live), so this module, not the solver, owns that boundary.

Planes are reached only through the sanctioned adapter seams
(``adapters.versum`` for the store, ``adapters.solver.interpretation`` for the
checker). The richer treat-like-alike level (``check_consistency`` over
``DecidedCase``) is a deliberate later increment: claims do not yet carry the
case features it compares — wiring it now would mean inventing them.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._storage_paths import LOG_ROOT_DEFAULT
from .adapters.solver.interpretation import audit, interpret
from .adapters.versum import append_record, iter_records

CONSISTENT = "CONSISTENT"
INCONSISTENT = "INCONSISTENT"
OPEN = "OPEN"

# Atoms travel into the solver's line-oriented notation ("fact: <atom>"), so
# they must never be able to smuggle extra lines or notation keywords in — an
# atom like "c\nfact: -x" would forge claims. Eager refusal at record time
# (fail-closed house rule), not sanitisation at check time.
_ATOM_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.|]*$")


@dataclass(frozen=True)
class Claim:
    """One recorded assertion. ``polarity`` "-" records ¬atom. ``grounding``
    is the Versum span the claim rests on; ``None`` means ungrounded, which
    keeps the session OPEN — recorded, visible, and never silently trusted."""
    atom: str
    polarity: str = "+"
    grounding: Optional[str] = None
    ts: str = ""


@dataclass(frozen=True)
class ConsistencyVerdict:
    verdict: str
    reasons: tuple[str, ...] = ()
    clashing: tuple[str, ...] = ()
    open_claims: tuple[str, ...] = ()


def session_store(session_id: str, *, log_root: Optional[str] = None) -> str:
    """Per-session Versum ``store_root`` (created on first use).

    The directory key is a digest of the session id, so ids with path-hostile
    characters (``session:{nonce}``) cannot traverse or collide.
    """
    if not session_id:
        raise ValueError("session_id must be non-empty")
    base = Path(log_root) if log_root else LOG_ROOT_DEFAULT
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    root = base / "reasoning_sessions" / digest / "versum"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def record_claim(session_id: str, claim: Claim, *,
                 log_root: Optional[str] = None) -> None:
    """Append one claim to the session's own store (its only mutation)."""
    if claim.polarity not in ("+", "-"):
        raise ValueError(f"polarity must be '+' or '-', got {claim.polarity!r}")
    if not _ATOM_RE.match(claim.atom or ""):
        raise ValueError(
            f"atom {claim.atom!r} refused: atoms are single notation tokens "
            "(letters, digits, '_', '.', '|'); anything else could forge "
            "claim lines in the solver notation")
    append_record(
        session_store(session_id, log_root=log_root),
        record={"atom": claim.atom, "polarity": claim.polarity,
                "grounding": claim.grounding, "ts": claim.ts,
                "body": ("-" if claim.polarity == "-" else "") + claim.atom},
        dimension="claim",
        actor=session_id,
    )


def _recorded_claims(session_id: str, *,
                     log_root: Optional[str] = None) -> tuple[list[Claim], list[str]]:
    """Read claims back. Returns (claims, malformed-descriptions) — a stored
    node this module cannot parse is reported, not skipped, so it surfaces as
    OPEN instead of silently narrowing the check."""
    claims: list[Claim] = []
    malformed: list[str] = []
    for node in iter_records(session_store(session_id, log_root=log_root)):
        rec = (node.get("properties") or {}).get("record") if isinstance(node, dict) else None
        if not isinstance(rec, dict) or "atom" not in rec:
            malformed.append(f"unparseable stored node {str(node)[:80]!r}")
            continue
        claims.append(Claim(atom=str(rec.get("atom", "")),
                            polarity=str(rec.get("polarity", "+")),
                            grounding=rec.get("grounding"),
                            ts=str(rec.get("ts", ""))))
    return claims, malformed


def check_session(session_id: str, *,
                  log_root: Optional[str] = None) -> ConsistencyVerdict:
    """Solver consistency over the session's recorded claims, fail-closed."""
    try:
        claims, malformed = _recorded_claims(session_id, log_root=log_root)
        if not claims and not malformed:
            return ConsistencyVerdict(OPEN, ("no claims recorded — nothing to attest",))
        ungrounded = [c for c in claims if not c.grounding]
        grounded = [c for c in claims if c.grounding]
        open_atoms = tuple(c.atom for c in ungrounded)

        if grounded:
            text = "\n".join(
                "fact: " + ("-" if c.polarity == "-" else "") + c.atom
                for c in grounded)
            interp = interpret(text)
            parsed = interp.get("facts") or set()
            if len(parsed) < len({("-" if c.polarity == "-" else "") + c.atom
                                  for c in grounded}):
                # The solver's parser dropped something we recorded. audit()
                # over the remainder could claim consistency for claims it
                # never saw — that is the fail-open trap; refuse to conclude.
                return ConsistencyVerdict(
                    OPEN,
                    ("solver parser dropped recorded claim(s) — cannot attest what was not checked",),
                    open_claims=open_atoms)
            result = audit(interp)
            if not result.get("consistent", False):
                reasons = tuple(str(r) for r in result.get("reasons", ()))
                clash: tuple[str, ...] = ()
                for r in reasons:
                    m = re.search(r"clashing atoms?:\s*([^)\]]+)", r)
                    if m:
                        clash += tuple(a.strip() for a in m.group(1).split(",") if a.strip())
                return ConsistencyVerdict(INCONSISTENT, reasons, clashing=clash,
                                          open_claims=open_atoms)

        if ungrounded or malformed:
            reasons = tuple(f"ungrounded claim: {a}" for a in open_atoms) + tuple(malformed)
            return ConsistencyVerdict(OPEN, reasons, open_claims=open_atoms)
        return ConsistencyVerdict(CONSISTENT,
                                  ("all claims grounded; closure has no clashing atoms",))
    except Exception as e:  # noqa: BLE001 — the fail-closed boundary
        return ConsistencyVerdict(OPEN, (f"check failed: {type(e).__name__}: {e}",))
