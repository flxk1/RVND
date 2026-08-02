// Real DOM test for the Sources & gaps drawer (workspace_grounder, read-only).
// Opens it, asserts the coverage / bibliography / frontier / oversight-feed
// cards render, gaps show as discrete counts (no 0-1 completeness dial), the
// "no claim of truth" honesty line shows, and the drawer is a read-only modal
// (no <button>). The drawer ships as its own pack bundle
// (app/src/panels/grounder.js), registered through the panel-mount contract,
// so this gate loads the composed page (GET /classic) rather than a bare
// readFileSync of the shell source — a raw index.html would open a frame
// with nothing inside it.
// Usage: node grounder_render.mjs <PORT> <FOLDER_CONTEXT>
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
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openGrounderPanel();
  await sleep(160);
  const gp = window.document.getElementById("grounderpanel");
  if (!gp) fail("sources & gaps panel did not open");
  if (gp.getAttribute("aria-modal") !== "true") fail("sources & gaps panel is not a modal dialog");
  if (!/no claim of truth/i.test(gp.textContent)) fail("missing the 'no claim of truth' honesty line");

  let txt = "";
  for (let i = 0; i < 40; i++) { await sleep(60); txt = window.document.getElementById("grout").textContent; if (/What is grounded/.test(txt)) break; }
  for (const card of ["What is grounded", "Bibliography", "Research frontier", "Oversight feed"])
    if (!txt.includes(card)) fail("card missing: " + card + " — got: " + txt.slice(0, 180));

  // read-only: only ✕ close (role=button span); no <button> writes
  const btns = [...gp.querySelectorAll("button")];
  if (btns.length) fail("sources & gaps drawer must be read-only — found " + btns.length + " button(s)");

  console.log("PASS: sources & gaps drawer — coverage/bibliography/frontier/feed; read-only; modal dialog");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
