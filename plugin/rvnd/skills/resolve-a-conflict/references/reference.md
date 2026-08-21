# Resolve a conflict: RVND mapping

- Read with `governance_graph`, `governance_lane_list`, and `governance_netlist`.
- Validate a resolving delta with `patch_validate`.
- There is no verified `resolve_conflict` operation; do not invent one.
- Rank clashing norms by consuming `loomground_legal`'s source hierarchy / precedence — a higher
  source defeats a lower one, defeasibly. The host never re-decides precedence on its own.
- Ratification uses approve/deny. Residual origination presents two or more unranked alternatives
  and never uses approve/reject vocabulary — this is the "it needs a person" outcome.
- Application belongs to `govern-an-action` after authorization.

## Cascade

Local-first (see `../../references/ingest-cascade.md`): the RVND engine adjudicates precisely when
present; otherwise the cloud LLM works the same Subgraph in-grammar, degraded and advisory, until the
engine ratifies. Legal precedence language may describe the conflict, but standalone it never
substitutes for an RVND validation result.

Shared contracts: `../../../references/catalogue.md`, `../../../references/protocol.md`, and
`../../../apps/decision-card.md`.
