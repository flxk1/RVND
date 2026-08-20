// Real DOM test for the mirror review lifecycle in the Data drawer
// (history / diff / discard). Opens the Data drawer, fills the mirror path of a
// seeded draft that has one edit revision, clicks "Show revision history", and
// asserts the revision renders from the real backend.
// This lifecycle lives inside the Data drawer's own pack bundle
// (app/src/panels/data.js), registered through the panel-mount contract, so
// this gate loads the composed page (GET /classic) rather than a bare
// readFileSync of the shell source — a raw index.html would open a frame
// with nothing inside it.
// Usage: node mirror_review_render.mjs <PORT> <FOLDER> <MIRROR_PATH>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
import { assertBridgeAlive } from "../harness/rvnd_gate_guards.mjs";
const PORT = process.argv[2], F = process.argv[3], MIR = process.argv[4];
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
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(60);

  window.openDataPanel();
  const panel = D.getElementById("datapanel");
  if (!panel) fail("Data drawer did not open");
  // wait for the async load (loadData) to actually render the controls
  for (let i = 0; i < 80 && !D.getElementById("mirhistbtn"); i++) await sleep(80);

  // the review controls must exist (history / diff / discard)
  if (!D.getElementById("mirhistbtn")) fail("no 'Show revision history' control; dtout=" + ((D.getElementById("dtout")||{}).textContent||"(none)").slice(0,200));
  if (!D.getElementById("mirdiffbtn")) fail("no 'Diff against revision' control");
  if (!D.getElementById("mirdiscbtn")) fail("no 'Discard draft' control");

  // drive the real read: fill the seeded mirror path, click history
  D.getElementById("mrvPath").value = MIR;
  D.getElementById("mirhistbtn").click();
  let out = D.getElementById("mirRev");
  for (let i = 0; i < 50 && /loading…/.test(out.textContent || ""); i++) await sleep(80);
  const t = out.textContent || "";
  if (/could not read/.test(t)) fail("history read errored: " + t.slice(0, 120));
  if (!/revision/.test(t)) fail("history did not render any revision; got: " + t.slice(0, 120));
  if (!/change_replacement|alice/.test(t)) fail("the seeded edit revision is not shown; got: " + t.slice(0, 160));

  console.log("PASS: mirror review — Data drawer history/diff/discard controls wired; revision history renders the real edit (" + t.replace(/\s+/g, " ").slice(0, 80) + ")");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
