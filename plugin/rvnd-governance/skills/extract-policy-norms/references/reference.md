# Extract policy norms: RVND mapping

- Discover: `workspace_workflow(op="help")`.
- Primary operation: `workspace_workflow(op="policy_ingest")`.
- Inputs come from the live help result; do not hardcode undocumented parameters.
- Output is proposed governance material with grounding and residuals, never an authorization.
- If the operation is unavailable, the source is ungrounded, or identity is unresolved, stop.
- Continue with `compile-loomground-policy`; do not call `patch_apply` from this skill.

Shared contracts: `../../../references/catalogue.md`, `../../../references/grounding.md`, and
`../../../references/operation-protocol.md`.
