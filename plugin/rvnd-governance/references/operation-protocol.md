# The typed operation protocol (layer 2)

Loomground defines *meaning*; RVND *runs* it; between them sits a small typed
protocol that the host and RVND exchange. The canonical verbs (`query`, `propose`, `validate`,
`apply`, `decide`, `revoke`, …) are **host vocabulary** — they map to these typed objects and to
exact `workspace_*` MCP ops (`catalogue.md`). They are not Loomground grammar and are never added to
the language.

Four layers, kept explicit:

1. **Loomground** — governance meaning, graph structure, declarations, evaluation, and the canonical
   observation. (`vocabulary.md`, and the vendored `schemas/loomground/`.)
2. **This protocol** — the typed objects exchanged: observations, deltas, validation results,
   authorization decisions, commits, runtime actions. (`schemas/protocol/operation-protocol.schema.json`.)
3. **RVND** — resolves identities, evaluates and applies, enforces runtime controls, signs the record.
4. **Skills / UI** — compact verbs mapped to the protocol objects and exact RVND ops.

## The objects

- **GovernanceObservation** — the current projected graph state, as the official Loomground
  observation (`urn:loomground:0.7:observation`) plus folder/policy/lane context. Read via
  `workspace_workflow(op="governance_graph" | "governance_netlist")`.
- **GovernanceDelta** — one graph mutation: an `operation` (`add` / `remove` / `replace`) carrying
  one **fully typed** construct (a node, cord, reservation, prohibition, egress-obligation, redress,
  or grant), each with `additionalProperties:false` so `{"type":"reservation","invented":"x"}` fails.
  A host verb like `revoke` **resolves to** a delta (e.g. `{operation:"remove", construct:{type:"cord",
  cord_type:"authority", from:"actor:researcher", to:"gate:publish"}}`); `revoke` is not itself a
  construct.
- **ValidationResult** — RVND's verdict on a delta/patch: `well_formed`, `applyable`,
  `conformance_vector_set`, the `canonical_observation` (the official observation object, not a
  string), a `verdict_preview` from the five-verdict alphabet, `warnings`, and `unavailable_fields`.
  `well_formed:false` is never `applyable`. The host presents this; it does not compute it. Produced
  by `workspace_workflow(op="patch_validate")`.
- **AuthorizationDecision** — the human decision, in one of two modes that are never collapsed:
  *ratification* of a determinate result (`approve` / `deny`; whether it **counts** is the
  projection's call), or *residual-origination* of a residual decision (two or more real, unranked
  alternatives, no default, never rendered as approve/reject). Preserves the underlying verdict
  (`human` / `reserved` / `refused` / `prohibited`). Driven by
  `workspace_workflow(op="approval_request" | "approval_decide" | "approval_resolve")`.
- **CommitReceipt** — the signed outcome after apply: what happened, the op that ran, the rule, the
  signing key, the chain position, the resulting observation, and whether it verified. From
  `workspace_workflow(op="patch_apply")` + `workspace_audit(op="verify_chain")`.
- **RuntimeAction** — an RVND runtime action **outside** Loomground (`hold` / `suspend` /
  `seal-air-gap` / `transfer-custody`), flagged `outside_loomground:true`. It is not a construct and
  is never presented as a policy declaration. `transfer-custody` may only be called that when the
  server records the principals and a signed handoff.

## The executable chain (the minimum wired proof)

The proof of grounding is one delta completing end to end against the live server:

1. Discover — `workspace_workflow(op="help")`.
2. Resolve — folder, actor, agent, and governed-object identities (no-id wall).
3. Read — the current graph and lane (`governance_graph`, `governance_lane_list`).
4. Produce — one typed `GovernanceDelta` from a supported request.
5. Validate — `workspace_workflow(op="patch_validate")` → `ValidationResult`.
6. Present — the correct ratification or residual-origination surface.
7. Apply — only after authorization, via the exact op (`patch_apply`).
8. Verify — return and check the signed receipt (`workspace_audit(op="verify_chain")`).
9. Compare — the resulting canonical observation against the validated expected observation.

The linter and schemas cover shape (layers 1–2); runtime identity, validation, application,
receipt verification, and observation comparison are accepted only from the compatible live RVND
server. The skills must **request** each field from RVND and mark anything unavailable, never
invent a construct, validation, binding, evidence, or observation to fill the envelope.
