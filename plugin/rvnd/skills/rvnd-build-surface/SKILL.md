---
name: rvnd-build-surface
description: Compose an RVND MCP App surface - pick the cards (context, proposal, patch, decision, receipt) and skills for a governed flow, and lint the composition so it cannot show a request as a grant. Deterministic local check; drives no server; enforces the plugin's card and composition schemas. Triggers - "build a governance surface", "compose RVND cards", "make an oversight cockpit", "lint this surface spec", "design the approval flow".
---

# rvnd-build-surface

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
2. **Compose** them with the skills that drive them (`rvnd-govern`, `rvnd-decide`, `rvnd-audit`,
   `rvnd-incident`) into a manifest.
3. **Lint** the manifest, each card, and any proposal envelope with `scripts/lint_surface.py`,
   which enforces the schemas in `../../schemas/` (including `proposal.schema.json`: residual
   present, constructs restricted to the real 0.8.2 vocabulary, version recorded).

## Run it

```
echo '{"name":"oversight-cockpit","skills":["rvnd-decide"],"cards":["context","decision"],"fail_closed":true}' | python3 scripts/lint_surface.py
```

Deterministic and offline; exits non-zero on any violation.

## The rules it enforces

- A surface that renders a **proposal** must also render **patch** and **receipt** — a request is
  never shown without its verdict and its outcome.
- Cards render discrete lamps and carry attribution: `forbids_scores` and `attributed` must be
  true. No dials, no scores.
- The proposal card's status vocabulary must exclude *granted / enabled / active / in-effect*.
- Every composition is `fail_closed` and drives the `rvnd` server.

## More

- `references/reference.md` - card roles, composition shape, and what the linter checks.
- `../../apps/` - the five card specs. `../../schemas/` - the schemas the linter enforces.
- `references/eval.json` - what it checks, determinism, and review status.
