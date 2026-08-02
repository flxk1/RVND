// Real DOM test for the Privacy-lock backend setup CTA. With no lock config on
// disk the drawer shows a "not set up" card whose Set up flow runs the
// onboarding wizard headlessly (empty backend = accept the recommendation),
// renders the smoke-test outcome + transcript, and flips to the configured
// card on refresh. The drawer ships as its own pack bundle
// (app/src/panels/lock.js), registered through the panel-mount contract, so
// this gate loads the composed page (GET /classic) rather than a bare
// readFileSync of the shell source — a raw index.html would open a frame
// with nothing inside it. Usage: node lock_setup_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    bridgeGlobals(window, PORT);
    window.fetch = (u, o) => fetch(u, o);
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const D = window.document;
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openLockPanel();
  let out = "";
  for (let i = 0; i < 60; i++) { await sleep(80); out = (D.getElementById("lkout") || {}).textContent || ""; if (/not set up/i.test(out)) break; }
  if (!/Semantic scan backend — not set up/.test(out)) fail("unconfigured backend shows no CTA card — got: " + out.slice(0, 200));
  if (!D.getElementById("lksetupcta")) fail("no Set up control in the CTA card");
  if (!/deterministic gates run regardless/i.test(out)) fail("CTA card does not state what still runs without a backend");

  // run the wizard with the recommendation accepted (empty backend spec)
  click(D.getElementById("lksetupbtn"));
  let res = "";
  for (let i = 0; i < 80; i++) { await sleep(120); res = (D.getElementById("lksuout") || {}).textContent || ""; if (/Setup complete|refused/i.test(res)) break; }
  if (/refused/i.test(res)) fail("setup was refused: " + res.slice(0, 200));
  if (!/Setup complete/.test(res)) fail("setup did not complete — got: " + res.slice(0, 200));
  if (!/backend/i.test(res) || !/smoke test/i.test(res)) fail("result card misses backend/smoke-test outcome: " + res.slice(0, 200));

  // the drawer refreshes to the configured card
  for (let i = 0; i < 40; i++) { await sleep(120); out = (D.getElementById("lkout") || {}).textContent || ""; if (/configured/.test(out) && !/not set up/i.test(out)) break; }
  if (!/Semantic scan backend/.test(out) || !/configured/.test(out)) fail("drawer did not flip to the configured card — got: " + out.slice(0, 200));
  if (/not set up/i.test(out)) fail("CTA card still shown after successful setup");

  console.log("PASS: lock setup CTA — unconfigured card + wizard run (recommendation accepted), smoke-test outcome + transcript rendered, drawer flips to configured");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
