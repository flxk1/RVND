---
name: govern-an-action
description: "I'm about to do X — is it allowed, and put it on the record." Take a consequential agent action, check it against the active governance lane, and — when you mean to go through with it — propose, validate, preview, confirm, apply, and display the signed verdict. Operates the shared 5D+nD governance Subgraph and cascades local-first — the RVND engine decides precisely, signs, and enforces when present; otherwise the cloud LLM builds and enriches the same Subgraph in-grammar (degraded, advisory) until the engine ratifies. Has two modes — a read-only dry-run (what's the governed outcome, change nothing) and the full write cycle. Never computes a verdict itself; keeps unexpressible meaning residual; fail-closed. Triggers — "is this allowed under policy", "govern this action", "run this through RVND", "gate this against the lane", "what's the verdict for this action", "can the agent do this", "check this case against the lane".
---

# govern-an-action

The core loop: *"I'm about to do X — is it allowed, and put it on the record."* Take an action an
agent wants to perform, check it against the lane, and — if you go through with it — put it through
RVND properly, so the verdict is the server's and the outcome is exactly what the server decided —
never a request dressed up as a grant.

The server decides; you render. You do not compute allow, hold, or deny. You resolve the
principal, read the lane, propose the action, hand it to the server, and surface what comes back.

## Two modes — dry-run and the full cycle

This skill folds in what used to be a separate read-only step (`reason-governance-rules`).

- **Dry-run (read-only).** Ask the server for the governed outcome of a concrete action —
  `workspace_workflow(op="operate")` against the active lane — and render the discrete verdict,
  residual, and evidence. It **changes nothing**: no proposal, no write, no signature. Use it to
  answer *"is this allowed?"* before committing. Stop here when that is all the user wanted.
- **Full cycle (the write).** When the user means to go through with the action, continue into
  propose → validate → preview → confirm → apply. The server signs the applied change into the
  chain.

Reads grant nothing; the dry-run never becomes permission on its own.

## The cycle (never skip a step)

1. **Discover** the live RVND tools — do not hardcode names (`../../references/catalogue.md`).
2. **Query** the governed state: which policy governs this folder, the agent's lane, its
   autonomy ceiling, the applicable rules. Reads grant nothing. *(A dry-run runs `operate` here and
   stops — see above.)*
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

## Cascade & the shared graph

Every skill in this plugin operates the **same dimensioned `Subgraph`** and cascades **local-first**.
See `../../references/ingest-cascade.md` for the plane list, the ingester entry points, and the
honesty rules; this skill does not restate them.

- **Engine first, never the reverse.** When RVND is present and the folder is governed, the engine
  decides the verdict, signs the receipt, and enforces it — precise and live. The assistant does not
  answer first and check later.
- **LLM fallback, in-grammar.** When the engine is absent or cannot, the `loomground_ingest`
  ingesters lower the action into the shared Subgraph and the cloud LLM **enriches it in-grammar**
  (the bearer, the condition, the deadline) — never a new shape. A standalone build is **degraded /
  advisory**: no server verdict, no signing, no enforcement, and the skill says so until the engine
  ratifies.
- **Fail-closed.** If neither the engine nor the ingest plane can produce a well-formed Subgraph and
  verdict, the skill stops and reports a residual — it never fabricates a verdict.

## Two rules

**Never present requested state as granted.** A proposal is "requested / pending" until the server
applies it. A request for a higher grade than the lane allows is a denial, not a pending grant.
Missing scope, an unapproved footprint or connector, a policy change, or a grade increase all
produce a denial — surface it faithfully. In dry-run, a `human` or `reserved` outcome is an
escalation, never a permission.

**Never invent a construct to clear the residual.** Meaning belongs in a real Loomground construct
(`../../references/vocabulary.md`) or in the residual ledger, visibly unresolved. "Responsibly",
"appropriately", and the like cannot become a guard — a guard ranges only over kind/risk/party/tags
and never computes — so they stay residual until a person defines them. Ask; do not approximate.

## More

- `references/reference.md` - the two modes, the envelope, residual ledger, tightening vs loosening, identity.
- `../../references/ingest-cascade.md` - the cascade, the plane list, the ingester entry points.
- `../../references/grounding.md` - the six grounding links and the residual ledger.
- `../../references/operation-protocol.md` - the typed layer-2 objects and the executable chain.
- `../../references/vocabulary.md` - the real Loomground constructs (0.8.2).
- `../../references/protocol.md` - the shared eight-step protocol and doctrine.
- `../../references/catalogue.md` - verb-to-operation mapping and discovery.
- `manifest.yaml` - the constructs this skill may read and propose.
- `references/eval.json` - what it drives, guarantees, and review status.
