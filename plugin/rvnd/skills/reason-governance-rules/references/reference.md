# Reason over rules: RVND mapping

- Discover with `workspace_workflow(op="help")`.
- Read lanes with `governance_lane_list` and policy state with `governance_graph`.
- Evaluate the governed action with `workspace_workflow(op="operate")`.
- Verify a returned receipt with `workspace_audit(op="verify_chain")`.
- Preserve RVND's five-verdict vocabulary; do not translate uncertainty into permission.
- If identity, lane, evidence, or the operation is unavailable, fail closed.

Shared contracts: `../../../references/catalogue.md`, `../../../references/protocol.md`, and
`../../../references/operation-protocol.md`.
