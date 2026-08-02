# RVND host protocol — the shared safety cycle

Every skill in this plugin drives the same governance server through the same discipline.
This file is the contract; the individual skills are entry points into it. Read it once, then
each skill tells you which part of the cycle it owns.

RVND is a live, local-first governance MCP server (reeve + nD). It sits between an agent and the
things it acts on and decides what may happen. **The server decides; the host renders.** The host
never computes a verdict, never softens one, and never shows a requested state as if it were
granted.

## The eight-step cycle

Any consequential interaction with RVND runs this loop. Skipping a step is a policy violation,
not a shortcut.

1. **Discover** — read the live tool surface, do not memorise it. Ask the server what it exposes
   (see `catalogue.md`) and use the names it returns. If a capability you expect is absent, treat
   it as unavailable, not as something to emulate locally.
2. **Query** — read the current governed state before proposing anything: which policy governs
   this folder, the agent's lane, its autonomy ceiling, the applicable rules. Reads are read-only;
   they change nothing and grant nothing.
3. **Propose** — build a proposed change or action as an explicit envelope (see the `apps/`
   card specs). A proposal is a request. It is not in effect. State it as a request in every
   surface you render.
4. **Validate** — hand the envelope to the server for evaluation against the lane and the
   applicable rules. The server returns a verdict (allow / hold / deny) bound to the rule that
   produced it. The host does not pre-judge the outcome.
5. **Preview** — show what the change would do *before* it is applied: which controls move, whose
   oversight it touches, what tightens or loosens. For anything that tightens oversight, route the
   preview through the server's officer preview so the effect is the server's projection, not the
   host's guess.
6. **Confirm** — obtain the human confirmation the verdict requires. A change that widens
   authority, raises an autonomy grade, or imports policy cannot proceed on the agent's say-so.
   It needs a named approver and a rationale. No confirmation, no apply.
7. **Apply** — only now does the change take effect, through the server, with the approver and
   rationale attached. The server writes the outcome to the signed chain. The host applies
   nothing itself.
8. **Display** — render the result the server returned: the verdict, the rule it cites, the
   receipt. Render it as what it is. Do not upgrade a hold into an allow in the wording.

Reads (steps 1–2) are always safe. Writes (steps 3–7) are fail-closed: if any step is missing,
errors, or times out, the action does not happen.

## Canonical verbs → live operations

The skills speak in ten canonical verbs so the workflow reads the same across hosts. Each verb
maps onto real RVND operations; `catalogue.md` holds the current mapping and the exact op names.
Always resolve the verb to a live operation at discovery time — the mapping is a convenience, the
server's tool list is ground truth.

| Verb | Cycle step | Intent |
|------|-----------|--------|
| `query` | Query | Read governed state — policy, lane, rules, applicable controls. |
| `propose` | Propose | Draft a lane version, policy import, or action envelope. Not in effect. |
| `validate` | Validate | Server evaluates the envelope; returns a rule-bound verdict. |
| `apply` | Apply | Commit an approved change through the server, with approver + rationale. |
| `operate` | (governed run) | Run a live governed action, checked against the lane every time. |
| `decide` | Confirm | A person signs off: approve, hold, or deny an item put to oversight. |
| `hold` | Confirm | Freeze an action or reserve it for a person; a time-based or oversight stop. |
| `revoke` | Apply | Withdraw a grant or erase a record with a signed tombstone, not a silent delete. |
| `transfer` | Apply | Hand custody of a governed scope to another principal, recorded and signed. |
| `verify` | Display | Check the tamper-evident audit chain and confirm a receipt. |

## Never present requested state as granted

This is the rule the whole plugin exists to enforce.

- An agent may **request** its assigned autonomy grade or a lower one — never a higher one. A
  request for more is a denial, not a negotiation.
- A **proposal** is rendered as a proposal. "Requested", "pending", "proposed" — never "granted",
  "enabled", "in effect" — until the server has applied it and returned a receipt.
- A **verdict** is rendered as the server returned it. A `hold` is a hold. A `deny` is a deny.
  Do not paraphrase either into something more permissive.
- Missing scope, an unapproved action or footprint, a connector change, a policy change, or an
  attempted grade increase all **produce a denial**. The host's job is to surface that denial
  faithfully, not to route around it.

## Tightening vs loosening

The direction of a change decides how much ceremony it needs.

- **Tightening** (narrowing authority, lowering a ceiling, adding oversight, sealing a folder)
  is the safe direction. Preview it through the server's officer preview so the effect shown is
  the server's, then apply. It still records who tightened and why.
- **Loosening** (widening authority, raising a grade, adding a connector, opening a sealed
  folder, importing a more permissive policy) is the dangerous direction. It **always** requires a
  new versioned lane or policy import, a named approver, and a written rationale, and it is
  fail-closed until it has them. Never loosen implicitly, in passing, or by reusing a prior
  approval for a different scope.

## Sign routing

Signatures are how RVND stays tamper-evident, and they route by change type.

- Every applied decision is appended to the per-folder, **Ed25519-signed** hash chain. The host
  does not sign; it asks the server to record, and the server signs.
- Erasure is a **signed tombstone**, never a silent delete: it purges this folder's record and
  blocks re-ingestion, but it cannot recall copies that already left the boundary. Say so when you
  render a revoke.
- A **loosening** change routes to a named human approver whose approval is captured with the
  rationale and folds into the chain. An agent cannot self-sign a loosening.
- A **tightening** change and a routine governed action still record their actor, but do not need
  a separate human approver unless policy says so.
- When you render any signed outcome, attribute it: which key, which rule, which approver.
  Attributed, not asserted.

## Identity resolution — the no-id wall

Before anything is governed, the principal has to be resolved. RVND is fail-closed on identity.

- Resolve the agent, the folder scope, and the acting person to concrete identities the server
  recognises **before** step 3. An unresolved principal is a wall: the action stops there.
- Never invent, assume, or reuse an identity to get past the wall. "The agent" is not an
  identity; a registered agent with a lane is.
- A folder with no policy is ungoverned by default and therefore fail-closed for consequential
  acts — resolve or register it first, do not proceed as if silence meant permission.
- If the server cannot resolve a principal, surface that as the reason the action did not happen.
  Do not substitute a plausible identity to make the flow continue.

## House doctrine (from the server, non-negotiable)

- Server decides, client renders.
- Discrete lamps, no dials or scores — render a verdict as a state, not a number.
- Attributed, not asserted — every outcome carries its rule, key, and approver.
- Fail-closed — in doubt, stop rather than proceed or leak.
- No-id wall — no governed action without a resolved principal.
