---
name: build-a-surface
description: Assemble a governed app screen and lint it — compose the RVND MCP App surface (the five cards: context, proposal, patch, decision, receipt) and the skills that drive them, then lint the composition so a built surface cannot show a request as a grant. Deterministic local check; drives no server; enforces the plugin's card and composition schemas. This is the standalone skill — engine-independent by design, a pure local linter over the plugin's own card specs and schemas, while the surface it builds renders the shared governance graph and verdicts the other skills produce. Triggers — "build a governance surface", "compose RVND cards", "make an oversight cockpit", "lint this surface spec", "design the approval flow".
---

# build-a-surface

The design step. Assemble the surface a person or agent sees when a governed flow runs — which
cards render, which skills drive them — and check it against the plugin's schemas so the built
surface cannot lie about state.

This skill does not drive the RVND server. It is a local, deterministic authoring and linting
tool that keeps a surface honest before it is wired up.

## What it does

1. **Choose** the cards for the flow from the five specs in `../../apps/` — context (query),
   proposal (propose), patch (validate/preview), decision (confirm), receipt (display). Which cards
   a surface needs follows from the construct types present: an authority editor when `authority` or
   `grade` is present, a decision card when a `reservation` or `quorum` exists, a duty panel when an
   `egress-obligation` is attached, a redress timeline when `redress` is declared, and a **residual
   panel** whenever a proposal carries residual. The layout is an RVND presentation artifact that
   references Loomground object identities — it is not itself Loomground.
2. **Compose** them with the skills that drive them (`govern-an-action`, `sign-off`, `verify-a-receipt`,
   `revoke-or-erase`) into a manifest.
3. **Lint** the manifest, each card, and any proposal envelope with `scripts/lint_surface.py`,
   which enforces the schemas in `../../schemas/` (including `proposal.schema.json`: residual
   present, constructs restricted to the real 0.8.2 vocabulary, version recorded).

## Run it

```
echo '{"name":"oversight-cockpit","skills":["sign-off"],"cards":["context","decision"],"fail_closed":true}' | python3 scripts/lint_surface.py
```

Deterministic and offline; exits non-zero on any violation.

## The rules it enforces

- A surface that renders a **proposal** must also render **patch** and **receipt** — a request is
  never shown without its verdict and its outcome.
- Cards render discrete lamps and carry attribution: `forbids_scores` and `attributed` must be
  true. No dials, no scores.
- The proposal card's status vocabulary must exclude *granted / enabled / active / in-effect*.
- Every composition is `fail_closed` and drives the `rvnd` server.

## Cascade & the shared graph

The other skills in this plugin operate one dimensioned `Subgraph` via `loomground_ingest` and
cascade **local-first** — engine first, cloud-LLM fallback (degraded), never the reverse. See
`../../references/ingest-cascade.md` for that shared architecture.

**This skill is the standalone exception: it is engine-independent by design.** It drives no server
and calls no ingest plane — it is a deterministic local linter over the plugin's own card specs
(`../../apps/`) and schemas (`../../schemas/`). It is the one skill here that needs no cascade,
because it computes no verdict and lowers no text; it only checks that a composition is honest
before it ships. What it builds, however, is exactly the surface that **renders** the shared
Subgraph and the server-computed verdicts the cascading skills (`govern-an-action`, `sign-off`,
`verify-a-receipt`, `revoke-or-erase`) produce — so the graph they build stays the graph the screen
shows, and a request can never be displayed as a grant.

## More

- `references/reference.md` - card roles, composition shape, and what the linter checks.
- `../../apps/` - the five card specs. `../../schemas/` - the schemas the linter enforces.
- `../../references/ingest-cascade.md` - the shared cascade the other skills follow (this one is engine-independent).
- `references/eval.json` - what it checks, determinism, and review status.
