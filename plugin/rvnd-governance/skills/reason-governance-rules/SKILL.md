---
name: reason-governance-rules
description: Ask the RVND server for the governed outcome of a concrete action. Use when a case must be checked against the active governance lane and returned with the server's discrete verdict and evidence. Never calculate or soften a verdict in the host.
---

# Reason over governance rules

Use RVND's live `operate` contract; the server decides and the host renders.

1. Discover `workspace_workflow` operations and required parameters.
2. Resolve folder, use-case, actor, agent, and governed-object identities.
3. Read the active lane and graph before proposing execution.
4. Call `workspace_workflow(op="operate")` with the documented case payload.
5. Preserve the returned Loomground verdict, residual, evidence, and unavailable fields.
6. Render `human` or `reserved` as escalation, never as permission.
7. Verify any resulting receipt with `workspace_audit(op="verify_chain")`.

Read `references/reference.md`. Do not reproduce kernel reasoning or derive a host-side
PASS/VIOLATION label.
