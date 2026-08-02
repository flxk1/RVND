---
name: extract-policy-norms
description: Extract grounded policy requirements through the RVND governance MCP server. Use when a regulation, contract, or policy must become reviewable governance input before validation or application. Never infer authority or compute a verdict in the host.
---

# Extract policy norms

Discover `workspace_workflow` with `op="help"`, then use the live `policy_ingest` contract to
submit the source text and folder context. Treat the server response as a proposal, not authority.

1. Resolve the folder and source identity.
2. Call `workspace_workflow(op="policy_ingest")` using only documented parameters.
3. Preserve source spans, residual meaning, and unavailable fields returned by the server.
4. Present extracted constructs for review; do not apply them.
5. Hand the proposed netlist or patch to `compile-loomground-policy` for validation.

Read `references/reference.md` for the operation mapping and failure rules. The host never invents
a norm, writes `.lg`, or converts unexpressible meaning into a nearby construct.
