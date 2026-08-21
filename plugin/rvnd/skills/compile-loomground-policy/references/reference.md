# Compile policy: RVND mapping

- Read current state with `workspace_workflow(op="governance_netlist")`.
- Validate with `workspace_workflow(op="patch_validate")`.
- Preserve the server's verdict, residual, unavailable fields, and canonical observation.
- A valid result remains a candidate until the correct decision surface authorizes application.
- Never call `patch_apply` from this skill.

Shared contracts: `../../../references/catalogue.md`, `../../../references/protocol.md`, and
`../../../schemas/protocol/operation-protocol.schema.json`.
