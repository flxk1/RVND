// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Shared verdict/graph helpers for Build (units/patchbay.mjs) and Run
// (units/run.mjs) — the same semantics the classic canvas (app/src/index.html)
// renders, so the new console never re-derives its own notion of a verdict.
// The server decides; this only colors and reads what it decided.

// Autonomy-grade ramp, one colour per grade. Length follows the active ladder
// (ISO/IEC 22989 §5.13 level-of-automation, L0..L6); L5 'full' deepens the warm
// end, L6 'self-governing' is a muted inert tone — it is never a live ceiling
// (always refused), so its colour reads as "not an operating level".
export const GHEX = ['#5b7a99', '#5f9088', '#c8a23f', '#df8b46', '#cf463c', '#a8324f', '#6b6472'];

export const VERDICT = {
  auto: { col: '#5aa886', label: 'auto' },
  human: { col: '#df8b46', label: 'needs a person' },
  refused: { col: '#d96a4a', label: 'refused now' },
  reserved: { col: '#e2554a', label: 'reserved' },
  prohibited: { col: '#a8332b', label: 'not allowed' },
  unfired: { col: '#5a616f', label: 'unfired' },
  permitted: { col: '#5f6675', label: '' },
};
export const VINFO = (v) => VERDICT[v] || VERDICT.permitted;

// The reserved-by-law floor may only tighten a more-permissive verdict up to
// 'reserved'; it never softens a stricter server verdict. An unknown or
// missing verdict renders most-restrictively (fail-closed), never permissive.
export function resolveEgressVerdict(verdict, reservedFloor) {
  if (!(verdict in VERDICT)) return 'prohibited';
  if (verdict === 'prohibited') return 'prohibited';
  return reservedFloor ? 'reserved' : verdict;
}

export function effVerdict(byId, e) {
  if (e.kind !== 'egress') return e.verdict;
  const uc = byId(e.from);
  return resolveEgressVerdict(e.verdict, !!(uc && uc.reserved && uc.reserved.length));
}

export function allowedFor(edges, ucId) {
  return edges.filter((e) => e.kind === 'authority' && e.to === ucId).map((e) => e.from.replace(/^party:/, ''));
}

export async function fetchGraph(call, folderContext) {
  if (!folderContext) return null;
  const r = await call('workspace_workflow', { op: 'governance_graph', params: { folder_context: folderContext } }).catch(() => null);
  return (r && Array.isArray(r.nodes)) ? r : null;
}
