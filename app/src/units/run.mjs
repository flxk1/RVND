// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Run — the mixdesk, over the shared store (units/state.mjs). One channel
// strip per task: its live verdict (workspace_workflow governance_graph),
// a Run button that executes it (workspace_workflow operate) against its
// wired agent, and the step lane of its latest run with a per-step sign-off
// override (workspace_audit record_override) — the same ops the classic
// canvas's Inspector uses. The server still decides every disposition; this
// only executes what's already wired and records what a person decides.

import { VINFO, effVerdict, allowedFor, fetchGraph } from './verdict.mjs';

export function createRun(store, call, doc) {
  const esc = (s) => { const e = doc.createElement('div'); e.textContent = String(s == null ? '' : s); return e.innerHTML; };

  let root = null;
  let g = null;       // {nodes, edges}
  let runs = [];       // workspace_workflow runs — latest per use_case wins
  let unsub = null;
  const sessionMsg = {}; // use_case node id -> transient session/run status or refusal reason

  function focus() { return store.getState().activeWorkspace || ''; }
  function byId(id) { return g && g.nodes.find((n) => n.id === id); }

  async function load() {
    const fc = focus();
    g = await fetchGraph(call, fc);
    if (fc) {
      const r = await call('workspace_workflow', { op: 'runs', params: { folder_context: fc } }).catch(() => null);
      runs = (r && Array.isArray(r.runs)) ? r.runs : [];
    } else runs = [];
    paint();
  }

  function mount(container) { root = container; unsub = store.subscribe(() => load()); load(); }
  function unmount() { if (unsub) unsub(); unsub = null; root = null; }
  function refresh() { load(); }

  function latestRunFor(ucid) {
    const mine = runs.filter((r) => r.use_case_id === ucid);
    return mine.length ? mine[mine.length - 1] : null;
  }

  function paint() {
    if (!root) return;
    if (!focus()) { root.innerHTML = '<div class="rn-empty">open a workspace to see its tasks</div>'; return; }
    if (!g) { root.innerHTML = '<div class="rn-empty">loading…</div>'; return; }
    const tasks = g.nodes.filter((n) => n.kind === 'use_case');
    root.innerHTML = tasks.length
      ? tasks.map(stripHtml).join('')
      : '<div class="rn-empty">no tasks in this patch yet — add one in Build</div>';
    wireEvents();
  }

  function stripHtml(n) {
    const ucid = n.id.replace(/^uc:/, '');
    const agent = allowedFor(g.edges, n.id)[0];
    const ev = g.edges.find((e) => e.kind === 'egress' && e.from === n.id);
    const v = ev ? VINFO(effVerdict(byId, ev)) : null;
    const run = latestRunFor(ucid);
    const canRun = !!agent;
    let h = '<div class="rn-strip" data-uc="' + esc(n.id) + '">';
    h += '<div class="rn-top"><span class="rn-label">' + esc(n.label || n.id) + '</span>'
      + (v ? '<span class="rn-v" style="color:' + v.col + '">' + esc(v.label) + '</span>' : '') + '</div>';
    h += '<div class="rn-meta">L' + Math.max(0, Math.min(4, n.grade || 0)) + ' · risk ' + esc(n.risk || 'low')
      + (agent ? ' · agent ' + esc(agent) : ' · no agent wired') + '</div>';
    h += canRun
      ? '<button class="rn-run" data-uc="' + esc(n.id) + '">▶ Run</button>'
      : '<div class="rn-dim">wire an agent in Build to run this</div>';
    if (sessionMsg[n.id]) h += '<div class="rn-dim" style="margin-top:4px">' + esc(sessionMsg[n.id]) + '</div>';
    if (run) {
      h += '<div class="rn-runh">run ' + esc((run.run_id || '').slice(0, 6)) + ' · ' + esc(run.final || '') + '</div>';
      h += (run.steps || []).map((s) => stepHtml(s)).join('');
    }
    h += '</div>';
    return h;
  }

  function stepHtml(s) {
    const needs = s.disposition === 'human' || s.disposition === 'reserved';
    const col = VINFO(s.disposition).col;
    let h = '<div class="rn-step"><span class="rn-step-dot" style="background:' + col + '"></span>'
      + '<span class="rn-step-lbl">' + esc(s.issue_id) + ' · ' + esc(s.disposition) + (s.reserved_to ? ' → ' + esc(s.reserved_to) : '') + '</span>';
    if (needs) {
      h += '<button class="rn-ov" data-i="' + esc(s.issue_id) + '" data-v="approved">approve</button>'
        + '<button class="rn-ov" data-i="' + esc(s.issue_id) + '" data-v="rejected">reject</button>';
    }
    h += '<span class="rn-ov-r" data-r="' + esc(s.issue_id) + '"></span></div>';
    return h;
  }

  // operate() now requires a live session capability (workspace_workflow
  // operations.py, session_admission.py): open one for this agent first,
  // then run. Opening needs the agent's currently-approved governance lane
  // (governance_lane_register — an explicit approval step in Build). In a
  // proxy deployment the request principal must match that agent; the
  // loopback-only app binds the agent to its authenticated bridge session for
  // this one mint operation. The capability is still re-checked by operate().
  async function runUC(ucNodeId) {
    const n = byId(ucNodeId); if (!n) return;
    const agent = allowedFor(g.edges, ucNodeId)[0]; if (!agent) return;
    const ucid = ucNodeId.replace(/^uc:/, '');
    const fc = focus();

    sessionMsg[ucNodeId] = 'opening a governed session…';
    paint();

    const lanes = await call('workspace_workflow', { op: 'governance_lane_list', params: { folder_context: fc } }).catch(() => null);
    const lane = lanes && Array.isArray(lanes.lanes) ? lanes.lanes.find((l) => l.agent === agent) : null;
    if (!lane) {
      sessionMsg[ucNodeId] = 'no approved governance lane for ' + agent + ' — register one (governance_lane_register) before it can run';
      paint();
      return;
    }

    const opened = await call('workspace_workflow', { op: 'governance_open', params: { folder_context: fc, party: agent, policy_fingerprint: lane.policy_fingerprint } }).catch((e) => ({ error: String(e && e.message || e) }));
    if (!opened || opened.error || opened.ok === false) {
      sessionMsg[ucNodeId] = 'session refused: ' + ((opened && opened.error) || (opened && Array.isArray(opened.violations) && opened.violations.join(', ')) || 'unknown');
      paint();
      return;
    }

    const r = await call('workspace_workflow', {
      op: 'operate',
      params: { folder_context: fc, use_case_id: ucid, agent_id: agent, capability_token: opened.capability_token, issues: [{ issue_id: 'i-' + (Date.now() % 100000), issue_type: n.issue_type || '', completeness: 'high' }], now_epoch: Math.floor(Date.now() / 1000) },
    }).catch((e) => ({ error: String(e && e.message || e) }));
    sessionMsg[ucNodeId] = (r && r.error) ? ('run failed: ' + r.error) : (r && r.final === 'refused' ? 'refused: ' + (r.reason || '') : null);
    await load();
    await store.hydrate();
  }

  async function overrideStep(issueId, value) {
    const out = root && root.querySelector('.rn-ov-r[data-r="' + issueId + '"]');
    if (out) out.textContent = '…';
    try {
      await call('workspace_audit', { op: 'record_override', params: { folder_context: focus(), card: { issue_id: issueId, stage: 'decision', node_id: issueId }, actor: 'app-user', field: 'disposition', new_value: value, rationale: 'decided in the mixdesk' } });
      if (out) out.textContent = '✓ ' + value;
    } catch (e) { if (out) out.textContent = 'err'; }
  }

  function wireEvents() {
    if (!root) return;
    root.querySelectorAll('.rn-run').forEach((b) => b.addEventListener('click', () => runUC(b.dataset.uc)));
    root.querySelectorAll('.rn-ov').forEach((b) => b.addEventListener('click', () => overrideStep(b.dataset.i, b.dataset.v)));
  }

  return { mount, unmount, refresh };
}
