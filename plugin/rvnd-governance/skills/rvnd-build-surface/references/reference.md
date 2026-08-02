# rvnd-build-surface - reference

## What it is

A local authoring and linting tool for RVND MCP App surfaces. It composes the plugin's cards and
skills into a manifest and validates that manifest against the plugin's schemas. It drives no
server and computes no verdict — its whole job is to stop a dishonest surface from being built.

`scripts/lint_surface.py` is deterministic and offline. It reads one JSON object (stdin, or a file
path; `-` also means stdin) and validates it. It uses the optional `jsonschema` package for full
structural checks when present, and always runs the plain-Python invariant checks so it fails
closed even without that dependency.

## The five cards

Each card owns one cycle step and renders server state, never a host verdict (full specs in
`../../apps/`):

- **context** (query) — the current governed state: policy, lane, ceiling, applicable rules.
- **proposal** (propose) — a requested change, rendered as a request. Status words limited to
  requested / pending / proposed.
- **patch** (validate + preview) — the server's verdict and the officer preview of what would
  move.
- **decision** (confirm) — the human approve / hold / deny, with the approver and rationale.
- **receipt** (display) — the applied outcome, verified against the signed chain, attributed.

## Composition shape

A composition manifest (see `../../schemas/composition.schema.json`) names the surface, the skills
it composes, the cards it renders in cycle order, and asserts `fail_closed`. `human_confirmation`
is true when the surface can loosen and therefore must route a named approver.

## What the linter enforces

The checks encode the doctrine so a surface cannot be shipped that shows a request as a grant:

- **Write-path completeness.** A composition that renders `proposal` must also render `patch` and
  `receipt`. You cannot show a request without showing its verdict and its outcome.
- **No scores.** `forbids_scores` and `attributed` must be true wherever the card schema requires
  them. Verdicts are discrete lamps; every outcome carries its attribution.
- **Honest status words.** The proposal card's `status_vocabulary` must exclude *granted*,
  *enabled*, *active*, and *in-effect*.
- **Fail-closed and correct server.** Every composition is `fail_closed` and drives the
  `rvnd-governance` server.

Any violation exits non-zero with the reason. A surface that does not lint does not ship.

## Guardrails

- Deterministic and offline — no network, no server, same input gives the same result.
- Enforces the schemas; does not invent new rules at run time.
- Fails closed on malformed input, an unknown shape, or any invariant breach.

## Pairing

Composes the cards that `rvnd-govern`, `rvnd-decide`, `rvnd-audit`, and `rvnd-incident` drive. It
is the one skill here that does not touch the server — it makes sure the surfaces that do are built
honestly.
