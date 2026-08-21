# Acceptance corpus — what a grounded skill must be tested against

Each governance skill should carry a corpus covering these classes. The corpus is behavioural: it
runs the skill over the real MCP boundary and checks the proposal, the residual ledger, the
validation result, and the signed outcome — not the prose. Vectors reference the pinned vocabulary
(`vocabulary.md`) and the envelope schema (`schemas/proposal.schema.json`).

## The fourteen classes

1. **Phrase → construct mapping.** Each supported phrase maps to the correct real construct.
   "This agent may draft" → `actor` + `gate` + `authority`; "Maria must approve" →
   `reserve <kind> by <role>`; "Never use for training" → `prohibit <kind>`; "Two reviewers" →
   `quorum` (`2 of { … }`); "Before publishing, disclose AI use" → `egress-obligation`
   (`ai-interaction-disclosure`); "They may contest it" → `redress`; "The delegate acts for Alice"
   → `on-behalf-of`; "The agent can act at L2" → actor `grade L2`; "This checkpoint requires L3" →
   source-gate `grade L3`.
2. **Stable object resolution.** Names resolve to RVND/Versum identities, not display names.
3. **Ambiguous names.** An ambiguous name triggers a clarification, not a guess.
4. **Unsupported meaning stays residual.** "responsibly", "culturally appropriate" land in the
   residual ledger with a reason; no construct is invented.
5. **Invalid graphs fail closed.** An ill-formed graph (human as a cord endpoint, actor→master, a
   cyclic pipe, a guard over `id`/`grade`) is rejected at apply, and the skill withholds.
6. **No delegation amplification.** `on-behalf-of` never grants the delegate more than the
   delegator; an ungraded delegator caps the delegate at ungraded.
7. **Reservation and prohibition precedence.** A `prohibition` overrides any grant; the verdict
   join takes the most restrictive (`prohibited` > `reserved` > `refused` > `human` > `auto`).
8. **Grade handling.** An actor may act at its granted grade or lower; a source gate's required
   grade withholds to `human` when the grant is insufficient. Grade is never guarded.
9. **Quorum distinctness.** A quorum requires distinct parties; the same party twice does not
   satisfy `2 of { … }`.
10. **Source and policy provenance.** Each declaration carries its Versum span/claim and its legal
    grounding from `grounding.json`.
11. **Confirmation before mutations.** Any applying/loosening step requires the confirmation the
    verdict demands (named approver + rationale for a loosening).
12. **Success and refusal over the real MCP boundary.** Both a successful apply and a fail-closed
    refusal are exercised against the live server, not a stub.
13. **Exact result and signed receipt.** The applied outcome matches the proposal and returns a
    verifiable signed chain entry.
14. **Version reporting.** The proposal reports language, vocabulary, Solver, RVND-contract, policy
    and catalogue versions.

## The equivalence vector (headline)

Chat, CLI, sheet, and `.lg` inputs of the same meaning must produce **one** canonical observation,
one validation result, one impact preview, and one confirmation requirement. This vector doubles as
the check on the canonical-verb → live-op mapping in `catalogue.md`: divergence means the mapping or
the grounding is wrong.

Worked pair:

```
chat : "Maria must approve external publication."
cli  : rvnd propose policy --reserve external-publication --role accountable-publisher
sheet: boundary press-kit -> approval layer -> external-publication -> accountable-publisher
lg   : reserve external-publication by accountable-publisher
=> same canonical observation, same validation, same impact, same confirmation.required
```

## Status

These are the acceptance targets. The deterministic surface/proposal linter
(`skills/build-a-surface/scripts/lint_surface.py`) covers the offline shape checks (envelope
well-formedness, residual present, constructs restricted to the real vocabulary). The classes that
need a live RVND server (2, 5, 6, 7, 8, 9, 12, 13) run against the MCP boundary and are marked
Each skill's `eval.json` records its compatibility review status.
