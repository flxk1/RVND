# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""GOVERN — the CLI seam onto the one oversight chokepoint.

Whatever calls this (a hook, a scheduler, an external caller with no Python
import path into RVND) gets the SAME PERMIT / HOLD / DENY decision, recorded
on the SAME signed chain, that any in-process caller of
``governance.decide_action`` gets — this module adds no new authority, no
new gate, no new signing path. It is a thin argparse front door onto
``governance.decide_action``, which already records the gate-verdict on the
folder's signed mutation chain via ``incidents.log_gate_decision`` (see
``governance.py``, D9) before this module ever sees the result.

Surface class: a non-visual host/operator CLI contract — invoked as
``python -m rvnd.govern`` by the runner's GOVERN step (and by any external
caller with no Python import path into RVND), never a console panel — and
therefore **internal by design**: it exposes no new authority or surface
beyond ``governance.decide_action``, so the surface verifier exempts it (cf.
``rvnd.witness_escape``, which is instead reached by static import).

Mirrors the existing ``rvnd.witness_escape`` CLI shape: ``main(argv) -> int``,
``if __name__ == "__main__": sys.exit(main())``, one machine-readable JSON
line on stdout per invocation, nothing hand-rolled.

Exit code contract (distinct from ``witness_escape``'s 0/1 shape, because
GOVERN's whole job is to surface the tri-state, not just success/failure):

  - ``0`` — ``verdict == "permit"``
  - ``3`` — ``verdict == "hold"``  (push to the human, wait)
  - ``4`` — ``verdict == "deny"``  (refused)
  - ``2`` — the call itself could not be resolved (a malformed workspace, a
    ``decide_action`` failure unrelated to the tri-state) — distinct from
    every tri-state outcome above so a caller never mistakes "we could not
    even ask the gate" for a governed refusal.

The real grade is passed straight through to ``decide_action`` — never
clamped, never defaulted-away — so a suspended actor's grade is still capped
to L0 by ``governance._actor_grade_cap`` inside the gate itself (D9); this
module does not pre-empt or duplicate that cap.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional


__all__ = ["main"]

#: Exit codes for the tri-state verdict — see module docstring.
EXIT_PERMIT = 0
EXIT_HOLD = 3
EXIT_DENY = 4
#: The call to the gate itself could not be resolved (not a tri-state outcome).
EXIT_ERROR = 2

_VERDICT_EXIT = {
    "permit": EXIT_PERMIT,
    "hold": EXIT_HOLD,
    "deny": EXIT_DENY,
}


def _govern(
    folder: str,
    *,
    actor: str,
    action_class: str,
    grade: str,
    log_root: Optional[str] = None,
) -> dict[str, Any]:
    """Call the ONE chokepoint (``governance.decide_action``) and return its
    decision dict unchanged. No new gate, no new signing — this composes the
    existing public API only."""
    from .governance import decide_action

    return decide_action(
        folder,
        action_class=action_class,
        actor=actor,
        grade=grade,
        log_root=log_root,
    )


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m rvnd.govern",
        description="Resolve one action to PERMIT / HOLD / DENY through the "
                     "one oversight chokepoint (governance.decide_action) "
                     "and print the verdict as one JSON line.",
    )
    parser.add_argument("--folder", required=True, help="workspace folder (folder_context)")
    parser.add_argument("--actor", required=True, help="the actor requesting the action")
    parser.add_argument("--action-class", default="shell.exec", dest="action_class",
                        help="the action class being requested (default: shell.exec)")
    parser.add_argument("--grade", required=True,
                        help="the requested autonomy grade (e.g. L2) — passed "
                             "through unclamped; a suspended actor is still "
                             "capped by the gate itself")
    parser.add_argument("--log-root", default=None, dest="log_root",
                        help="override the mutation-log root (optional)")

    args = parser.parse_args(argv)

    try:
        decision = _govern(
            args.folder,
            actor=args.actor,
            action_class=args.action_class,
            grade=args.grade,
            log_root=args.log_root,
        )
    except Exception as exc:  # noqa: BLE001 — surface, never swallow
        print(json.dumps({
            "error": f"{type(exc).__name__}: {exc}",
            "folder": args.folder,
            "actor": args.actor,
            "action_class": args.action_class,
            "grade": args.grade,
        }))
        print(f"govern failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    verdict = decision.get("verdict")
    payload = {
        "audit_id": decision.get("audit_id"),
        "verdict": verdict,
        "light": decision.get("light"),
        "gate_verdict": decision.get("gate_verdict"),
        "grade": decision.get("grade"),
        "requested_grade": decision.get("requested_grade"),
        "reason": decision.get("reason"),
        "action_class": decision.get("action_class"),
        "actor": decision.get("actor"),
    }
    print(json.dumps(payload))

    exit_code = _VERDICT_EXIT.get(verdict)
    if exit_code is None:
        # decide_action's verdict is documented as always one of permit/hold/
        # deny (verdict.py's tri-state) — an unrecognised value here means
        # something upstream changed shape. Fail closed rather than guess.
        print(f"govern: unrecognised verdict {verdict!r}", file=sys.stderr)
        return EXIT_ERROR
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
