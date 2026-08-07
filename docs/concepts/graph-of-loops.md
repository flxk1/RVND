# Graph of loops (the `loop_graph` projection)

`loop_graph` is one of RVND's read-only projections of the compiled governance
graph (alongside `governance_map` and `governance_kg`). It renders how the
control loops — execution, oversight, drift, recovery, and policy improvement —
watch or veto one another. It reads execution counts from the signed chain and
drift state from the latest baseline; it does not run actions or change policy.

It is a **view**, not the model: a Patchbay view can render the same projection,
and the enforcement it depicts runs whether or not the graph is ever drawn (see
[Enforcement](#enforcement) below).

## Call

Call the workflow facade with the governed folder:

```json
{
  "op": "loop_graph",
  "params": {"folder_context": "/absolute/path/to/workspace"}
}
```

The result contains visualization-ready `nodes` and directed `edges`. Edges
marked `veto: true` can stop or cap execution.

## `control_bindings`

`control_bindings` shows where the compiled policy acts: authority is routed to
execution, autonomy ceilings and reserved acts to oversight, prohibitions to the
recovery breaker, and the signed configuration to drift monitoring. The
projection distributes controls already compiled into the governance graph; it
does not infer new legal meaning from the graph itself.

## Enforcement

Enforcement does not depend on rendering the graph.
`workspaces.loop_graph.assess_with_drift` feeds structural drift into the Breaker
*before* the action gate runs, while behavioral drift routes benign work to
interactive review. The graph is one way to *see* this; the checks run
regardless.
