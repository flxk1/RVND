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
  let sel = null;        // selected node id
  let boundaryFormFor = null; // boundary node id whose routing form is open, or null
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
      + '<span class="pb-hint">inspect only — this patch reflects the activated policy; agents arrive at the handshake, not here</span>'
      + '<span class="pb-hint">' + (g.nodes.length ? g.nodes.length + ' node' + (g.nodes.length === 1 ? '' : 's') : 'this patch is empty') + '</span>'
      + '</div>'
      + '<div class="pb-stage" style="width:' + stageW + 'px;height:' + stageH + 'px">'
      + '<svg class="pb-cords" width="' + stageW + '" height="' + stageH + '">' + cords + '</svg>'
      + nodesHtml
      + '</div>'
      + '<div class="pb-inspect">' + inspectHtml() + '</div>';

    wireEvents();
  }

  function nodeHtml(n, p) {
    // An egress connector that carries a policy-derived gate renders AS a
    // boundary (label + gate meta), so N boundaries stand in a row alongside
    // the single master edge. The gate fields (floor/group/destination_class)
    // come from governance_graph's egress_boundaries projection.
    const kindLabel = n.is_boundary ? 'Boundary'
      : ({ agent: 'Agent', human: 'Person', use_case: 'Task', master: 'Boundary', connector: 'Connector', router: 'Routing' }[n.kind] || n.kind);
    let meta = '';
    if (n.kind === 'agent') meta = esc(n.grade || '') + (n.status && n.status !== 'active' ? ' · ' + esc(n.status) : '');
    else if (n.kind === 'human') meta = n.role ? esc(n.role) : 'in the loop';
    else if (n.kind === 'use_case') {
      const grade = Math.max(0, Math.min(4, n.grade || 0));
      const ev = g.edges.find((e) => e.kind === 'egress' && e.from === n.id);
      const v = ev ? VINFO(effVerdict(byId, ev)) : null;
      // Oversight mode is server-composed (governance_graph); rendered, never computed here.
      const ov = n.oversight && n.oversight.mode ? ' · ' + esc(n.oversight.mode) : '';
      meta = 'L' + grade + ' · ' + esc(n.risk || 'low') + ov + (v && v.label ? ' · ' + esc(v.label) : '');
    } else if (n.kind === 'master') meta = 'edge of what’s allowed out';
    if (n.is_boundary) meta = 'floor ' + esc(n.floor || 'permit')
      + (n.group ? ' · group ' + esc(n.group) : '')
      + (n.destination_class ? ' · ' + esc(n.destination_class) : '');
    const w = nodeWidth(n);
    return '<div class="pb-node pb-' + n.kind + (sel === n.id ? ' sel' : '') + '" data-id="' + esc(n.id)
      + '" style="left:' + p.x + 'px;top:' + p.y + 'px;width:' + w + 'px" tabindex="0" role="button">'
      + '<div class="pb-nk">' + esc(kindLabel) + '</div>'
      + '<div class="pb-nl">' + esc(n.label || n.id) + '</div>'
      + '<div class="pb-nm">' + meta + '</div></div>';
  }

  function inspectHtml() {
    const n = sel ? byId(sel) : null;
    if (!n) return '<div class="pb-empty">select a node to inspect it</div>';
    if (n.kind === 'agent' || n.kind === 'human') return inspectParty(n);
    if (n.kind === 'use_case') return inspectUseCase(n);
    if (n.is_boundary) return inspectBoundary(n);
    if (n.kind === 'master') return inspectMaster();
    return '<div class="pb-empty">' + esc(n.label || n.id) + '</div>';
  }

  function inspectParty(n) {
    let h = '<div class="pb-i-h">' + esc(n.label || n.id) + '</div>';
    h += '<div class="pb-i-row">' + (n.kind === 'agent' ? 'grade <b>' + esc(n.grade || '—') + '</b> · status <b>' + esc(n.status || 'active') + '</b>' : 'a person in the loop' + (n.role ? ' · ' + esc(n.role) : '')) + '</div>';
    // Oversight (kill/revive) is not performed here — it is decided in the
    // oversight repo and arrives as a governed, translated act on the chain.
    // The governance lane (the channel strip) is set on Run (the mixdesk), not here.
    return h;
  }

  // A boundary is the editable routing point of the patch: which bus/class it
  // routes to (group) and its own gate (floor). Editing re-versions the egress
  // connector — the same op the policy used to create it. No node is minted;
  // the destination-class comes from the policy and stays read-only.
  function inspectBoundary(n) {
    const cid = n.id.replace(/^conn:/, '');
    let h = '<div class="pb-i-h">' + esc(n.label || cid) + '</div>';
    h += '<div class="pb-i-row">egress · ' + esc(n.channel || 'channel')
      + (n.destination_class ? ' · ' + esc(n.destination_class) : '') + '</div>';
    if (boundaryFormFor === n.id) { h += boundaryFormHtml(n); return h; }
    h += '<div class="pb-i-row">floor <b>' + esc(n.floor || 'permit') + '</b>'
      + (n.group ? ' · bus <b>' + esc(n.group) + '</b>' : ' · <span class="pb-i-warn">no bus</span>') + '</div>';
    h += '<button class="pb-bnd-open" data-id="' + esc(n.id) + '">Route this boundary</button>';
    return h;
  }

  function boundaryFormHtml(n) {
    const floors = ['permit', 'hold', 'deny'];
    const opt = (f, cur) => '<option value="' + f + '"' + (cur === f ? ' selected' : '') + '>' + f + '</option>';
    return '<div class="pb-form" style="flex-direction:column;align-items:stretch;gap:6px;margin-top:6px">'
      + '<label class="pb-i-dim">floor — this channel’s own gate</label>'
      + '<select id="pb-bnd-floor">' + floors.map((f) => opt(f, n.floor || 'permit')).join('') + '</select>'
      + '<label class="pb-i-dim">bus / class — the group this boundary routes to</label>'
      + '<input id="pb-bnd-group" placeholder="group id (e.g. company, finance)" value="' + escA(n.group || '') + '">'
      + '<label class="pb-i-dim">bus floor — binds every boundary on this group (strictest-wins); leave to keep</label>'
      + '<select id="pb-bnd-busfloor"><option value="">— leave —</option>' + floors.map((f) => opt(f, '')).join('') + '</select>'
      + '<div style="display:flex;gap:8px"><button class="pb-form-ok" id="pb-bnd-save" data-id="' + esc(n.id) + '">Save routing</button>'
      + '<button class="pb-form-cancel" id="pb-bnd-cancel">Cancel</button></div></div>';
  }

  function inspectUseCase(n) {
    const allowed = allowedFor(g.edges, n.id);
    const agents = g.nodes.filter((x) => x.kind === 'agent' && !allowed.includes(x.id.replace(/^party:/, '')));
    const ev = g.edges.find((e) => e.kind === 'egress' && e.from === n.id);
    const v = ev ? VINFO(effVerdict(byId, ev)) : null;
    let h = '<div class="pb-i-h">' + esc(n.label || n.id) + '</div>';
    h += '<div class="pb-i-row">risk <b>' + esc(n.risk || 'low') + '</b> · L' + Math.max(0, Math.min(4, n.grade || 0)) + (v ? ' · <span style="color:' + v.col + '">' + esc(v.label) + '</span>' : '') + '</div>';
    if (n.reserved && n.reserved.length) h += '<div class="pb-i-row pb-i-warn">reserved: ' + esc(n.reserved.join(', ')) + '</div>';
    if (n.oversight && n.oversight.mode) h += '<div class="pb-i-row">oversight <b>' + esc(n.oversight.mode) + '</b>'
      + (n.oversight.overseers && n.oversight.overseers.length ? ' · ' + esc(n.oversight.overseers.join(', ')) : '')
      + ' · caps L' + esc(n.oversight.grade_ceiling) + '</div>';
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

  // Route a boundary: re-version its egress connector with a new floor/group.
  // Preserve the connector's real fields from the chain — re-registering from
  // the graph node alone would blank use_cases/tags/tool_ref. Optionally set the
  // group's bus floor (the master fader) in the same gesture.
  async function saveBoundary(nodeId, floor, group, busFloor) {
    const fc = focus(); if (!fc) return;
    const cid = nodeId.replace(/^conn:/, '');
    const list = await call('workspace_workflow', { op: 'connector_list', params: { folder_context: fc } }).catch(() => null);
    const cur = ((list && list.connectors) || []).find((c) => c.connector_id === cid) || {};
    const r = await call('workspace_workflow', { op: 'connector_register', params: {
      folder_context: fc, connector_id: cid, role: cur.role || 'egress', channel: cur.channel || '',
      use_cases: cur.use_cases || [], name: cur.name || '', tags: cur.tags || [],
      floor, group, credential_ref: cur.credential_ref,
      destination_class: cur.destination_class || '', tool_ref: cur.tool_ref, actor: 'app-user',
    } }).catch((e) => ({ error: String(e && e.message || e) }));
    if (r && r.error) { doc.defaultView.alert('Routing failed: ' + r.error); return; }
    if (busFloor && group) {
      const gr = await call('workspace_workflow', { op: 'group_floor', params: { folder_context: fc, group_id: group, floor: busFloor, actor: 'app-user' } }).catch((e) => ({ error: String(e && e.message || e) }));
      if (gr && gr.error) { doc.defaultView.alert('Bus floor failed: ' + gr.error); return; }
    }
    boundaryFormFor = null;
    await load();
    await store.hydrate();
  }

  // --- events ---------------------------------------------------------------

  function wireEvents() {
    if (!root) return;
    root.querySelectorAll('.pb-node').forEach((el) => el.addEventListener('click', () => { sel = el.dataset.id; boundaryFormFor = null; paint(); }));
    root.querySelectorAll('.pb-grant').forEach((b) => b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const sel2 = root.querySelector('#pb-grant-agent');
      if (sel2 && sel2.value) grant(b.dataset.uc, sel2.value);
    }));
    root.querySelectorAll('.pb-revoke').forEach((b) => b.addEventListener('click', (ev) => { ev.stopPropagation(); revoke(b.dataset.uc, b.dataset.agent); }));
    root.querySelectorAll('.pb-bnd-open').forEach((b) => b.addEventListener('click', (ev) => { ev.stopPropagation(); boundaryFormFor = b.dataset.id; paint(); }));
    const bndCancel = root.querySelector('#pb-bnd-cancel'); if (bndCancel) bndCancel.addEventListener('click', (ev) => { ev.stopPropagation(); boundaryFormFor = null; paint(); });
    const bndSave = root.querySelector('#pb-bnd-save');
    if (bndSave) bndSave.addEventListener('click', (ev) => {
      ev.stopPropagation();
      saveBoundary(bndSave.dataset.id,
        root.querySelector('#pb-bnd-floor').value,
        (root.querySelector('#pb-bnd-group').value || '').trim(),
        root.querySelector('#pb-bnd-busfloor').value);
    });
  }

  return { mount, unmount, refresh };
}
