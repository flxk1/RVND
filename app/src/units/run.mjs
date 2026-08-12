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
  const escA = (s) => String(s == null ? '' : s).replace(/"/g, '&quot;');

  let root = null;
  let g = null;       // {nodes, edges}
  let runs = [];       // workspace_workflow runs — latest per use_case wins
  let lanes = {};      // agent id (bare) -> latest governance lane (the channel strip)
  let laneFormFor = null; // agent id whose register/renew lane form is open, or null
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
      const ll = await call('workspace_workflow', { op: 'governance_lane_list', params: { folder_context: fc } }).catch(() => null);
      lanes = {};
      (ll && Array.isArray(ll.lanes) ? ll.lanes : []).forEach((l) => { lanes[l.agent] = l; });
    } else { runs = []; lanes = {}; }
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
    if (!focus()) { root.innerHTML = '<div class="rn-empty">open a workspace to see its mixdesk</div>'; return; }
    if (!g) { root.innerHTML = '<div class="rn-empty">loading…</div>'; return; }
    const agents = g.nodes.filter((n) => n.kind === 'agent');
    const tasks = g.nodes.filter((n) => n.kind === 'use_case');
    // The mixdesk: a governance-lane channel strip per agent, then a task strip
    // per governed act. Agents arrive at the handshake; tasks arrive from the
    // activated policy — the console mints neither.
    let h = '<div class="rn-sec">Governance lanes</div>';
    h += agents.length
      ? agents.map(laneStripHtml).join('')
      : '<div class="rn-dim" style="padding:8px 14px">no agents yet — they identify themselves at the handshake</div>';
    h += '<div class="rn-sec">Tasks</div>';
    h += tasks.length
      ? tasks.map(stripHtml).join('')
      : '<div class="rn-dim" style="padding:8px 14px">no tasks in this patch yet — they arrive from the activated policy</div>';
    root.innerHTML = h;
    wireEvents();
  }

  function laneStripHtml(n) {
    const agentId = n.id.replace(/^party:/, '');
    const lane = lanes[agentId];
    let h = '<div class="rn-strip" data-agent="' + esc(agentId) + '">';
    h += '<div class="rn-top"><span class="rn-label">' + esc(n.label || agentId) + '</span>'
      + (lane ? '<span class="rn-v">max ' + esc(lane.max_grade) + ' · v' + esc(lane.version) + '</span>'
              : '<span class="rn-v" style="color:#d98b8b">no lane</span>') + '</div>';
    if (lane) {
      h += '<div class="rn-meta">' + esc((lane.action_classes || []).join(', ') || 'no action classes') + '</div>';
      h += '<div class="rn-dim">approved by ' + esc(lane.approved_by) + ' — ' + esc(lane.rationale) + '</div>';
      h += '<button class="rn-run rn-lane-open" data-agent="' + esc(agentId) + '">Renew (new version)</button>';
    } else {
      h += '<div class="rn-dim">no approved lane — this agent cannot open a governed run session</div>';
      h += '<button class="rn-run rn-lane-open" data-agent="' + esc(agentId) + '">Register a lane</button>';
    }
    if (laneFormFor === agentId) h += laneFormHtml(agentId, lane);
    h += '</div>';
    return h;
  }

  function laneFormHtml(agentId, existing) {
    const nextVersion = existing ? existing.version + 1 : 1;
    return '<div class="rn-laneform">'
      + '<input id="rn-lane-id" placeholder="lane id" value="' + escA(existing ? existing.lane_id : 'lane-' + agentId) + '">'
      + '<select id="rn-lane-grade">' + ['L0', 'L1', 'L2', 'L3', 'L4'].map((gr) => '<option value="' + gr + '"' + (existing && existing.max_grade === gr ? ' selected' : '') + '>' + gr + '</option>').join('') + '</select>'
      + '<input id="rn-lane-actions" placeholder="action classes, comma-separated" value="' + escA(existing ? (existing.action_classes || []).join(', ') : '') + '">'
      + '<input id="rn-lane-fpr" placeholder="policy fingerprint (any stable label)" value="' + escA(existing ? existing.policy_fingerprint : '') + '">'
      + '<input id="rn-lane-approver" placeholder="approved by" value="' + escA(existing ? existing.approved_by : 'app-user') + '">'
      + '<input id="rn-lane-rationale" placeholder="rationale (required)" value="' + escA(existing ? existing.rationale : '') + '">'
      + '<div class="rn-dim">version ' + nextVersion + (existing ? ' (widening this lane)' : '') + '</div>'
      + '<div style="display:flex;gap:8px"><button class="rn-run rn-lane-save" data-agent="' + esc(agentId) + '" data-version="' + nextVersion + '">Save</button>'
      + '<button class="rn-run rn-lane-cancel">Cancel</button></div></div>';
  }

  async function registerLane(agentId, version, fields) {
    const fc = focus(); if (!fc) return;
    const actionClasses = fields.actions.split(',').map((s) => s.trim()).filter(Boolean);
    if (!fields.laneId || !actionClasses.length || !fields.approver || !fields.rationale) return;
    const r = await call('workspace_workflow', {
      op: 'governance_lane_register',
      params: {
        folder_context: fc, lane_id: fields.laneId, agent: agentId, max_grade: fields.grade,
        action_classes: actionClasses, policy_fingerprint: fields.fingerprint,
        version, approved_by: fields.approver, rationale: fields.rationale,
      },
    }).catch((e) => ({ error: String(e && e.message || e) }));
    if (r && r.error) { doc.defaultView.alert('Lane registration failed: ' + r.error); return; }
    laneFormFor = null;
    await load();
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
    // rn-run is a shared button style; the task-run handler must target only the
    // task buttons (data-uc), never the lane buttons that reuse the same class.
    root.querySelectorAll('.rn-run[data-uc]').forEach((b) => b.addEventListener('click', () => runUC(b.dataset.uc)));
    root.querySelectorAll('.rn-ov').forEach((b) => b.addEventListener('click', () => overrideStep(b.dataset.i, b.dataset.v)));
    root.querySelectorAll('.rn-lane-open').forEach((b) => b.addEventListener('click', () => { laneFormFor = b.dataset.agent; paint(); }));
    const lc = root.querySelector('.rn-lane-cancel'); if (lc) lc.addEventListener('click', () => { laneFormFor = null; paint(); });
    const ls = root.querySelector('.rn-lane-save');
    if (ls) ls.addEventListener('click', () => {
      registerLane(ls.dataset.agent, Number(ls.dataset.version), {
        laneId: (root.querySelector('#rn-lane-id').value || '').trim(),
        grade: root.querySelector('#rn-lane-grade').value,
        actions: root.querySelector('#rn-lane-actions').value || '',
        fingerprint: (root.querySelector('#rn-lane-fpr').value || '').trim(),
        approver: (root.querySelector('#rn-lane-approver').value || '').trim(),
        rationale: (root.querySelector('#rn-lane-rationale').value || '').trim(),
      });
    });
  }

  return { mount, unmount, refresh };
}
