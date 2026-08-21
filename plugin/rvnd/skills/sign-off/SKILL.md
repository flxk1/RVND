---
name: sign-off
description: The human oversight decision — "what's waiting for my approval? Approve / hold / deny." When an action loosens authority, raises a grade, imports policy, or is a reserved act, this skill puts it to a person cleanly and records what they decide — the named approver and the rationale a loosening needs — into the per-folder signed chain. The terminal step of the govern / onboard / resolve flows: it reads the pending decisions the engine produced and ratifies them; it never pre-selects, nudges, or treats silence as a yes. Discrete lamps, no dials/scores; fail-closed. Triggers - "what's waiting for my approval", "who signs off on this", "put this to oversight", "approve this", "hold this", "escalate for human decision", "does this need approval".
---

# sign-off

The confirm step, in depth. When an action needs a person — because it loosens authority, raises a
grade, imports policy, or is a reserved act — this skill puts it to them cleanly and records what
they decide, so the approval is real, attributed, and signed.

The person decides; you record. You do not pre-select an outcome, nudge toward one, or treat
silence as a yes. The mode is the server's `AuthorizationDecision`, not your choice.

## Cascade & the shared graph

sign-off is the **human terminal** of the shared local-first flow — it does not lower text or invent
a format. It **reads pending decisions and approvals the engine produced** off the same dimensioned
`Subgraph` that `govern-an-action`, `onboard-a-policy`, and `resolve-a-conflict` route into it, and
records the person's decision back onto the signed chain. See `../../references/ingest-cascade.md`
for the one-graph architecture and the plane list.

Local-first, always: when the RVND engine is present the pending item is a **precise, server-computed
verdict** and the recorded decision **signs into the per-folder Ed25519 chain** — enforced. When the
engine is absent the upstream item is only the **coarse, advisory** graph the cloud LLM enriched
in-grammar; sign-off then captures the human decision but marks it **degraded** — unsigned and
unenforced until the engine returns and ratifies. Engine first, never the reverse.

## Two modes — never collapsed

RVND distinguishes ratifying a determinate result from originating a residual one, and this skill
keeps them apart.

**Ratification** — the server reached a verdict; a person ratifies it. Present two discrete
outcomes, **approve / deny** (a **hold** parks it for a person without loosening). Whether an
approval *counts* is the projection's call, not yours. Driven by
`workspace_workflow(op="approval_decide" | "approval_resolve")`.

**Residual-origination** — there is no determinate result; the policy is residual here, so a
competent person **originates** the choice. Present the server's two-or-more **real, unranked
alternatives**, with **no default**. This mode must **never** render as approve/reject — offering a
"yes/no" where the server offered alternatives is a misrepresentation.

## What it does

1. **Query** the decision: which mode? Is the item reserved for a person? The underlying verdict
   (`human` / `reserved` / `refused` / `prohibited`) is preserved, never flattened to "hold".
2. **Resolve** the acting person's identity. Unresolved → no decision (no-id wall).
3. **Present** the correct surface — approve/hold/deny for ratification, unranked alternatives for
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

- `../../references/ingest-cascade.md` - the one graph, the plane list, and the local-first cascade.
- `references/reference.md` - the two modes, reserved acts, and rationale enforcement.
- `../../references/operation-protocol.md` - the AuthorizationDecision object and its two modes.
- `../../references/protocol.md` - the shared protocol, Sign routing, tightening vs loosening.
- `references/eval.json` - what it drives, guarantees, and review status.
