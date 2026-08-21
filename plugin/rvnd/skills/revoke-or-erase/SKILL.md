---
name: revoke-or-erase
description: Pull an authority you granted, or erase a person from the governed record, through the exact live RVND operations on the signed chain. The safe direction — tightening (revoke) — is immediate; restoring authority is a loosening that needs a named approver and rationale. Erasure is a signed tombstone, not a silent delete: it purges this folder's record and blocks re-ingestion, and it cannot recall copies already past the boundary. Cascades local-first — the RVND engine performs the write and signs it; with no engine the skill fails closed and never fakes a revocation. Triggers - "revoke that grant", "pull that authority", "remove this agent's authority", "erase this person from the record", "something is wrong stop its authority".
---

# revoke-or-erase

The action a user reaches for when something is wrong and they think *"pull that authority"* or
*"erase this person from the record."* It revokes a grant or records an erasure through the exact
live RVND operations, in the safe direction and on the signed chain.

Tightening is the safe direction and can be immediate. Restoring a revoked grant is a normal
loosening: it needs a named approver and a rationale. An incident does not get a shortcut around
that.

When the incident is a policy change — removing an authority cord, adding a `prohibition`, lowering
a `grade` — it becomes a Loomground proposal that flows through the normal propose → validate →
confirm → apply cycle.

## What it does

- **Revoke** — withdraw a grant, in the safe direction, immediately.
- **Erase** — record erasure as a **signed tombstone**, not a silent delete: it purges this folder's
  record and blocks re-ingestion, and it **cannot recall copies that already left the boundary**. Say
  so when you render it.

Every action routes through the server into the per-folder Ed25519-signed chain.

## Cascade & the shared graph

These are **governed writes on the engine's signed chain**, not advisory reads. The cascade is
local-first — see `../../references/ingest-cascade.md` for the plane list and honesty rules; this
skill does not restate them.

- **Engine first.** RVND performs the revoke or erasure through its own operations and signs it into
  the per-folder chain. That is the only path that actually revokes or erases.
- **No engine → cannot act.** Unlike a read-only skill, there is no LLM fallback here: a revocation
  is a state change on the signed chain. If the engine is absent or unreachable, the skill **fails
  closed** and reports a residual. The cloud LLM never fakes a revocation or a tombstone.
- **Same object.** A revocation or a prohibition is a construct on the shared dimensioned Subgraph;
  the skill proposes it in-grammar and lets the engine apply it, never a hand-built shape.

## The rules

- Tightening now, loosening later with approval.
- Revocation and erasure are signed tombstones with honest limits — they cannot recall what already
  left the boundary.
- Resolve the principal and the scope before acting — no-id wall, even in an incident.
- Fail-closed: no engine, unreachable server, or unresolved principal → the action does not happen.

## More

- `references/reference.md` - each action, its direction, and what it can and cannot undo.
- `../../references/ingest-cascade.md` - the cascade, the plane list, the fail-closed rule.
- `../../references/protocol.md` - the shared protocol, Sign routing, tightening vs loosening.
- `../../references/vocabulary.md` - which incident actions are runtime vs a real construct.
- `manifest.yaml` - runtime actions (outside Loomground) vs the constructs it may propose.
- `references/eval.json` - what it drives, guarantees, and review status.
