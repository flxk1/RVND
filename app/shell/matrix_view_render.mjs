// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the MATRIX canvas view — the coverage lens as a third
// view-toggle state beside PATCH and ARRANGE. Asserts: the toggle button
// exists; switching to MATRIX fills the stage container with the kind x risk
// grid (same projection the Coverage panel reads); the view's own preset
// selector switches to task x role; toggling back to PATCH hides the view;
// read-only (no chain writes).
// Usage: node matrix_view_render.mjs <PORT> <FOLDER>
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

function cellAria(row, col) {
  const heads = [...D.querySelectorAll("#mxvout thead th")].map((h) => h.textContent);
  const c = heads.indexOf(col);
  if (c < 1) return null;
  for (const tr of D.querySelectorAll("#mxvout tbody tr")) {
    if (tr.querySelector("th").textContent !== row) continue;
    const cell = tr.querySelectorAll("td .mxcell")[c - 1];
    return cell ? cell.getAttribute("aria-label") : null;
  }
  return null;
}

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  const btn = D.querySelector('.viewtog button[data-view="matrix"]');
  if (!btn) fail("missing MATRIX button on the view toggle");

  window.S.path = A; await window.reload(); await sleep(60);
  window.setView("matrix");
  for (let i = 0; i < 60 && !D.querySelector("#mxvout .cvtable"); i++) await sleep(30);
  const host = D.getElementById("matrixview");
  if (!host.classList.contains("show")) fail("matrix view not shown after setView");
  if (!D.querySelector("#mxvout .cvtable")) fail("matrix grid did not render");
  if (btn.getAttribute("aria-checked") !== "true") fail("MATRIX toggle not aria-checked");

  // default lens is kind x risk: kinds as rows, bands as columns
  const rows = [...D.querySelectorAll("#mxvout tbody tr")].map((tr) => tr.querySelector("th").textContent);
  if (!(rows.includes("billing") && rows.includes("outreach"))) fail("kind rows missing: " + JSON.stringify(rows));
  const bh = cellAria("billing", "high");
  if (!bh || !bh.includes("billing at high")) fail("billing/high cell not projected: " + bh);

  // the view's own preset selector switches the lens
  const sel = D.getElementById("mxvpreset");
  if (!sel) fail("missing view preset selector");
  sel.value = "task_role";
  await window.renderMatrixView();
  for (let i = 0; i < 60; i++) { await sleep(30);
    const hh = [...D.querySelectorAll("#mxvout thead th")].map((h) => h.textContent);
    if (hh.includes("data-protection")) break; }
  const cov = cellAria("uc-bill", "data-protection");
  if (!cov || !cov.includes("covered")) fail("task x role lens did not render in the view: " + cov);

  // zoom level two: activating a row header narrows the field to that row's
  // strip, with the band detail and its source refs spelled out
  window.document.getElementById("mxvpreset").value = "kind_risk";
  await window.mxvZoomOut(true);
  for (let i = 0; i < 60; i++) { await sleep(30);
    const rr = [...D.querySelectorAll("#mxvout tbody tr")].length;
    if (rr >= 2) break; }
  const billingTh = [...D.querySelectorAll("#mxvout th.mxrowh")].find((t) => t.title === "billing");
  if (!billingTh) fail("billing row header missing");
  if (billingTh.getAttribute("role") !== "button") fail("row header is not a control");
  billingTh.click();
  for (let i = 0; i < 60; i++) { await sleep(30);
    if (D.querySelectorAll("#mxvout tbody tr").length === 1) break; }
  if (D.querySelectorAll("#mxvout tbody tr").length !== 1) fail("strip did not narrow to one row");
  const detail = D.querySelector("#mxvout [aria-label='strip detail']");
  if (!detail) fail("strip detail missing");
  if (!detail.textContent.includes("uc-bill")) fail("strip detail must name the source refs: " + detail.textContent);
  const back = D.getElementById("mxvback");
  if (!back) fail("missing the way back to the field");
  back.click();
  for (let i = 0; i < 60; i++) { await sleep(30);
    if (D.querySelectorAll("#mxvout tbody tr").length >= 2) break; }
  if (D.querySelectorAll("#mxvout tbody tr").length < 2) fail("back did not restore the field");

  // the editable preset: a granted cell revokes (tighten-only); an ungranted
  // cell is inert — granting is never a click
  window.document.getElementById("mxvpreset").value = "task_agent";
  await window.renderMatrixView();
  for (let i = 0; i < 60 && !D.querySelector("#mxvout .mxcell.mxedit"); i++) await sleep(30);
  const grant = [...D.querySelectorAll("#mxvout .mxcell.mxedit")]
    .find((el) => el.dataset.uc === "uc-out" && el.dataset.agent === "bot-a");
  if (!grant) fail("granted uc-out/bot-a cell missing or not editable");
  if (grant.getAttribute("role") !== "button") fail("editable cell is not a control");
  grant.click();
  for (let i = 0; i < 80; i++) { await sleep(40);
    const again = [...D.querySelectorAll("#mxvout .mxcell.mxedit")]
      .find((el) => el.dataset.uc === "uc-out" && el.dataset.agent === "bot-a");
    if (!again) break; }
  const still = [...D.querySelectorAll("#mxvout .mxcell.mxedit")]
    .find((el) => el.dataset.uc === "uc-out" && el.dataset.agent === "bot-a");
  if (still) fail("revoked grant still renders as editable/granted");
  // the write is signed: the chain must still verify after the revoke
  const v2 = await window.tool("workspace_audit", { op: "verify_chain", params: { folder_context: A } });
  if (v2 && v2.ok === false) fail("chain does not verify after the revoke");

  // toggling back to PATCH hides the view
  window.setView("patch");
  if (host.classList.contains("show")) fail("matrix view still shown after switching to patch");

  // pure lens: the view wrote nothing to the chain
  const v = await window.tool("workspace_audit", { op: "verify_chain", params: { folder_context: A } });
  if (v && v.ok === false) fail("the matrix view disturbed the chain");

  console.log("PASS: MATRIX canvas view — third toggle state renders the coverage grid full-stage (kind × risk default, task × role via its own preset selector); a row header zooms to its strip with refs spelled out and back restores the field; the task × agent preset revokes a grant tighten-only with the chain still verifying; toggling away hides it");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
