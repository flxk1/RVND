// Real DOM test: an EMPTY real workspace must show EMPTY — never the demo patch.
// The canvas, the Check panel and the Inspector must all agree with the workspace's
// real (empty) state; the demo 'Loan decision' only appears when there is NO
// workspace open at all.
// Usage: node empty_workspace_render.mjs <PORT> <EMPTY_FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
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
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");

  // focus a real but EMPTY workspace
  window.S.path = F; await window.reload(); await sleep(120);
  if (window.S.g && window.S.g._sample) fail("an empty real workspace fell back to the DEMO patch");
  if ((window.S.g.nodes || []).some((n) => n.kind === "use_case" || n.kind === "agent" || n.kind === "human")) fail("empty workspace shows agents/tasks it doesn't have (demo leak)");
  // scope to the RENDERED UI (canvas + right panel), not the inline <script> source
  const ui = (D.getElementById("stage").textContent || "") + (D.getElementById("panel").textContent || "");
  if (/Loan decision/.test(ui)) fail("the demo 'Loan decision' leaked into an empty real workspace (canvas/Check/Inspector disagree with the empty workspace)");
  if (/required by law/i.test(D.getElementById("panel").textContent || "")) fail("Check panel shows a demo 'reserved by law' verdict while the workspace is empty");
  // the canvas shows the honest empty-patch hint
  if (!D.getElementById("stagehint")) fail("empty workspace shows no empty-patch hint");

  // and the demo IS the right thing only when there is NO workspace at all
  window.S.path = null; await window.reload(); await sleep(120);
  if (!(window.S.g && window.S.g._sample)) fail("with no workspace open, the demo patch should be shown (first-run legibility)");

  console.log("PASS: empty workspace shows EMPTY (no demo leak; canvas/Check/Inspector agree); the demo appears only when no workspace is open");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
