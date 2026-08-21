# Decision card

Renders the human decision step. RVND draws a hard line that this card must preserve: **ratifying a
determinate result is not the same act as originating a residual choice.** The card therefore has
two modes, and it never collapses them into a single approve/hold/deny control.

The mode is set by the server's `AuthorizationDecision` (`schemas/protocol/`), not by the host.

## Mode A — ratification (a determinate result)

The server already reached a verdict; a person ratifies it. Here `approve` / `deny` is the honest
control.

- Shows the determinate result and the rule that produced it (the underlying verdict — `human`,
  `reserved`, `refused`, or `prohibited` — is shown, not flattened to "hold").
- Two discrete outcomes: **approve** / **deny**. No slider, no score.
- Whether an approval **counts** is the projection's call, not the card's — the card records the
  decision and lets the server decide its effect.
- A loosening approval requires a named approver and a rationale (inert without it).
- Driven by `workspace_workflow(op="approval_decide" | "approval_resolve")`.

## Mode B — residual-origination (a residual choice)

There is no determinate result to ratify — the policy is residual here, and a competent person must
**originate** the choice. This mode must **never** render as approve/reject.

- Shows two or more **real, unranked alternatives**, with **no default** and no pre-selection.
- The person originates a choice among them; the card does not frame one as the "yes".
- The alternatives come from the server's `AuthorizationDecision.residual_origination.alternatives`
  (minimum two); the card never invents an option.
- Recording the choice may open a fresh activation with its own token and trace — a released action
  is a new activation, not a continuation of the withheld one.

## Rendering rules (both modes)

- The person decides; the card records. It never pre-selects an outcome or nudges toward one.
- Discrete controls only — no dials, no scores.
- Preserve the underlying verdict state (`human` / `reserved` / `refused` / `prohibited`); do not
  collapse them into "hold" or "deny".
- If the acting person's identity is unresolved, no decision can be taken — no-id wall.
- A `decision_vocabulary` declared for a residual-origination card must exclude *approve* / *deny* /
  *reject* — the linter enforces this.
