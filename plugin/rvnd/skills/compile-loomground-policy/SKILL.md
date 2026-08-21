---
name: compile-loomground-policy
description: Validate extracted policy material as a typed RVND governance patch - read the current governance_netlist, assemble a GovernanceDelta from grounded ingestion only, and patch_validate it against the live graph before human ratification. The compile step after extract-policy-norms; hands valid output to rvnd-decide. Never writes grammar or applies a patch in the host. Triggers - "validate this policy", "compile the extracted policy", "check these constructs against the graph", "is this policy patch well-formed".
---

# Compile a Loomground policy

Compilation means server-side validation of a typed governance delta, not host-side `.lg` writing.

1. Discover the current `workspace_workflow` operations.
2. Read `governance_netlist` for the current canonical state.
3. Assemble a `GovernanceDelta` only from grounded ingestion output.
4. Call `patch_validate` with the documented folder context and patch/netlist parameter.
5. Present the returned validation and canonical observation as a candidate policy.
6. Hand valid output to `rvnd-decide`; only `rvnd-govern` may apply after authorization.

Read `references/reference.md`. Missing operations, residual meaning, or a non-well-formed result
block the handoff.
