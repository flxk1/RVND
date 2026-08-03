// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Build — the patchbay, over the shared store (units/state.mjs). Renders the
// live governance graph (workspace_workflow governance_graph) as agent/person/
// task/boundary nodes and their authority/egress cords, and writes back
// through the same ops the classic canvas (app/src/index.html) uses:
// workspace_policy party_register/party_status for people, workspace_workflow
// use_case_register for tasks and for granting/revoking authority. The server
// still decides every verdict and every grade; this renders it, never
// recomputes it.

import { GHEX, VINFO, effVerdict, allowedFor } from './verdict.mjs';

const NW = 200, NH = 56, GAP_Y = 74;
const COLX = { agent: 24, human: 24, use_case: 300, connector: 300, router: 300, master: 580 };

export function createPatchbay(store, call, doc) {
  const esc = (s) => { const e = doc.createElement('div'); e.textContent = String(s == null ? '' : s); return e.innerHTML; };
  const escA = (s) => String(s == null ? '' : s).replace(/"/g, '&quot;');

  let root = null;
  let g = null;          // last fetched {nodes, edges}, or null
  let loading = false;
  let loadError = '';
  let authStale = false; // true when /tool returned 403 — the page's session token
                         // predates a server restart; only a reload recovers it
  let lanes = {};        // agent id (bare, no party: prefix) -> latest governance lane, or absent
  let sel = null;        // selected node id
  let addOpen = null;    // 'agent' | 'human' | 'use_case' | null — which add-form is open
  let laneFormFor = null; // agent id (bare) whose "register a lane" form is open, or null
  let unsub = null;

  function focus() { return store.getState().activeWorkspace || ''; }
  function byId(id) { return g && g.nodes.find((n) => n.id === id); }

  async function load() {
    const fc = focus();
    loading = !!fc;
    loadError = '';
    authStale = false;
    paint();
    if (!fc) { g = null; loading = false; paint(); return; }
    // Call governance_graph directly (not the swallow-to-null fetchGraph) so the
    // real failure is visible: a 403 means this page's session token predates a
    // server restart and only a reload recovers it — surface that, don't hide it
    // behind a generic "connection" message with a Retry that resends the dead token.
    try {
      const r = await call('workspace_workflow', { op: 'governance_graph', params: { folder_context: fc } });
      g = (r && Array.isArray(r.nodes)) ? r : null;
      if (!g) loadError = 'This workspace returned no patch yet (empty or unreadable graph).';
    } catch (e) {
      g = null;
      const msg = String((e && e.message) || e);
      if (/\b403\b/.test(msg)) {
        authStale = true;
        loadError = 'Session expired — the RVND server was restarted after this page loaded. Reconnect to get a fresh session.';
      } else {
        loadError = 'The patch could not be loaded (' + msg + '). Is the RVND server still running?';
      }
    }
    loading = false;
    lanes = {};
    if (g) {
      const r = await call('workspace_workflow', { op: 'governance_lane_list', params: { folder_context: fc } }).catch(() => null);
      (r && Array.isArray(r.lanes) ? r.lanes : []).forEach((l) => { lanes[l.agent] = l; });
    }
    paint();
  }

  function mount(container) {
    root = container;
    unsub = store.subscribe(() => load());
    load();
  }
  function unmount() {
    if (unsub) unsub();
    unsub = null;
    root = null;
  }
  function refresh() { load(); }

  // --- layout -----------------------------------------------------------

  function computeLayout(nodes) {
    const rows = {};
    const pos = {};
    for (const n of nodes) {
      const col = n.kind === 'human' ? 'agent' : n.kind;
      const x = COLX[col] != null ? COLX[col] : COLX.use_case;
      const i = rows[col] || 0; rows[col] = i + 1;
      pos[n.id] = { x, y: 20 + i * GAP_Y };
    }
    return pos;
  }

  function nodeWidth(n) { return n.kind === 'master' ? 150 : NW; }

  // --- render -------------------------------------------------------------

  function paint() {
    if (!root) return;
    if (!focus()) { root.innerHTML = '<div class="pb-empty">open a workspace to see its patch</div>'; return; }
    if (loading) { root.innerHTML = '<div class="pb-empty">loading the patch…</div>'; return; }
    if (!g) {
      const label = authStale ? 'Reconnect' : 'Retry';
      root.innerHTML = '<div class="pb-empty pb-error">' + esc(loadError || 'The patch is unavailable.')
        + ' <button class="pb-retry">' + label + '</button></div>';
      const retry = root.querySelector('.pb-retry');
      // On a stale session (403) a re-fetch resends the same dead token — only a
      // page reload pulls a fresh token from the running server. Otherwise re-run.
      if (retry) retry.addEventListener('click', authStale ? () => doc.defaultView.location.reload() : load);
      return;
    }

    const pos = computeLayout(g.nodes);
    const stageW = Math.max(760, COLX.master + 170);
    const stageH = 40 + Math.max(1, ...Object.values(pos).map((p) => p.y + NH)) ;

    let cords = '';
    for (const e of g.edges) {
      const a = byId(e.from), b = byId(e.to);
      if (!a || !b || !pos[a.id] || !pos[b.id]) continue;
      const pa = pos[a.id], pb = pos[b.id];
      const x1 = pa.x + nodeWidth(a), y1 = pa.y + NH / 2;
      const x2 = pb.x, y2 = pb.y + NH / 2;
      const col = e.kind === 'egress' ? VINFO(effVerdict(byId, e)).col : '#5f6675';
      cords += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${col}" stroke-width="2" ${e.kind === 'egress' && effVerdict(byId, e) === 'unfired' ? 'stroke-dasharray="3 6"' : ''}/>`;
    }

    const nodesHtml = g.nodes.map((n) => nodeHtml(n, pos[n.id])).join('');

    root.innerHTML =
      '<div class="pb-toolbar">'
      + '<button class="pb-add" data-add="agent">+ Agent</button>'
      + '<button class="pb-add" data-add="human">+ Person</button>'
      + '<button class="pb-add" data-add="use_case">+ Task</button>'
      + '<span class="pb-hint">' + (g.nodes.length ? g.nodes.length + ' node' + (g.nodes.length === 1 ? '' : 's') : 'this patch is empty') + '</span>'
      + '</div>'
      + (addOpen ? addFormHtml(addOpen) : '')
      + '<div class="pb-stage" style="width:' + stageW + 'px;height:' + stageH + 'px">'
      + '<svg class="pb-cords" width="' + stageW + '" height="' + stageH + '">' + cords + '</svg>'
      + nodesHtml
      + '</div>'
      + '<div class="pb-inspect">' + inspectHtml() + '</div>';

    wireEvents();
  }

  function nodeHtml(n, p) {
    const kindLabel = { agent: 'Agent', human: 'Person', use_case: 'Task', master: 'Boundary', connector: 'Connector', router: 'Routing' }[n.kind] || n.kind;
    let meta = '';
    if (n.kind === 'agent') meta = esc(n.grade || '') + (n.status && n.status !== 'active' ? ' · ' + esc(n.status) : '');
    else if (n.kind === 'human') meta = n.role ? esc(n.role) : 'in the loop';
    else if (n.kind === 'use_case') {
      const grade = Math.max(0, Math.min(4, n.grade || 0));
      const ev = g.edges.find((e) => e.kind === 'egress' && e.from === n.id);
      const v = ev ? VINFO(effVerdict(byId, ev)) : null;
      meta = 'L' + grade + ' · ' + esc(n.risk || 'low') + (v && v.label ? ' · ' + esc(v.label) : '');
    } else if (n.kind === 'master') meta = 'edge of what’s allowed out';
    const w = nodeWidth(n);
    return '<div class="pb-node pb-' + n.kind + (sel === n.id ? ' sel' : '') + '" data-id="' + esc(n.id)
      + '" style="left:' + p.x + 'px;top:' + p.y + 'px;width:' + w + 'px" tabindex="0" role="button">'
      + '<div class="pb-nk">' + esc(kindLabel) + '</div>'
      + '<div class="pb-nl">' + esc(n.label || n.id) + '</div>'
      + '<div class="pb-nm">' + meta + '</div></div>';
  }

  function addFormHtml(kind) {
    const label = kind === 'use_case' ? 'Task' : kind === 'agent' ? 'Agent' : 'Person';
    return '<div class="pb-form">'
      + '<span class="pb-form-h">New ' + label + '</span>'
      + '<input id="pb-name" placeholder="name" aria-label="name">'
      + (kind === 'use_case' ? '<select id="pb-risk"><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="critical">critical</option></select>' : '')
      + '<button class="pb-form-ok" id="pb-create">Create</button>'
      + '<button class="pb-form-cancel" id="pb-cancel">Cancel</button>'
      + '</div>';
  }

  function inspectHtml() {
    const n = sel ? byId(sel) : null;
    if (!n) return '<div class="pb-empty">select a node to inspect it</div>';
    if (n.kind === 'agent' || n.kind === 'human') return inspectParty(n);
    if (n.kind === 'use_case') return inspectUseCase(n);
    if (n.kind === 'master') return inspectMaster();
    return '<div class="pb-empty">' + esc(n.label || n.id) + '</div>';
  }

  function inspectParty(n) {
    const killed = n.kind === 'agent' && n.status && n.status !== 'active';
    let h = '<div class="pb-i-h">' + esc(n.label || n.id) + '</div>';
    h += '<div class="pb-i-row">' + (n.kind === 'agent' ? 'grade <b>' + esc(n.grade || '—') + '</b> · status <b>' + esc(n.status || 'active') + '</b>' : 'a person in the loop' + (n.role ? ' · ' + esc(n.role) : '')) + '</div>';
    if (n.kind === 'agent') h += '<button class="pb-kill" data-id="' + esc(n.id) + '" data-want="' + (killed ? 'active' : 'killed') + '">' + (killed ? 'Revive agent' : 'Kill agent') + '</button>';
    if (n.kind === 'agent') h += inspectLane(n.id.replace(/^party:/, ''));
    return h;
  }

  // A governed run (operate) now needs a live session capability, and
  // opening one (governance_open) needs an approved governance lane for the
  // agent — a separate durable approval, not implied by an authority cord.
  // No op computes a "current policy fingerprint" yet, so it's a plain field
  // here; Run reads it back off the same lane record it registers, so any
  // value round-trips correctly as long as it's non-empty.
  function inspectLane(agentId) {
    const lane = lanes[agentId];
    let h = '<div class="pb-i-sub">Governance lane</div>';
    if (lane) {
      h += '<div class="pb-i-row">' + esc(lane.lane_id) + ' · max <b>' + esc(lane.max_grade) + '</b> · v' + esc(lane.version) + '</div>';
      h += '<div class="pb-i-row pb-i-dim">' + esc((lane.action_classes || []).join(', ') || 'no action classes') + '</div>';
      h += '<div class="pb-i-row pb-i-dim">approved by ' + esc(lane.approved_by) + ' — ' + esc(lane.rationale) + '</div>';
      h += '<button class="pb-lane-open" data-agent="' + esc(agentId) + '">Renew (new version)</button>';
    } else {
      h += '<div class="pb-i-row pb-i-warn">no approved lane — this agent cannot open a governed run session</div>';
      h += '<button class="pb-lane-open" data-agent="' + esc(agentId) + '">Register a lane</button>';
    }
    if (laneFormFor === agentId) h += laneFormHtml(agentId, lane);
    return h;
  }

  function laneFormHtml(agentId, existing) {
    const nextVersion = existing ? existing.version + 1 : 1;
    return '<div class="pb-form" style="flex-direction:column;align-items:stretch;gap:6px;margin-top:6px">'
      + '<input id="pb-lane-id" placeholder="lane id" value="' + escA(existing ? existing.lane_id : 'lane-' + agentId) + '">'
      + '<select id="pb-lane-grade">' + ['L0', 'L1', 'L2', 'L3', 'L4'].map((g) => '<option value="' + g + '"' + (existing && existing.max_grade === g ? ' selected' : '') + '>' + g + '</option>').join('') + '</select>'
      + '<input id="pb-lane-actions" placeholder="action classes, comma-separated" value="' + escA(existing ? (existing.action_classes || []).join(', ') : '') + '">'
      + '<input id="pb-lane-fpr" placeholder="policy fingerprint (any stable label)" value="' + escA(existing ? existing.policy_fingerprint : '') + '">'
      + '<input id="pb-lane-approver" placeholder="approved by" value="' + escA(existing ? existing.approved_by : 'app-user') + '">'
      + '<input id="pb-lane-rationale" placeholder="rationale (required)" value="' + escA(existing ? existing.rationale : '') + '">'
      + '<div class="pb-i-dim">version ' + nextVersion + (existing ? ' (widening this lane)' : '') + '</div>'
      + '<div style="display:flex;gap:8px"><button class="pb-form-ok" id="pb-lane-save" data-agent="' + esc(agentId) + '" data-version="' + nextVersion + '">Save</button>'
      + '<button class="pb-form-cancel" id="pb-lane-cancel">Cancel</button></div></div>';
  }

  function inspectUseCase(n) {
    const allowed = allowedFor(g.edges, n.id);
    const agents = g.nodes.filter((x) => x.kind === 'agent' && !allowed.includes(x.id.replace(/^party:/, '')));
    const ev = g.edges.find((e) => e.kind === 'egress' && e.from === n.id);
    const v = ev ? VINFO(effVerdict(byId, ev)) : null;
    let h = '<div class="pb-i-h">' + esc(n.label || n.id) + '</div>';
    h += '<div class="pb-i-row">risk <b>' + esc(n.risk || 'low') + '</b> · L' + Math.max(0, Math.min(4, n.grade || 0)) + (v ? ' · <span style="color:' + v.col + '">' + esc(v.label) + '</span>' : '') + '</div>';
    if (n.reserved && n.reserved.length) h += '<div class="pb-i-row pb-i-warn">reserved: ' + esc(n.reserved.join(', ')) + '</div>';
    h += '<div class="pb-i-sub">Authority</div>';
    h += allowed.length
      ? allowed.map((a) => '<div class="pb-i-row">' + esc(a) + ' <button class="pb-revoke" data-uc="' + esc(n.id) + '" data-agent="' + esc(a) + '">Revoke</button></div>').join('')
      : '<div class="pb-i-row pb-i-dim">no agent wired — this task cannot run</div>';
    if (agents.length) {
      h += '<div class="pb-i-row"><select id="pb-grant-agent">' + agents.map((a) => '<option value="' + esc(a.id) + '">' + esc(a.label || a.id) + '</option>').join('')
        + '</select> <button class="pb-grant" data-uc="' + esc(n.id) + '">Grant authority</button></div>';
    }
    return h;
  }

  function inspectMaster() {
    const evs = g.edges.filter((e) => e.kind === 'egress').map((e) => effVerdict(byId, e));
    const n = (v) => evs.filter((x) => x === v).length;
    let h = '<div class="pb-i-h">the boundary — what’s in force</div>';
    h += '<div class="pb-i-row">releasing · auto within policy · ' + n('auto') + ' cord' + (n('auto') === 1 ? '' : 's') + '</div>';
    h += '<div class="pb-i-row">needs a person · ' + n('human') + ' cord' + (n('human') === 1 ? '' : 's') + '</div>';
    h += '<div class="pb-i-row">reserved · a person must sign off · ' + n('reserved') + ' cord' + (n('reserved') === 1 ? '' : 's') + '</div>';
    h += '<div class="pb-i-row">not allowed · severed · ' + n('prohibited') + ' cord' + (n('prohibited') === 1 ? '' : 's') + '</div>';
    return h;
  }

  // --- writes (same ops the classic canvas uses) ---------------------------

  // Preserve the use case's real fingerprint from the chain — re-registering
  // from the graph node alone would blank it and silently no-op every future
  // wire/rename (the graph node itself carries no fingerprint).
  async function reReg(ucNode, allowed) {
    const fc = focus();
    const ucid = ucNode.id.replace(/^uc:/, '');
    let fingerprint = ucNode.issue_type ? { issue_type: ucNode.issue_type } : {};
    const r = await call('workspace_workflow', { op: 'use_case_get', params: { folder_context: fc, use_case_id: ucid } }).catch(() => null);
    const got = (r && r.use_case) || {};
    if (got.fingerprint && typeof got.fingerprint === 'object' && Object.keys(got.fingerprint).length) fingerprint = got.fingerprint;
    return call('workspace_workflow', { op: 'use_case_register', params: { folder_context: fc, use_case_id: ucid, name: ucNode.label, fingerprint, risk: ucNode.risk || 'low', allowed_agents: allowed, actor: 'app-user' } });
  }

  async function grant(ucId, agentId) {
    const uc = byId(ucId); if (!uc) return;
    const allowed = allowedFor(g.edges, ucId);
    const bare = agentId.replace(/^party:/, '');
    if (!allowed.includes(bare)) allowed.push(bare);
    await reReg(uc, allowed);
    await load();
    await store.hydrate();
  }

  async function revoke(ucId, agentId) {
    const uc = byId(ucId); if (!uc) return;
    if (!doc.defaultView.confirm('Revoke authority: ' + agentId + ' → ' + (uc.label || ucId) + '?\nThis removes a signed grant and is recorded.')) return;
    const allowed = allowedFor(g.edges, ucId).filter((a) => a !== agentId);
    await reReg(uc, allowed);
    await load();
    await store.hydrate();
  }

  async function toggleKill(agentId, want) {
    await call('workspace_policy', { op: 'party_status', params: { folder_context: focus(), party_id: agentId.replace(/^party:/, ''), status: want, actor: 'app-user' } }).catch(() => null);
    await load();
    await store.hydrate();
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

  async function create(kind, name, risk) {
    const fc = focus(); if (!fc || !name) return;
    let r;
    if (kind === 'use_case') {
      const id = 'uc-' + (Date.now() % 100000);
      r = await call('workspace_workflow', { op: 'use_case_register', params: { folder_context: fc, use_case_id: id, name, fingerprint: {}, risk: risk || 'low', allowed_agents: [], actor: 'app-user' } }).catch((e) => ({ error: String(e && e.message || e) }));
    } else {
      const id = (kind === 'agent' ? 'agent-' : 'human-') + (Date.now() % 100000);
      r = await call('workspace_policy', { op: 'party_register', params: { folder_context: fc, party_id: id, kind, name, actor: 'app-user' } }).catch((e) => ({ error: String(e && e.message || e) }));
    }
    if (!r || r.error || r.ok === false) {
      const detail = r && (r.error || r.message);
      doc.defaultView.alert('Create failed' + (detail ? ': ' + detail : ': the server did not accept the request.'));
      return;
    }
    addOpen = null;
    await load();
    await store.hydrate();
  }

  // --- events ---------------------------------------------------------------

  function wireEvents() {
    if (!root) return;
    root.querySelectorAll('[data-add]').forEach((b) => b.addEventListener('click', () => { addOpen = b.dataset.add; sel = null; paint(); }));
    const cancel = root.querySelector('#pb-cancel'); if (cancel) cancel.addEventListener('click', () => { addOpen = null; paint(); });
    const okBtn = root.querySelector('#pb-create');
    if (okBtn) okBtn.addEventListener('click', () => {
      const nm = (root.querySelector('#pb-name').value || '').trim();
      const risk = addOpen === 'use_case' ? root.querySelector('#pb-risk').value : undefined;
      if (nm) create(addOpen, nm, risk);
    });
    root.querySelectorAll('.pb-node').forEach((el) => el.addEventListener('click', () => { sel = el.dataset.id; addOpen = null; laneFormFor = null; paint(); }));
    root.querySelectorAll('.pb-kill').forEach((b) => b.addEventListener('click', (ev) => { ev.stopPropagation(); toggleKill(b.dataset.id, b.dataset.want); }));
    root.querySelectorAll('.pb-grant').forEach((b) => b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const sel2 = root.querySelector('#pb-grant-agent');
      if (sel2 && sel2.value) grant(b.dataset.uc, sel2.value);
    }));
    root.querySelectorAll('.pb-revoke').forEach((b) => b.addEventListener('click', (ev) => { ev.stopPropagation(); revoke(b.dataset.uc, b.dataset.agent); }));
    root.querySelectorAll('.pb-lane-open').forEach((b) => b.addEventListener('click', (ev) => { ev.stopPropagation(); laneFormFor = b.dataset.agent; paint(); }));
    const laneCancel = root.querySelector('#pb-lane-cancel'); if (laneCancel) laneCancel.addEventListener('click', (ev) => { ev.stopPropagation(); laneFormFor = null; paint(); });
    const laneSave = root.querySelector('#pb-lane-save');
    if (laneSave) laneSave.addEventListener('click', (ev) => {
      ev.stopPropagation();
      registerLane(laneSave.dataset.agent, Number(laneSave.dataset.version), {
        laneId: (root.querySelector('#pb-lane-id').value || '').trim(),
        grade: root.querySelector('#pb-lane-grade').value,
        actions: root.querySelector('#pb-lane-actions').value || '',
        fingerprint: (root.querySelector('#pb-lane-fpr').value || '').trim(),
        approver: (root.querySelector('#pb-lane-approver').value || '').trim(),
        rationale: (root.querySelector('#pb-lane-rationale').value || '').trim(),
      });
    });
  }

  return { mount, unmount, refresh };
}
