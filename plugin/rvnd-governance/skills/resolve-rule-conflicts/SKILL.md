---
name: resolve-rule-conflicts
description: Surface conflicting RVND governance rules and obtain a server-validated resolution or residual decision. Use when active rules, exceptions, or authorities point to incompatible outcomes. Never choose a winner in the host.
---

# Resolve rule conflicts

RVND has no verified standalone conflict-resolution operation. Use the validated governance cycle
and preserve genuine undecidability.

1. Discover the live operations.
2. Read the governance graph, lanes, and current netlist.
3. Identify the conflicting typed constructs and their source evidence.
4. Submit a candidate resolving delta through `patch_validate`.
5. If RVND returns a residual-origination decision, present at least two unranked alternatives.
6. Route ratification through `rvnd-decide`; apply only through `rvnd-govern` after authorization.
7. If no real operation can resolve the conflict, mark it unavailable and stop.

Read `references/reference.md`. Legal priority language may describe the conflict, but it never
substitutes for an RVND validation result.
