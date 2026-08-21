# Context card

Renders the current governed state for a folder or agent. This is a **query** surface — read-only,
grants nothing, changes nothing. It is what the host shows before anyone proposes a change.

## Source

Built from `query` reads: `governance_map` (rules by role/step/risk), `governance_lane_list` (the
agent's current lane and ceiling), `loop_graph` `control_bindings` (where policy acts), and
`model_capability` (local-model availability). The server produces it; the card renders it.

## Shows

- The policy governing this folder, or a clear **ungoverned** state if none applies (ungoverned is
  fail-closed for consequential acts, not a blank permit).
- The agent's resolved identity and its lane: autonomy ceiling (`max_grade`), permitted action
  classes, footprints, connectors, policy fingerprint.
- The applicable rules and the controls they bind (authority → execution, ceilings → oversight,
  prohibitions → breaker, config → drift).
- Local-model status and how governance degrades without it.

## Rendering rules

- Discrete lamps for each dimension's state; no dials, no scores.
- Show the ceiling as a ceiling, not as a current grant. A lane is an envelope, not an assertion
  that the agent is operating at the top of it.
- If a principal is unresolved, render the **no-id wall**, not a guessed identity.
- Attribute every rule shown to its policy source.
