// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the Coverage drawer's agents × tasks lens — a read-only
// view of governance_graph: agents (rows) × tasks (cols), a filled cell = the
// agent has authority to run that task, an empty cell = no authority (the
// gap). We boot the live MCP, point the app at a seeded folder, open the
// drawer, and assert the grid reads the patch correctly: authority present ->
// filled cell ("may run"), authority absent -> the gap ("no authority").
// Client renders, never decides. The drawer ships as its own pack bundle
// (app/src/panels/coverage.js), registered through the panel-mount contract,
// so this gate loads the composed page (GET /classic) rather than a bare
// readFileSync of the shell source — a raw index.html would open a frame
// with nothing inside it.
// Usage: node coverage_render.mjs <PORT> <FOLDER>
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

// aria-label of the cell at (agent row, task column), by their visible labels.
function cellAria(agentLabel, taskLabel) {
  const heads = [...D.querySelectorAll("#cvout thead th")].map((h) => h.textContent);
  const col = heads.indexOf(taskLabel);               // 0 is the empty corner
  if (col < 1) return null;
  for (const tr of D.querySelectorAll("#cvout tbody tr")) {
    if (tr.querySelector("th").textContent !== agentLabel) continue;
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
  if (cp.getAttribute("aria-modal") !== "true") fail("coverage panel is not a modal dialog");
  for (let i = 0; i < 60 && !D.querySelector("#cvout .cvtable"); i++) await sleep(25);
  if (!D.querySelector("#cvout .cvtable")) fail("Coverage grid did not render");

  // shape: two agent rows, two task columns
  const rows = [...D.querySelectorAll("#cvout tbody tr")].map((tr) => tr.querySelector("th").textContent);
  if (!(rows.includes("bot-a") && rows.includes("bot-b"))) fail("agent rows missing: " + JSON.stringify(rows));
  const heads = [...D.querySelectorAll("#cvout thead th")].map((h) => h.textContent);
  if (!(heads.includes("Task X") && heads.includes("Task Y"))) fail("task columns missing: " + JSON.stringify(heads));

  // authority reads: uc-x allows only bot-a; uc-y allows both.
  const ax = cellAria("bot-a", "Task X"); if (!ax || !ax.includes("may run")) fail("bot-a should be able to run Task X: " + ax);
  const bx = cellAria("bot-b", "Task X"); if (!bx || !bx.includes("no authority")) fail("bot-b must NOT be able to run Task X (the gap): " + bx);
  const ay = cellAria("bot-a", "Task Y"); if (!ay || !ay.includes("may run")) fail("bot-a should be able to run Task Y: " + ay);
  const by = cellAria("bot-b", "Task Y"); if (!by || !by.includes("may run")) fail("bot-b should be able to run Task Y: " + by);

  // no run yet -> the filled cells read as 'unfired', proving colour tracks the
  // server verdict (client renders it, never decides it)
  if (!ax.includes("unfired")) fail("unrun task should read unfired on a filled cell: " + ax);

  // it is a pure lens: opening it wrote nothing to the chain (verify still intact)
  const v = await window.tool("workspace_audit", { op: "verify_chain", params: { folder_context: A } });
  if (v && v.ok === false) fail("opening the lens disturbed the chain");

  // toggling the control closes it (same open/close idiom as the other panels)
  await window.openCoveragePanel();
  if (D.getElementById("coveragepanel")) fail("Coverage panel did not toggle closed");

  console.log("PASS: Coverage lens renders agents × tasks over governance_graph — authority shows as a filled 'may run' cell coloured by the server verdict, absent authority shows the gap ('no authority'); read-only (no chain writes); toggles closed");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
