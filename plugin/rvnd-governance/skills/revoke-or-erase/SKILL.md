---
name: rvnd-incident
description: Respond to a governance incident in RVND by revoking authority or recording erasure through exact live operations and the signed chain. Drives RVND; the safe direction (tightening) is immediate, loosening back needs approval; fail-closed. Triggers - "revoke that grant", "remove this agent's authority", "erase this governed record", "something is wrong stop its authority".
---

# rvnd-incident

The response path for when something is wrong: revoke authority or erase a governed record through
the exact live RVND operations, in the safe direction and on the record.

Tightening is the safe direction and can be immediate. Restoring a revoked
grant is a normal loosening: it needs a named approver and a rationale.
Incidents do not get a shortcut around that.

When the incident is a policy change — removing an authority cord, adding a
`prohibition`, lowering a `grade` — does it become a Loomground proposal that flows through the
normal propose → validate → confirm → apply cycle.

## What it does

- **Revoke** — withdraw a grant. Erasure is a **signed tombstone**, not a silent delete: it purges
  this folder's record and blocks re-ingestion, and it cannot recall copies that already left the
  boundary. Say so when you render it.
Every incident action routes through the server into the per-folder Ed25519-signed chain.

## The rules

- Tightening now, loosening later with approval.
- Revocation is a signed tombstone with honest limits — it cannot recall what already left.
- Resolve the principal and the scope before acting — no-id wall, even in an incident.

## More

- `references/reference.md` - each action, its direction, and what it can and cannot undo.
- `../../references/protocol.md` - the shared protocol, Sign routing, tightening vs loosening.
- `../../references/vocabulary.md` - which incident actions are runtime vs a real construct.
- `manifest.yaml` - runtime actions (outside Loomground) vs the constructs it may propose.
- `references/eval.json` - what it drives, guarantees, and review status.
