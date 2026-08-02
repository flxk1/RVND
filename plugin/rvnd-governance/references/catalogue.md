# RVND catalogue — the real MCP surface and the verb mapping

The skills speak in compact host verbs (see `protocol.md`). This file maps those verbs onto the
operations RVND exposes in the declared compatible release range. **Discovery
over memorisation still holds:** every tool is self-documenting via `op="help"`, and that live list
is ground truth — this table is a pinned convenience that can drift with the server.

## The surface: a family of `workspace_*` tools, dispatched by `op`

RVND does not expose ten verb-named tools. It exposes a family of `workspace_*` tools, each a facade
dispatched by an `op` argument and self-documenting via `op="help"` (also `"ops"` / `"catalogue"`),
which returns each op with its required params. The governance workhorse is **`workspace_workflow`**.

Discover first, every session:

```
workspace_workflow(op="help")     # lists the governance/lifecycle ops + required params
workspace_audit(op="help")        # audit-chain ops
workspace_lock(op="help")         # privacy-lock / egress ops
```

If a verb below has no exact op on the live server, treat it as **unavailable** and fail closed —
do not substitute an inferred op.

## Verb → tool + op

**query** — read governed state (read-only):
- `workspace_workflow(op="governance_graph")` — the policy graph.
- `workspace_workflow(op="governance_netlist")` — the graph as a Loomground netlist/patch.
- `workspace_workflow(op="governance_query")`, `op="coverage_matrix"`, `op="loop_graph"`.
- `workspace_workflow(op="governance_lane_list")` — the latest lane per agent.
- `workspace_grounder(op="provenance.trace" | "claim.status" | "bibliography")` — evidence.

**propose** — host-side assembly of a `GovernanceDelta` / patch (no single server op). Read the
current netlist with `governance_netlist`, build the typed delta, then validate.

**validate** — `workspace_workflow(op="patch_validate")`. Required `folder_context`; optional
`netlist`, `patch`. Returns the validation result; the host presents it, never computes it.

**apply** — `workspace_workflow(op="patch_apply")` for a graph mutation; `op="governance_lane_register"`
to register/version a lane (widening needs a named approver + rationale); `op="policy_ingest"` for a
policy import (human-confirmed before it applies).

**operate** — `workspace_workflow(op="operate")` — a governed live action, checked against the lane.

**decide** — the human decision path:
- `workspace_workflow(op="approval_request")` — open an approval under a control form (timeout = DENY).
- `workspace_workflow(op="approval_decide")` — approve | deny. **Whether it COUNTS is the
  projection's call**, which is why the decision surface must distinguish ratification from residual
  origination (see `protocol.md` and the decision card).
- `op="approval_resolve"`, `op="approval_delegate"`, `op="approval_list"`.

**hold** — NOT one op or one state. It can be the verdict `human` or `reserved` from a
`patch_validate`/`operate`, a pending quorum, an unelapsed `temporal` window, an approval timeout, or
a runtime containment. Preserve and render the underlying state; do not flatten it to a single "hold".

**revoke** — several distinct contracts, never one:
- `workspace_workflow(op="authority_revoke")` — remove an authority grant.
- `op="tool_revoke"`, `op="group_revoke"` — tool/group governance revocation.
- connector revocation via the connector ops (`connector_register`/`connector_list`).
- **erasure** via `workspace_erase(op="request" | "status" | "subject" | "sweep")` — a signed
  tombstone; it cannot recall what already left the boundary.
- **adding a prohibition** is not a revoke — it is a `GovernanceDelta` applied via `patch_apply`.

**transfer** — no verified single op records principals plus a signed custody handoff. Session
export/import is movement of a bundle, **not** a custody transfer. Mark `transfer` **unavailable**
until the server confirms an op that records the principals and signs the handoff.

**verify** — `workspace_audit(op="verify_chain")`, `op="get_event"`, `op="discipline")`.

## Other exposed tools (for reads and adjacent needs)

- `workspace_lock` — `classify`, `egress_check`, `ingress_check`, `audit_query`, `threshold_get`
  (Privacy Lock / egress; this is also the read-only gateway profile).
- `workspace_model` — `complete`, `classify`, `list`, `cascade`, `status` (local model availability).
- `workspace_contract` — `review`, `list_reviews`, `request_approval`, `record_approval`,
  `list_approvals`.
- `workspace_audit` — `verify_chain`, `get_event`, `discipline`.

## Two surfaces — do not confuse them

- The **full local MCP server** (`mcp_server`) exposes `workspace_workflow` and the mutating tools;
  this is what the plugin drives.
- The **read-only egress gateway** exposes only a restricted profile (`workspace_lock`,
  `workspace_audit`, `workspace_grounder`, `workspace_model`, `workspace_contract`,
  `workspace_policy`). `workspace_workflow` and other write/private tools are deliberately **off** the
  gateway. If you are on the gateway, mutations are simply not available — fail closed, do not route
  around it.

## What is NOT on the surface

- No host-side verdict — the host never computes allow/hold/deny; `patch_validate` / `operate` do.
- No dials or scores — verdicts are the five discrete Loomground states.
- No way to grant more than the lane allows by asking differently — a grade increase is a denial
  unless it is a new approved lane version.
