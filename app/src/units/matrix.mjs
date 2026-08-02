// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Matrix — the coverage lens, over the shared store (units/state.mjs). Same
// patch as Build, projected as a rows x cols grid (workspace_workflow
// coverage_matrix) so absence and over-reach are visible as a shape, not just
// a wire you didn't draw: kind x risk (where autonomy is weak in the
// high-risk band), task x role (a reservation no one can discharge), task x
// agent (who may run what, and where authority is too wide — the border
// grid). The task x agent lens is the only editable one, and only
// tighten-only: revoking a grant is workspace_workflow authority_revoke, the
// same signed write Build's inspector uses. The server still decides every
// verdict; this only projects it as a grid.

import { VINFO } from './verdict.mjs';

const EXTRA_COL = { none: '#3a3f4a', gap: '#a8332b', covered: '#5aa886' };

export function createMatrix(store, call, doc) {
  const esc = (s) => { const e = doc.createElement('div'); e.textContent = String(s == null ? '' : s); return e.innerHTML; };

  let root = null;
  let unsub = null;
  let presetList = null;   // [{preset, question}]
  let preset = 'kind_risk';
  let gapsOnly = false;
  let mx = null;            // last coverage_matrix result
  let selCell = null;       // {row, col} of the selected cell

  function focus() { return store.getState().activeWorkspace || ''; }

  async function load() {
    const fc = focus();
    if (!fc) { mx = null; presetList = null; paint(); return; }
    if (!presetList) {
      const pl = await call('workspace_workflow', { op: 'coverage_matrix', params: { folder_context: fc, preset: 'list' } }).catch(() => null);
      presetList = (pl && Array.isArray(pl.presets)) ? pl.presets : [{ preset: 'kind_risk', question: '' }];
    }
    mx = await call('workspace_workflow', { op: 'coverage_matrix', params: { folder_context: fc, preset, gaps_only: gapsOnly } }).catch(() => null);
    paint();
  }

  function mount(container) { root = container; unsub = store.subscribe(() => load()); load(); }
  function unmount() { if (unsub) unsub(); unsub = null; root = null; }
  function refresh() { load(); }

  function paint() {
    if (!root) return;
    if (!focus()) { root.innerHTML = '<div class="mx-empty">open a workspace to see its coverage</div>'; return; }
    if (!mx) { root.innerHTML = '<div class="mx-empty">loading…</div>'; return; }
    if (mx.error) { root.innerHTML = '<div class="mx-empty">' + esc(mx.error) + '</div>'; return; }

    root.innerHTML =
      '<div class="mx-toolbar">'
      + '<select id="mx-preset">' + (presetList || []).map((p) => '<option value="' + esc(p.preset) + '"' + (p.preset === preset ? ' selected' : '') + '>' + esc(p.preset) + '</option>').join('') + '</select>'
      + '<label class="mx-gaps"><input type="checkbox" id="mx-gaps"' + (gapsOnly ? ' checked' : '') + '> gaps only</label>'
      + '<span class="mx-hint">' + (mx.findings ? mx.findings + ' finding' + (mx.findings === 1 ? '' : 's') : 'no findings') + '</span>'
      + '</div>'
      + '<div class="mx-q">' + esc(questionFor(preset)) + '</div>'
      + (mx.empty ? '<div class="mx-empty">nothing to project yet — wire a task in Build</div>' : gridHtml())
      + '<div class="mx-inspect">' + inspectHtml() + '</div>';
    wireEvents();
  }

  function questionFor(p) {
    const hit = (presetList || []).find((x) => x.preset === p);
    return (hit && hit.question) || '';
  }

  function colorFor(cell) {
    if (cell.verdict in EXTRA_COL) return EXTRA_COL[cell.verdict];
    if (cell.count === 0) return '#3a3f4a';
    return VINFO(cell.verdict).col;
  }

  function gridHtml() {
    let h = '<div class="mx-grid-wrap"><table class="mx-tbl"><tr><th></th>' + mx.cols.map((c) => '<th>' + esc(c) + '</th>').join('') + '</tr>';
    mx.rows.forEach((r, ri) => {
      h += '<tr><th class="mx-row-h">' + esc(r) + '</th>' + mx.cols.map((c, ci) => {
        const cell = mx.cells[ri][ci];
        const sel = selCell && selCell.ri === ri && selCell.ci === ci ? ' sel' : '';
        return '<td class="mx-cell' + (cell.finding ? ' mx-finding' : '') + sel + '" data-ri="' + ri + '" data-ci="' + ci + '" style="background:' + colorFor(cell) + '" title="' + escA(cell.row + ' × ' + cell.col) + '">' + esc(cell.letter) + (cell.count > 1 ? '<span class="mx-count">' + cell.count + '</span>' : '') + '</td>';
      }).join('') + '</tr>';
    });
    h += '</table></div>';
    return h;
  }
  function escA(s) { return String(s == null ? '' : s).replace(/"/g, '&quot;'); }

  function inspectHtml() {
    if (!selCell || !mx) return '<div class="mx-empty">select a cell to see what’s in it</div>';
    const cell = mx.cells[selCell.ri][selCell.ci];
    let h = '<div class="mx-i-h">' + esc(cell.row) + ' × ' + esc(cell.col) + '</div>';
    h += '<div class="mx-i-row">verdict <b>' + esc(cell.verdict) + '</b> · ' + cell.count + ' task' + (cell.count === 1 ? '' : 's') + '</div>';
    if (cell.why) h += '<div class="mx-i-row mx-i-warn">' + esc(cell.why) + '</div>';
    if ((cell.refs || []).length) h += '<div class="mx-i-row">' + cell.refs.map((r) => esc(r.label || r.id)).join(', ') + '</div>';
    if (cell.editable && cell.count > 0 && cell.use_case_id && cell.agent_id) {
      h += '<button class="mx-revoke" data-uc="' + esc(cell.use_case_id) + '" data-agent="' + esc(cell.agent_id) + '">Revoke authority</button>';
    }
    return h;
  }

  async function revoke(ucId, agentId) {
    if (!doc.defaultView.confirm('Revoke authority: ' + agentId + ' → ' + ucId + '?\nThis removes a signed grant and is recorded.')) return;
    await call('workspace_workflow', { op: 'authority_revoke', params: { folder_context: focus(), use_case_id: ucId, agent_id: agentId, actor: 'app-user' } }).catch(() => null);
    selCell = null;
    await load();
    await store.hydrate();
  }

  function wireEvents() {
    if (!root) return;
    const ps = root.querySelector('#mx-preset');
    if (ps) ps.addEventListener('change', () => { preset = ps.value; selCell = null; load(); });
    const gp = root.querySelector('#mx-gaps');
    if (gp) gp.addEventListener('change', () => { gapsOnly = gp.checked; selCell = null; load(); });
    root.querySelectorAll('.mx-cell').forEach((td) => td.addEventListener('click', () => { selCell = { ri: +td.dataset.ri, ci: +td.dataset.ci }; paint(); }));
    root.querySelectorAll('.mx-revoke').forEach((b) => b.addEventListener('click', (ev) => { ev.stopPropagation(); revoke(b.dataset.uc, b.dataset.agent); }));
  }

  return { mount, unmount, refresh };
}
