# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Witness escape — recording, and responding to, an out-of-territory touch.

A "witness" (a run, a skill invocation, an agent's declared working set) is
scoped to a folder's territory. When the run's own trace shows it touched a
path OUTSIDE that territory, that is a witness escape. ``record_witness_escape``
does BOTH halves of detect-then-respond, in one call:

  1. **Detect, on the record**: append exactly one event to the folder's
     signed mutation chain via the EXISTING ``MutationLog.append`` signing
     path (never hand-rolled signing) — placing its discriminator at
     ``extra["kind"]``, the same place ``incidents.log_gate_decision``
     (``kind == "gate-verdict"``) and ``parties.set_party_status``
     (``kind == "PartyStatus"``) already place theirs.

  2. **Respond, causally**: quarantine the actor by calling the PUBLIC
     ``parties.set_party_status(folder_context, actor, "suspended")`` API.
     That single call is what gives this module teeth: ``governance.
     _actor_grade_cap`` reads the party register and caps a suspended
     actor's autonomy grade to ``L0`` BEFORE the gate runs
     (``governance.decide_action``, D9). Suspension is reversible and
     human-clear-only — this module only ever moves an actor TO
     ``suspended``, never back to ``active``, and never to ``killed``. Only a
     human-invoked ``parties.set_party_status(folder, actor, "active")``
     restores the actor's grade; nothing in this module exposes a
     self-clear path.

Both steps use ONLY the existing public APIs (``MutationLog.append`` /
``verify_chain``, ``parties.set_party_status``) — this module adds no new
signing, no new party-registry storage, and no new authority of its own. It
composes what already exists.

On any failure, this module raises a typed error rather than silently
no-op'ing or returning a partial result:

  - a malformed call (no actor, no paths) raises BEFORE anything is written
    — no event, no suspension;
  - a failed append, or a chain that no longer verifies after the append,
    raises BEFORE the quarantine call is even attempted — an unsigned or
    unverifiable record is never used as grounds to suspend anyone;
  - a quarantine call that fails AFTER the event was cleanly appended and
    verified raises a distinct typed error that names the audit_id of the
    (now-permanent, append-only) event, so the caller knows the record
    landed but the actor is NOT yet capped and needs attention.

Internal by design: its operator surface is the ``python -m rvnd.witness_escape``
CLI, called by whatever detects an out-of-territory touch, not a console panel.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Optional

from .mutation_log import ChainVerificationResult, LogEvent, MutationLog

__all__ = [
    "WITNESS_ESCAPE_KIND",
    "WitnessEscapeError",
    "WitnessEscapeInputError",
    "WitnessEscapeRecordError",
    "WitnessEscapeVerificationError",
    "WitnessEscapeQuarantineError",
    "record_witness_escape",
    "recent_witness_escapes",
    "main",
]

#: The value placed at ``extra["kind"]`` on a witness-escape event — the same
#: convention ``incidents.log_gate_decision`` (``extra["kind"] = "gate-verdict"``)
#: and ``parties.set_party_status`` (``extra["kind"] = "PartyStatus"``) already
#: use: the event's own ``event`` field stays the generic ``"system"``, and
#: ``extra.kind`` is what a scanner keys on.
WITNESS_ESCAPE_KIND = "witness-escape"

#: The actor attributed to the automatic quarantine's PartyStatus event —
#: this is a SYSTEM action (the tripwire responding to a recorded escape),
#: never self-attributed to the actor being suspended, and distinct from a
#: human's later, explicit clear.
_QUARANTINE_ACTOR = "system"


class WitnessEscapeError(Exception):
    """Base for every typed failure this module raises. Never a silent no-op:
    a call that cannot complete its contract raises one of these rather than
    returning a partial or empty result."""


class WitnessEscapeInputError(WitnessEscapeError, ValueError):
    """Raised for a malformed call — missing actor, empty path list — before
    anything is written."""


class WitnessEscapeRecordError(WitnessEscapeError):
    """Raised when the append to the mutation log itself fails (disk full,
    sealed workspace, lock failure, ...). Wraps the underlying exception.
    Nothing is quarantined when this is raised."""


class WitnessEscapeVerificationError(WitnessEscapeError):
    """Raised when the event appended cleanly but ``verify_chain()`` no longer
    returns ``ok`` afterward. Nothing is quarantined when this is raised — an
    unverifiable chain is never used as grounds to suspend anyone."""


class WitnessEscapeQuarantineError(WitnessEscapeError):
    """Raised when the witness-escape event was appended AND verified, but
    the causal quarantine (``parties.set_party_status(..., "suspended")``)
    itself failed. The event is already permanent on the append-only chain
    (named by ``audit_id`` in the message) — this error exists so a caller
    never mistakes "the record landed" for "the actor is capped"."""


def _relpath(path: Any, folder_context: str | Path) -> str:
    """Best-effort relative form of an escaped path for the event payload.

    An escaped path is, by definition, likely OUTSIDE ``folder_context`` — the
    relative form (when both are absolute) surfaces how far outside via
    leading ``../`` segments, which is more informative than a raw absolute
    path and keeps the event free of the caller's absolute filesystem layout.
    Falls back to the path string with any leading separators stripped when a
    relative form can't be computed (different drives, a non-absolute
    ``folder_context``, ...) — never raises on a path shape it doesn't like.
    """
    p = str(path)
    if not p:
        return p
    try:
        base = Path(folder_context).expanduser()
        pp = Path(p).expanduser()
        if pp.is_absolute() and base.is_absolute():
            import os
            return os.path.relpath(pp, base)
    except Exception:
        pass
    return p.lstrip("/\\")


def record_witness_escape(
    folder_context: str | Path,
    unauthorised_paths: Iterable[Any],
    actor: str,
    *,
    run_since: Optional[float] = None,
    log_root: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Append EXACTLY ONE witness-escape event to ``folder_context``'s signed
    mutation chain, THEN causally quarantine ``actor`` — atomically-in-effect:
    either both steps land, or a typed error tells the caller exactly which
    step failed and what (if anything) is now on the chain.

    Returns ``{"audit_id": str, "event": dict}`` — ``event`` is the appended
    ``LogEvent`` (post-append: ``prev_hash``/``signature``/``host_id`` filled
    in) as a plain dict, so a caller doesn't need this module's dataclass to
    read it back. ``audit_id`` is the witness-escape event's own id (not the
    quarantine event's).

    The event carries ``extra = {"kind": "witness-escape", "actor": actor,
    "paths": [...], "run_since": run_since, "count": len(paths)}`` — the same
    place existing gate-verdict / incident events carry their ``kind``.

    The quarantine is exactly one call to the PUBLIC parties API,
    ``parties.set_party_status(folder_context, actor, "suspended", ...)`` —
    the SAME status transition ``governance._actor_grade_cap`` reads to cap a
    suspended actor's autonomy grade to ``L0`` before every subsequent gate
    decision in this folder (``governance.decide_action``, D9). Reversible
    (a human can later restore ``"active"``) and never ``"killed"``.

    Note on scope (inherent to the party register, not a limitation added
    here): the cap only takes hold for an actor already registered as a
    party in this folder (``parties.register_party``) — a PartyStatus event
    for an unregistered ``party_id`` is recorded but has no projected effect,
    same as any other caller of ``set_party_status``. This module does not
    register parties; it only suspends one, per the contract.

    Raises:
        WitnessEscapeInputError: ``actor`` is empty, or ``unauthorised_paths``
            is empty (once blank entries are stripped) — nothing is written
            for a malformed call: no event, no suspension.
        WitnessEscapeRecordError: the append itself failed (propagates the
            underlying cause — ``SealedWriteError``, ``DiskFullError``, or any
            other failure of ``MutationLog.append``). No suspension is
            attempted.
        WitnessEscapeVerificationError: the append succeeded but
            ``verify_chain()`` no longer returns ``ok`` afterward. No
            suspension is attempted.
        WitnessEscapeQuarantineError: the event was appended and verified,
            but ``parties.set_party_status`` itself raised. The event is
            already permanent on the chain (its ``audit_id`` is named in the
            error); the actor is NOT quarantined.

    Never silently no-ops: every failure path above raises rather than
    returning a partial result or swallowing the error.
    """
    actor = (actor or "").strip()
    if not actor:
        raise WitnessEscapeInputError("actor is required")
    paths = [_relpath(p, folder_context) for p in (unauthorised_paths or []) if str(p).strip()]
    if not paths:
        raise WitnessEscapeInputError("unauthorised_paths must be non-empty")

    extra = {
        "kind": WITNESS_ESCAPE_KIND,
        "actor": actor,
        "paths": paths,
        "run_since": run_since,
        "count": len(paths),
    }
    event = LogEvent(
        event="system",
        folder_path=str(folder_context),
        pair_id=WITNESS_ESCAPE_KIND,
        channel="system",
        actor=actor,
        extra=extra,
    )

    log = MutationLog(folder_context, log_root=log_root)
    try:
        audit_id = log.append(event)
    except WitnessEscapeError:
        raise
    except Exception as exc:  # SealedWriteError, DiskFullError, OSError, ...
        raise WitnessEscapeRecordError(
            f"failed to record witness escape for actor {actor!r} in "
            f"{folder_context!r}: {type(exc).__name__}: {exc}"
        ) from exc

    verification: ChainVerificationResult = log.verify_chain()
    if not verification.ok:
        raise WitnessEscapeVerificationError(
            "witness-escape event was appended but the chain no longer "
            f"verifies afterward (audit_id={audit_id}): "
            f"broken_links={len(verification.broken_links)} "
            f"signature_failures={len(verification.signature_failures)} "
            f"malformed_lines={verification.malformed_lines}"
        )

    # (ii) Causal quarantine — the ONLY effect that gives (i) any teeth. Calls
    # ONLY the public parties API; never edits parties.py, governance.py, or
    # the gate itself. Always "suspended" (reversible, human-clear-only) —
    # NEVER "killed".
    from .parties import set_party_status
    try:
        set_party_status(
            folder_context,
            actor,
            "suspended",
            reason=f"witness-escape audit_id={audit_id}",
            actor=_QUARANTINE_ACTOR,
            log_root=log_root,
        )
    except Exception as exc:  # noqa: BLE001 — surface, never swallow
        raise WitnessEscapeQuarantineError(
            f"witness-escape event {audit_id} for actor {actor!r} was "
            "recorded and the chain verifies, but quarantining the actor "
            f"failed: {type(exc).__name__}: {exc}. The event is permanent on "
            "the chain; the actor is NOT yet capped."
        ) from exc

    return {"audit_id": audit_id, "event": asdict(event)}


def recent_witness_escapes(
    folder_context: str | Path,
    actor: str,
    *,
    since: Optional[float] = None,
    log_root: Optional[str | Path] = None,
) -> list[dict[str, Any]]:
    """Read-only: witness-escape events recorded for ``actor`` in this folder's
    mutation log, at or after ``since`` (unix seconds; ``None`` = no lower
    bound — every recorded escape matches).

    Folder-scoped by construction — a ``MutationLog`` is one folder's chain,
    so an escape recorded in a different workspace is never read here — and
    actor-filtered by this function, so an escape recorded for a different
    actor in the SAME workspace never matches either.

    Each returned dict is the event's ``extra`` payload plus ``ts`` and
    ``audit_id``. Returns ``[]`` when nothing matches (including when the log
    is empty or does not yet exist) — that is the answer, not a failure.
    """
    log = MutationLog(folder_context, log_root=log_root)
    hits: list[dict[str, Any]] = []
    for evt in log.replay():
        if evt.actor != actor:
            continue
        extra = evt.extra or {}
        if extra.get("kind") != WITNESS_ESCAPE_KIND:
            continue
        if since is not None and evt.ts < since:
            continue
        hits.append({**extra, "ts": evt.ts, "audit_id": evt.audit_id})
    return hits


# ---------------------------------------------------------------------------
# CLI: python -m rvnd.witness_escape record --folder <ws> --actor <a>
#      --paths <p1,p2> [--since <ts>] [--log-root <dir>]
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m rvnd.witness_escape",
        description="Record a witness-escape event on a workspace's signed "
                     "mutation chain and quarantine the actor.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="record a witness-escape event and "
                                        "quarantine the actor")
    rec.add_argument("--folder", required=True, help="workspace folder (folder_context)")
    rec.add_argument("--actor", required=True, help="the actor whose run escaped")
    rec.add_argument("--paths", required=True,
                     help="comma-separated list of unauthorised paths")
    rec.add_argument("--since", type=float, default=None, dest="run_since",
                     help="unix timestamp the run started (optional)")
    rec.add_argument("--log-root", default=None, dest="log_root",
                     help="override the mutation-log root (optional)")

    args = parser.parse_args(argv)

    if args.command == "record":
        paths = [p.strip() for p in args.paths.split(",") if p.strip()]
        try:
            result = record_witness_escape(
                args.folder, paths, args.actor,
                run_since=args.run_since, log_root=args.log_root,
            )
        except WitnessEscapeError as exc:
            print(f"witness-escape record failed: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 — surface any unexpected failure, don't swallow
            print(f"witness-escape record failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
        print(result["audit_id"])
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - argparse.error() exits before this


if __name__ == "__main__":
    raise SystemExit(main())
