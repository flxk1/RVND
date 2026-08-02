// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the Coverage drawer's Kind × risk and Task × role
// lenses — the coverage panel's other two presets, backed by the server's
// coverage_matrix projection. Rows are the kinds present (issue_type) or the
// reserved tasks, columns the risk bands or the competences; each cell is
// the strictest verdict for that band. We boot the live MCP, open the
// Coverage drawer, switch the preset, and assert the grid reads the patch:
// the kinds/tasks appear as rows, the bands/competences as columns, and a
// populated cell names its band/coverage and count. The drawer ships as its
// own pack bundle (app/src/panels/coverage.js), registered through the
// panel-mount contract, so this gate loads the composed page (GET /classic)
// rather than a bare readFileSync of the shell source — a raw index.html
// would open a frame with nothing inside it.
// Usage: node coverage_matrix_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], A = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const dom = new JSDOM(html, { runScripts: "dangerously", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o); window.confirm = () => true;
  Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
  Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
} });
const { window } = dom; const D = window.document;

// aria-label of the cell at (kind row, risk column), by their visible labels.
function cellAria(kindLabel, riskLabel) {
  const heads = [...D.querySelectorAll("#cvout thead th")].map((h) => h.textContent);
  const col = heads.indexOf(riskLabel);               // 0 is the empty corner
  if (col < 1) return null;
  for (const tr of D.querySelectorAll("#cvout tbody tr")) {
    if (tr.querySelector("th").textContent !== kindLabel) continue;
    const cell = tr.querySelectorAll("td .mxcell")[col - 1];
    return cell ? cell.getAttribute("aria-label") : null;
  }
  return null;
}

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");

  window.S.path = A; await window.reload(); await sleep(60);
  await window.openCoveragePanel();
  const cp = D.getElementById("coveragepanel");
  if (!cp) fail("coverage panel did not open");
  for (let i = 0; i < 60 && !D.querySelector("#cvout .cvtable"); i++) await sleep(25);
  if (!D.querySelector("#cvout .cvtable")) fail("coverage grid did not render");

  // switch the lens preset to kind x risk (the server-backed projection)
  const sel = D.getElementById("cvpreset");
  if (!sel) fail("missing lens preset selector");
  sel.value = "kind_risk";
  sel.dispatchEvent(new window.Event("change"));
  for (let i = 0; i < 60; i++) { await sleep(30);
    const heads = [...D.querySelectorAll("#cvout thead th")].map((h) => h.textContent);
    if (heads.includes("high") && heads.includes("low")) break; }

  const heads = [...D.querySelectorAll("#cvout thead th")].map((h) => h.textContent);
  if (!(heads.includes("low") && heads.includes("high"))) fail("risk band columns missing: " + JSON.stringify(heads));
  const rows = [...D.querySelectorAll("#cvout tbody tr")].map((tr) => tr.querySelector("th").textContent);
  if (!(rows.includes("billing") && rows.includes("outreach"))) fail("kind rows missing: " + JSON.stringify(rows));

  // the billing use case is high-risk: its cell must name the band and its count
  const bh = cellAria("billing", "high");
  if (!bh || !bh.includes("billing at high")) fail("billing/high cell not projected: " + bh);
  if (!bh.includes("1 use case")) fail("billing/high cell should count its one use case: " + bh);
  // billing has nothing at low risk -> the empty band reads 'none'
  const bl = cellAria("billing", "low");
  if (!bl || !bl.includes("none")) fail("billing/low should be an empty band: " + bl);

  // switch to the task x role preset: reserved acts vs the competent roster
  sel.value = "task_role";
  sel.dispatchEvent(new window.Event("change"));
  for (let i = 0; i < 60; i++) { await sleep(30);
    const hh = [...D.querySelectorAll("#cvout thead th")].map((h) => h.textContent);
    if (hh.includes("data-protection")) break; }
  const rheads = [...D.querySelectorAll("#cvout thead th")].map((h) => h.textContent);
  if (!(rheads.includes("data-protection") && rheads.includes("finance"))) fail("competence columns missing: " + JSON.stringify(rheads));
  const rrows = [...D.querySelectorAll("#cvout tbody tr")].map((tr) => tr.querySelector("th").textContent);
  if (!(rrows.includes("uc-bill") && rrows.includes("uc-out"))) fail("reserved-task rows missing: " + JSON.stringify(rrows));
  // uc-bill reserves to data-protection, which the DPO holds -> covered
  const cov = cellAria("uc-bill", "data-protection");
  if (!cov || !cov.includes("covered")) fail("uc-bill/data-protection should be covered: " + cov);
  if (!cov.includes("1 approver")) fail("covered cell should count its approver: " + cov);
  // uc-out reserves to finance, which nobody holds -> a fail-closed finding
  const gap = cellAria("uc-out", "finance");
  if (!gap || !gap.includes("finding")) fail("uc-out/finance should be a fail-closed finding: " + gap);
  // uc-bill does not reserve to finance -> that band reads none
  const nb = cellAria("uc-bill", "finance");
  if (!nb || !nb.includes("none")) fail("uc-bill/finance should read none: " + nb);

  // pure lens: switching presets wrote nothing to the chain
  const v = await window.tool("workspace_audit", { op: "verify_chain", params: { folder_context: A } });
  if (v && v.ok === false) fail("opening the lens disturbed the chain");

  await window.openCoveragePanel();
  if (D.getElementById("coveragepanel")) fail("Coverage panel did not toggle closed");

  console.log("PASS: coverage_matrix presets render — kind × risk (kinds × bands, count per cell, empty band 'none') and task × role (reserved tasks × competences, covered vs fail-closed gap finding); read-only; toggles closed");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
