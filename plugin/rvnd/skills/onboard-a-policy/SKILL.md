---
name: onboard-a-policy
description: Bring a written policy, regulation, or contract into RVND governance — lower it into the shared 5D+nD governance graph and hand it to sign-off for ratification. Consumes the loomground ingest plane (governance + deontic + legal + norm + factual + epistemic) to produce one dimensioned Subgraph, never a hand-built format. Cascades local-first — the RVND engine lowers it precisely when present; otherwise the cloud LLM builds and enriches the same Subgraph in-grammar (degraded, advisory) until the engine ratifies. Never applies or signs on its own; applying is a separate governed step. Triggers — "onboard this policy", "bring this regulation into governance", "turn this contract into rules", "ingest this policy", "make RVND enforce this".
---

# onboard-a-policy

The one action a user thinks of as *"make RVND govern by this."* It merges what used to be two
pipeline steps (extract + compile) into a single job and runs the flow internally.

It **produces a graph, it does not apply one.** Applying the validated graph to the signed record is
a separate governed step (`sign-off` ratifies; the engine applies). This skill stops at a
validated, dimensioned Subgraph plus its residuals.

## The object it builds

One **dimensioned `Subgraph`** — the shared 5D+nD object versum stores and RVND ingests. Built by
**consuming the `loomground_ingest` plane**, never hand-rolled. See `../../references/ingest-cascade.md`
for the plane list, the ingester entry points, and the honesty rules; this skill does not restate them.

A policy graph draws on all the planes at once: governance topology (`actor/gate/human/master` +
cords), the deontic modality (`O/P/F` norms with Hohfeldian incidents), legal anchors (source,
precedence, defeasibility), and — where the text carries them — factual and epistemic facets.

## The flow (cascade local-first)

1. **Scope the artifact.** Category (`policy` / `contract` / …), source, and the spans to preserve.
2. **Try the engine first.** If RVND is present and the folder is governed, lower through its own
   ingest → versum → solver: a precise, signable Subgraph with server-computed structure. Done.
3. **Fall back only if the engine is absent or cannot.** Run the ingest plane's ingesters
   (`GovernanceIngester`, `DeonticIngester`, and the legal/factual/epistemic grammars) to lower the
   text into a coarse Subgraph, then **enrich it in-grammar** — correct the bearer, the conditions,
   the deadlines, the cross-references the deterministic parser could not resolve — producing valid
   plane objects (e.g. a well-formed `DeonticFormula`), never a new shape.
4. **Report the graph + its residuals.** Show the nodes, the deontic norms, the legal anchors, and
   every span the lowering could not place (a visible residual, never an invented construct). Mark a
   standalone build **degraded / advisory** — no solver precision, no signing, until the engine ratifies.
5. **Hand off.** Route the validated Subgraph to `sign-off`; the engine applies on ratification. This
   skill applies nothing and signs nothing.

## Rules

- **Consume, don't regrow.** The graph is built by the ingest plane and the governance grammar, not
  by this skill inventing constructs. A meaning that has no construct is a **residual**, surfaced, not
  faked.
- **Local-first.** The engine's lowering always wins when present; the LLM path is the fallback and
  says so.
- **Same object throughout.** Standalone or engine-built, the output is the one RVND-ingestible
  Subgraph — an engine that ingests the fallback graph later sees the same shape.
- **Never applies.** Onboarding ends at a validated graph. Applying/signing is `sign-off` + the engine.

## More

- `../../references/ingest-cascade.md` — the cascade, the plane list, the ingester entry points.
- `../../references/protocol.md` — the shared protocol, identity resolution, Sign routing.
- `references/reference.md` — the flow in depth, the residual ledger, and the degraded-mode contract.
- `references/eval.json` — what it drives, guarantees, and review status.
