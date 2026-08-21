---
name: resolve-a-conflict
description: Two governance rules clash — surface the conflict and either validate a candidate resolving delta or hand back a residual decision for a person. Reads the shared 5D+nD governance graph, lanes, and netlist, identifies the incompatible typed constructs, and patch_validates a resolving delta; consumes the legal plane's source hierarchy / precedence (loomground_legal) to rank which norm wins. Never chooses a winner in the host — routes ratification through sign-off, applies only through govern-an-action. Cascades local-first — the RVND engine adjudicates precisely when present; otherwise the cloud LLM works the same Subgraph in-grammar (degraded, advisory) until the engine ratifies. Triggers — "these rules conflict", "resolve the rule conflict", "which authority wins here", "two policies contradict each other", "tell me if this needs a person".
---

# resolve-a-conflict

Two rules clash — resolve it, or tell the user it needs a person. This skill surfaces conflicting
governance rules and either validates a candidate resolving delta or surfaces a residual decision. It
**never chooses a winner in the host**: adjudication is the engine's (or a human's), and ratification
routes through `sign-off`.

## The object it works

One **dimensioned `Subgraph`** — the shared 5D+nD object versum stores and RVND ingests, read through
the `loomground_ingest` plane, never hand-built. The clashing rules are typed constructs already in
that graph (deontic `O/P/F` norms, governance cords, legal anchors). Resolving means proposing a
delta to the same Subgraph, not minting a bespoke resolution format.

The **legal plane's source hierarchy / precedence** (`loomground_legal` — `Anchor / ApplicableLaw /
Citation / Adjudication`) is what precisely adjudicates *which* rule wins: it ranks the clashing norms
by source authority and precedence and carries honest defeasibility. Consume it; do not re-decide
precedence in the host.

## The flow

1. **Discover the live operations** and read the governance graph, lanes, and current netlist.
2. **Identify the conflicting typed constructs** and their source evidence — the incompatible norms
   and the spans they came from.
3. **Rank by precedence.** Consume `loomground_legal`'s source hierarchy to order the clashing norms;
   a higher source defeats a lower one, defeasibly.
4. **Submit a candidate resolving delta** through `patch_validate`.
5. **If RVND returns a residual-origination decision,** present at least two unranked alternatives —
   never approve/reject vocabulary. This is the "it needs a person" outcome.
6. **Route ratification through `sign-off`;** apply only through `govern-an-action` after
   authorization. This skill applies nothing and signs nothing.
7. **If no real operation can resolve the conflict,** mark it unavailable and stop — a visible
   residual, never a fabricated winner.

## Cascade & the shared graph

This skill follows the shared architecture in `../../references/ingest-cascade.md` — one dimensioned
Subgraph, operated through the ingest plane, **local-first**:

- **Same object.** It reads and proposes deltas on the shared Subgraph and ranks clashing norms by
  `loomground_legal` precedence — it does not invent a conflict or resolution format.
- **Engine first, never the reverse.** When RVND is present and the folder is governed, the engine
  adjudicates precisely — validated deltas, precedence computed server-side. Only if the engine is
  absent or cannot does the skill **delegate to the free plane skills** — `loomground-ingest` to read
  and lower the graph, `deontic` and `loomground` for the norm and governance facets — and work the
  assembled Subgraph in-grammar. Precedence is consumed from the `loomground_legal` **package**
  directly (the legal plane ships no skill of its own). A delegated-only build is **degraded /
  advisory** and says so, until the engine ratifies.
- **Routes to sign-off.** Ratification always goes through `sign-off`; application through
  `govern-an-action`. The host never picks a winner.

## More

- `../../references/ingest-cascade.md` — the cascade, the plane list, the ingester entry points.
- `references/reference.md` — the RVND op mapping and the residual-decision contract.
- `references/eval.json` — what it drives, guarantees, and review status.
