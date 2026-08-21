# onboard-a-policy — reference

## What it drives

The loomground **ingest plane** and, when present, the RVND ingest/validate path. It turns a written
policy, regulation, or contract into the shared dimensioned `Subgraph` and stops there — a validated
graph plus its residuals, handed to `sign-off`. It applies nothing and signs nothing.

This skill is the **reference implementation** of the ingest-cascade architecture
(`../../references/ingest-cascade.md`); the other user-action skills follow the same shape.

## Why one skill

"Bring this policy into governance" is a single user goal. It used to be two invokable steps —
`extract-policy-norms` then `compile-loomground-policy` — which is pipeline plumbing exposed as
product. Here the extract → compile → validate flow runs *inside* one skill; the user names the goal,
not the steps.

## The flow in depth

1. **Scope.** Fix the category (`policy` by default), the source, and the spans to preserve. Nothing
   is inferred that the text does not carry.
2. **Lower — engine first.** If RVND is present and the folder is governed, lower through its own
   ingest → versum → solver. The result is a precise, signable Subgraph; skip to *report*.
3. **Lower — fallback.** Otherwise run the ingest-plane ingesters (`GovernanceIngester`,
   `DeonticIngester`, and the legal / factual / epistemic grammars). This is deterministic and
   **coarse** — it will mis-resolve bearers, miss conditions, drop edges.
4. **Enrich, in-grammar.** The cloud LLM corrects the coarse graph — the bearer, the conditions, the
   deadlines, the legal cross-references — producing *valid plane objects* (a well-formed
   `DeonticFormula`, a governance patch that validates against `schema('patch')`). It never introduces
   a shape outside the grammar, so an engine that ingests this graph later sees the same object.
5. **Report + residual ledger.** Present the governance nodes, the deontic norms (`O/P/F` with their
   incidents), the legal anchors, and — explicitly — every span the lowering could not place. A
   residual is a first-class output, never silently dropped and never back-filled with an invented
   construct.
6. **Hand off.** Route the validated Subgraph to `sign-off`; the engine applies on ratification.

## The degraded-mode contract

A standalone (LLM-only) build is **advisory**:
- no solver/versum precision — the structure is the ingesters' plus the LLM's best in-grammar reading;
- no signature — nothing is on the signed chain until the engine ratifies;
- no enforcement — the graph describes governance; it does not yet operate it.

Say all three plainly whenever the engine was not in the loop. The graph is real and RVND-ingestible;
its authority is not, until the engine and a person ratify it.

## Guardrails

- **Consume, don't regrow** — the lowering belongs to the planes; a meaning with no construct is a
  residual.
- **Local-first** — the engine's lowering wins when present; the LLM path is the labelled fallback,
  never the default.
- **Never applies** — onboarding ends at a validated graph. Applying and signing are `sign-off` plus
  the engine.

## Pairing

Feeds `sign-off` (ratification) and, through it, the engine's apply. `resolve-a-conflict` handles the
case where the new policy's norms clash with existing ones. `audit-the-ai` later shows the applied
policy as part of the governance board.
