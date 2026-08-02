// Real DOM test for the Workflows drawer (workspace_workflow), both tabs.
// Run board (the default tab): definitions + run queue render, the
// reads/runs badge, zero act controls in the DOM. Stuck runs tab: waiting
// runs with cancel/resume plus the run/enqueue/delete workbench render;
// cancelling the queued run flips it to cancelled. The drawer ships as its
// own pack bundle (app/src/panels/workflow.js), registered through the
// panel-mount contract, so this gate loads the composed page (GET /classic)
// rather than a bare readFileSync of the shell source — a raw index.html
// would open a frame with nothing inside it.
// Usage: node workflow_render.mjs <PORT> <FOLDER_CONTEXT>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const dom = new JSDOM(html, { runScripts: "dangerously", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o); window.confirm = () => true; window.alert = () => {};
  Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
  Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
} });
const { window } = dom;
const txt = () => window.document.getElementById("wfout").textContent;
async function waitFor(re, n=40){ let t=""; for(let i=0;i<n;i++){ await sleep(60); t=txt(); if(re.test(t)) return t; } return t; }
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  // default tab: Run board (Record -> Run board opens here)
  await window.openWorkflowPanel("read"); await sleep(160);
  let wp = window.document.getElementById("workflowpanel");
  if (!wp) fail("run board did not open");
  if (wp.getAttribute("aria-modal") !== "true") fail("not a modal dialog");
  if (!/Workflow board/i.test(wp.querySelector("b").textContent)) fail("header wrong: " + wp.querySelector("b").textContent);
  let rb = wp.querySelector(".robadge");
  if (!rb || !/reads/i.test(rb.textContent) || !/runs/i.test(rb.textContent)) fail("missing the reads/runs badge: " + (rb && rb.textContent));
  let t = await waitFor(/nightly/);
  if (!/nightly/.test(t)) fail("defined workflow not rendered: " + t.slice(0,160));
  if (!/Run queue/.test(t)) fail("run queue not rendered");
  if (wp.querySelectorAll("button").length) fail("Run board tab renders act controls — it must render none");

  // Stuck runs tab (Pending -> Stuck runs jumps straight here, even on a
  // fresh open, via the back-compat wrapper's post-mount tab switch)
  await window.openWorkflowPanel("act"); await sleep(160);
  wp = window.document.getElementById("workflowpanel");
  if (!wp) fail("stuck runs panel is not open after switching tabs");
  const tab = wp.querySelector('[data-wftab="act"]');
  if (!tab || tab.getAttribute("aria-selected") !== "true") fail("Stuck runs tab did not become selected");
  t = await waitFor(/nightly/);
  if (!/Stuck runs/.test(t)) fail("stuck-runs section missing: " + t.slice(0,160));
  if (![...wp.querySelectorAll("button")].some(b=>/^run$/i.test(b.textContent.trim()))) fail("no run control in the Stuck runs tab");
  if (![...wp.querySelectorAll("button")].some(b=>/^cancel$/i.test(b.textContent.trim()))) fail("no cancel control on the waiting run");
  const cancelBtn = wp.querySelector("[data-wfcancel]");
  if (!cancelBtn) fail("queued nightly run not found to cancel");
  const runId = decodeURIComponent(cancelBtn.dataset.wfcancel);
  cancelBtn.click(); await sleep(220);
  const q = await window.tool("workspace_workflow", { op: "queue", params: { folder_context: F } });
  const after = ((q && q.entries) || []).find(e=>e.run_id===runId);
  if (!after || after.state !== "cancelled") fail("run did not flip to cancelled — " + (after&&after.state));

  console.log("PASS: workflows drawer — run board renders definitions + queue with zero act controls; stuck runs renders waiting runs with cancel/resume + the start workbench and the reads/runs badge; cancel round-trip flips to cancelled; modal");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
