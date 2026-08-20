// Real DOM test for the Standing facts drawer (workspace_legal, read-only). Opens it,
// asserts the Subject cards section renders, the read-only + attributed-not-
// asserted honesty lines show, no percentage/dial, and a read-only modal.
// The drawer ships as its own pack bundle (app/src/panels/legal.js),
// registered through the panel-mount contract, so this gate loads the
// composed page (GET /classic) rather than a bare readFileSync of the shell
// source — a raw index.html would open a frame with nothing inside it.
// Usage: node legal_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
import { assertBridgeAlive } from "../harness/rvnd_gate_guards.mjs";
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
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openLegalPanel();
  await sleep(160);
  const gp = window.document.getElementById("legalpanel");
  if (!gp) fail("standing facts panel did not open");
  if (gp.getAttribute("aria-modal") !== "true") fail("standing facts panel is not a modal dialog");
  if (!/it never changes them/i.test(gp.textContent)) fail("missing the read-only honesty line");
  if (!/does .*not.* certify legal compliance/i.test(gp.textContent)) fail("missing the attributed-not-asserted honesty line");

  let txt = "";
  for (let i = 0; i < 40; i++) { await sleep(60); txt = window.document.getElementById("lgout").textContent; if (/Subject cards/.test(txt)) break; }
  if (!/Subject cards/.test(txt)) fail("Subject cards section missing — got: " + txt.slice(0, 200));
  if (/%/.test(txt)) fail("drawer renders a percentage — doctrine forbids a 0-1 dial/score");

  const btns = [...gp.querySelectorAll("button")];
  if (btns.length) fail("standing facts drawer must be read-only — found " + btns.length + " button(s)");

  console.log("PASS: standing facts drawer — subject-cards read; read-only; modal dialog");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
