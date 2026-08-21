---
name: rvnd-govern
description: Run a consequential agent action through RVND's governance cycle - query the lane, propose it as a typed Loomground envelope, let the server validate it, preview the effect, confirm, apply, display the verdict. Drives the RVND governance MCP server; never writes .lg or computes a verdict itself; keeps unexpressible meaning residual; fail-closed. For a read-only verdict without proposing a change, use reason-governance-rules. Triggers - "govern this action", "is this allowed under policy", "run this through RVND", "gate this against the lane", "can the agent do this".
---

# rvnd-govern

The core loop. Take an action an agent wants to perform and put it through RVND properly, so the
verdict is the server's and the outcome is exactly what the server decided — never a request
dressed up as a grant.

The server decides; you render. You do not compute allow, hold, or deny. You resolve the
principal, read the lane, propose the action, hand it to the server, and surface what comes back.

## The cycle (never skip a step)

1. **Discover** the live RVND tools — do not hardcode names (`references/catalogue.md`).
2. **Query** the governed state: which policy governs this folder, the agent's lane, its
   autonomy ceiling, the applicable rules. Reads grant nothing.
3. **Propose** the action as a typed envelope (`../../schemas/proposal.schema.json`): assemble the
   intent, scope, and runtime bindings, and ask RVND to generate the Loomground constructs. You do
   not write `.lg`. Anything the user means that no real construct expresses goes in the
   **residual** ledger, not into an invented rule. It is a request, not an effect.
4. **Validate**: hand the envelope to the server's action gate. It returns allow / hold / deny
   bound to a rule.
5. **Preview** what would change; route anything that tightens oversight through the officer
   preview.
6. **Confirm**: obtain the human sign-off the verdict requires. Loosening needs a named approver
   and a rationale.
7. **Apply** through the server, with approver and rationale attached; the server signs it into
   the chain.
8. **Display** the verdict as the server returned it.

Reads are safe. Writes are fail-closed: any missing, errored, or timed-out step means the action
does not happen.

## Two rules

**Never present requested state as granted.** A proposal is "requested / pending" until the server
applies it. A request for a higher grade than the lane allows is a denial, not a pending grant.
Missing scope, an unapproved footprint or connector, a policy change, or a grade increase all
produce a denial — surface it faithfully.

**Never invent a construct to clear the residual.** Meaning belongs in a real Loomground construct
(`../../references/vocabulary.md`) or in the residual ledger, visibly unresolved. "Responsibly",
"appropriately", and the like cannot become a guard — a guard ranges only over kind/risk/party/tags
and never computes — so they stay residual until a person defines them. Ask; do not approximate.

## More

- `references/reference.md` - the envelope, residual ledger, tightening vs loosening, identity.
- `../../references/grounding.md` - the six grounding links and the residual ledger.
- `../../references/operation-protocol.md` - the typed layer-2 objects and the executable chain.
- `../../references/vocabulary.md` - the real Loomground constructs (0.8.2).
- `../../references/protocol.md` - the shared eight-step protocol and doctrine.
- `../../references/catalogue.md` - verb-to-operation mapping and discovery.
- `manifest.yaml` - the constructs this skill may read and propose.
- `references/eval.json` - what it drives, guarantees, and review status.
