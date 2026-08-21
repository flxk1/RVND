---
name: rvnd-decide
description: Run the human decision in RVND in the right mode - ratify a determinate verdict (approve/deny) OR originate a residual choice among unranked alternatives (never approve/reject) - capturing the named approver and rationale a loosening needs and recording it to the signed chain. Drives RVND oversight; discrete lamps, no dials/scores; fail-closed. Triggers - "who signs off on this", "put this to oversight", "approve this", "originate the decision", "escalate for human decision", "does this need approval".
---

# rvnd-decide

The confirm step, in depth. When an action needs a person — because it loosens authority, raises a
grade, imports policy, or is a reserved act — this skill puts it to them cleanly and records what
they decide, so the approval is real, attributed, and signed.

The person decides; you record. You do not pre-select an outcome, nudge toward one, or treat
silence as a yes. The mode is the server's `AuthorizationDecision`, not your choice.

## Two modes — never collapsed

RVND distinguishes ratifying a determinate result from originating a residual one, and this skill
keeps them apart.

**Ratification** — the server reached a verdict; a person ratifies it. Present two discrete
outcomes, **approve / deny**. Whether an approval *counts* is the projection's call, not yours.
Driven by `workspace_workflow(op="approval_decide" | "approval_resolve")`.

**Residual-origination** — there is no determinate result; the policy is residual here, so a
competent person **originates** the choice. Present the server's two-or-more **real, unranked
alternatives**, with **no default**. This mode must **never** render as approve/reject — offering a
"yes/no" where the server offered alternatives is a misrepresentation.

## What it does

1. **Query** the decision: which mode? Is the item reserved for a person? The underlying verdict
   (`human` / `reserved` / `refused` / `prohibited`) is preserved, never flattened to "hold".
2. **Resolve** the acting person's identity. Unresolved → no decision (no-id wall).
3. **Present** the correct surface — approve/deny for ratification, unranked alternatives for
   origination.
4. **Require** a rationale for any approval that loosens. Inert without it.
5. **Record** through the server; it folds into the per-folder Ed25519-signed chain. A released
   action becomes a fresh activation with its own token and trace.

## The rules

- A task reserved for a person cannot run automatically. Surface it; do not route around it.
- A loosening needs a **named approver and a written rationale**. An agent cannot self-sign one.
- Preserve the underlying verdict; render the outcome as the person chose it, never paraphrased
  into something more permissive.

## More

- `references/reference.md` - the two modes, reserved acts, and rationale enforcement.
- `../../references/operation-protocol.md` - the AuthorizationDecision object and its two modes.
- `../../references/protocol.md` - the shared protocol, Sign routing, tightening vs loosening.
- `references/eval.json` - what it drives, guarantees, and review status.
