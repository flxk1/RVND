---
name: onboard-a-policy
description: Bring a written policy, regulation, or contract into RVND governance — lower it into the shared 5D+nD governance graph and hand it to sign-off for ratification. Does NOT reimplement lowering — it delegates to the free Loomground plane skills (loomground-ingest to drive the lowering, deontic for the O/P/F norms, loomground for the governance .lg patch + express/policy/host litmus) and assembles their output into one dimensioned Subgraph. Cascades local-first — the RVND engine lowers it precisely when present; otherwise it delegates to the plane skills and enriches the same Subgraph in-grammar (degraded, advisory) until the engine ratifies. Never applies or signs on its own; applying is a separate governed step. Triggers — "onboard this policy", "bring this regulation into governance", "turn this contract into rules", "ingest this policy", "make RVND enforce this".
---

# onboard-a-policy

The one action a user thinks of as *"make RVND govern by this."* It merges what used to be two
pipeline steps (extract + compile) into a single job and runs the flow internally.

It **produces a graph, it does not apply one.** Applying the validated graph to the signed record is
a separate governed step (`sign-off` ratifies; the engine applies). This skill stops at a
validated, dimensioned Subgraph plus its residuals.

## The object it builds

One **dimensioned `Subgraph`** — the shared 5D+nD object versum stores and RVND ingests. It is not
hand-rolled and it is not built by this skill's own code: the lowering belongs to the **free
Loomground plane skills**, and this skill **delegates to them** and assembles the result. See
`../../references/ingest-cascade.md` for the cascade and the honesty rules; this skill does not
restate them.

A policy graph draws on every plane at once: governance topology (`actor/gate/human/master` + cords),
the deontic modality (`O/P/F` norms with Hohfeldian incidents), legal anchors (source, precedence,
defeasibility), and — where the text carries them — factual and epistemic facets.

## Delegates to (consume, don't regrow)

This skill orchestrates; it never re-authors a plane's lowering. Each facet is produced by the plane's
own skill from the free `loomground` marketplace (Apache-2.0):

| facet | delegated skill | it owns |
|---|---|---|
| lowering / ingest | **`loomground-ingest`** | drive the network-free ingest plane → a dimensioned Subgraph |
| deontic | **`deontic`** (loomground-deontic) | transcribe each norm into a verified `DeonticFormula` (`O/P/F`, incidents) |
| governance | **`loomground`** (loomground-governance) | the governance `.lg` patch **and** the express / policy / host **litmus** |

`onboard-a-policy` (commercial `rvnd` tier) consuming these free plane skills is the layering working
as intended — never the reverse, and never a reimplementation of what they already do.

## The flow (cascade local-first)

1. **Scope the artifact.** Category (`policy` / `contract` / …), source, and the spans to preserve.
   Nothing is inferred that the text does not carry.
2. **Try the engine first.** If RVND is present and the folder is governed, lower through its own
   ingest → versum → solver: a precise, signable Subgraph with server-computed structure. Done.
3. **Else delegate to the plane skills.** Invoke `loomground-ingest` to lower the text, `deontic` for
   the norm facet, and `loomground` for the governance patch + the express/policy/host litmus. Then
   **enrich in-grammar** — correct the bearer, conditions, deadlines, and cross-references the
   deterministic pass could not resolve — producing valid plane objects (a well-formed
   `DeonticFormula`, a patch that validates against `schema('patch')`), never a shape outside the
   grammar. Assemble the facets into the one shared Subgraph.
4. **Report the graph + its residuals.** Show the governance nodes, the deontic norms, the legal
   anchors, and — explicitly — every span the lowering could not place (the `loomground` litmus's
   **host hand-offs** land here as first-class residuals, never invented constructs). Mark a
   delegated-only build **degraded / advisory** — no solver precision, no signing, until the engine ratifies.
5. **Hand off.** Route the validated Subgraph to `sign-off`; the engine applies on ratification. This
   skill applies nothing and signs nothing.

## Rules

- **Delegate, don't regrow.** The lowering belongs to the plane skills (`loomground-ingest`, `deontic`,
  `loomground`). This skill assembles and enriches their output; it never re-authors a plane's job. A
  meaning with no construct is a **residual**, surfaced, not faked.
- **Local-first.** The engine's lowering always wins when present; the plane-skill path is the fallback
  and says so.
- **Same object throughout.** Engine-built or delegated, the output is the one RVND-ingestible
  Subgraph — an engine that ingests the fallback graph later sees the same shape.
- **Never applies.** Onboarding ends at a validated graph. Applying/signing is `sign-off` + the engine.

## More

- `../../references/ingest-cascade.md` — the cascade, the plane list, and the plane-skill delegation.
- `../../references/protocol.md` — the shared protocol, identity resolution, Sign routing.
- `references/reference.md` — the flow in depth, the residual ledger, and the degraded-mode contract.
- `references/eval.json` — what it drives, guarantees, and review status.
