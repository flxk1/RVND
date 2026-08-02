// Real DOM test for the Run-state transport (renamed from Transport; redesigned).
// One Play/Pause STATE toggle for the focused workspace (Running ⇄ Held) + an
// always-on REC. No Stop here (the rail master ■ All-Stop is the single global
// stop). Asserts: the toggle reflects the live run-state; clicking it holds
// (instant, tighten) and resumes (confirm-gated, loosen); REC has no off-state.
// Usage: node transport_render.mjs <PORT> <FOLDER_CONTEXT>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
let confirmReturn = true;
const dom = new JSDOM(html, { runScripts: "dangerously", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o); window.confirm = () => confirmReturn;
  Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
  Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
} });
const { window } = dom; const D = window.document;
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
const active = async () => { const r = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: F } }); const a = Array.isArray(r) ? r : (r.parties || r.rows || []); return a.filter((p) => (p.status || "active") === "active").length; };
const play = () => D.getElementById("trplay");
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(80);

  // renamed group + a single play/pause toggle; REC; NO transport Stop
  if (D.getElementById("lg-tr").textContent.trim() !== "Run state") fail("transport group not renamed to 'Run state'");
  if (!play()) fail("no run-state play/pause toggle");
  if (!D.querySelector(".trbtn.trrec")) fail("REC indicator missing");
  if (D.querySelector(".trbtn.trstop")) fail("the redundant transport Stop should be gone (rail master is the global stop)");

  // reflects live state: seeded agent is active → Running, pressed
  for (let i = 0; i < 30 && !play().classList.contains("on"); i++) { await window.updateRunState(); await sleep(40); }
  if (!play().classList.contains("on")) fail("toggle should read Running (active) for a workspace with an active agent");
  if (play().getAttribute("aria-pressed") !== "true") fail("Running state not aria-pressed");
  if (!/Running/.test(play().textContent)) fail("toggle label should say Running");

  // Running → click holds (instant, tighten; no confirm needed)
  confirmReturn = false;
  click(play());
  for (let i = 0; i < 40; i++) { await sleep(60); if (!(await active())) break; }
  if (await active()) fail("clicking the toggle while Running did not hold (suspend) the agents");
  await window.updateRunState(); await sleep(40);
  if (play().classList.contains("on") || play().getAttribute("aria-pressed") !== "false") fail("toggle should read Held after holding");
  if (!/Held/.test(play().textContent)) fail("toggle label should say Held");

  // Held → click resumes, but it LOOSENS, so a declined confirm changes nothing
  confirmReturn = false;
  click(play()); await sleep(160);
  if (await active()) fail("resume acted despite a declined confirm — loosening must be confirm-gated");
  // accepted confirm brings agents back → Running
  confirmReturn = true;
  click(play());
  for (let i = 0; i < 40; i++) { await sleep(60); if (await active()) break; }
  if (!(await active())) fail("accepted resume did not bring the agents back to active");

  // REC is always-on: opens the record, never an off-state
  const rec = D.querySelector(".trbtn.trrec");
  if (!rec.querySelector(".trdot") || !/can.t be turned off|never stops|always on/i.test((rec.getAttribute("title") || "") + rec.textContent)) fail("REC must read as always-on / can't be turned off");
  click(rec); await sleep(120);
  if (!D.getElementById("tickerstrip")) fail("REC did not open the live record");

  console.log("PASS: run-state transport — renamed 'Run state'; single Play/Pause state toggle (Running⇄Held, reflects live state); hold instant (tighten), resume confirm-gated (loosen); no redundant Stop; REC always-on opens the record");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
