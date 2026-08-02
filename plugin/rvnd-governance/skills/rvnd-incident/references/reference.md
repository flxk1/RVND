# rvnd-incident - reference

## What it drives

The RVND control surfaces for revoking authority and recording erasure. This skill sequences only
the exact operations declared in its manifest and records the outcome; it holds no enforcement
logic of its own and fabricates no authority to act.

## The direction rule governs everything here

An incident is urgent, but urgency does not reverse the safety gradient:

- **Tightening is the safe direction and can be immediate.** Revoking a grant or narrowing a lane
  records who did it and why.
- **Loosening back is still a loosening.** Restoring revoked authority needs a new versioned lane,
  a named approver, and a rationale, and is fail-closed until it has them.

## The actions

**Revoke.** Withdraw a grant, or erase a record. Erasure is a **signed tombstone**: it purges this
folder's record and blocks re-ingestion. It is not a silent delete, and it **cannot recall copies
that already left the boundary** — render that limit every time, so no one over-relies on a
revoke. To narrow an agent going forward, supersede its lane with a narrower version.

## Identity, even in an incident

Resolve the principal and the folder scope before acting. The no-id wall does not lift under
pressure: an unresolved target means the action stops, not that you pick a plausible one to move
faster.

## Guardrails

- Tightening is immediate; restoring authority needs a named approver and rationale.
- Revocation is a signed tombstone with honest limits — never rendered as a total recall.
- Fail-closed throughout: unreachable server or unresolved principal → the action does not happen.

## Pairing

Handles what `rvnd-audit` surfaces when a chain or an action looks wrong, and reverses or contains
what `rvnd-govern` and `rvnd-decide` put in place. Restoring normal operation afterwards routes
back through `rvnd-govern` as an ordinary approved change.
