// Real DOM test for the Obligations board (Pending section, read-only).
// Opens the panel over a seeded registry (one obligation ticked past its
// deadline, one with an unresolvable deadline) and asserts: severity-ordered
// bins with glosses, empty bins dimmed but present, the breach candidate row,
// the unresolved-deadline warning, per-row history drill-in, closed counts,
// and no write control anywhere (the tick lives in the Contracts panel). The
// drawer ships as its own pack bundle (app/src/panels/obligations.js),
// registered through the panel-mount contract, so this gate loads the
// composed page (GET /classic) rather than a bare readFileSync of the shell
// source — a raw index.html would open a frame with nothing inside it.
// Usage: node obligations_render.mjs <PORT> <FOLDER>
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
const D = window.document;
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openObligationsPanel();
  const ob = D.getElementById("obligationspanel");
  if (!ob) fail("obligations panel did not open");
  if (ob.getAttribute("aria-modal") !== "true") fail("obligations panel is not a modal dialog");
  if (!ob.querySelector(".robadge")) fail("no read-only badge");

  let txt = "";
  for (let i = 0; i < 60; i++) { await sleep(80); txt = (D.getElementById("oblout") || {}).textContent || ""; if (/breach candidate/.test(txt)) break; }
  // severity order: breach candidate before due before due soon before pending
  const order = ["breach candidate", "due soon", "pending"].map(s => txt.indexOf(s));
  if (order.some(i => i < 0)) fail("a severity bin is missing — got: " + txt.slice(0, 240));
  if (!(order[0] < order[1] && order[1] < order[2])) fail("bins are not in severity order");
  if (!/a person decides/.test(txt)) fail("breach-candidate gloss missing (breach is never auto-declared)");
  if (!/notify the controller/.test(txt)) fail("the breached obligation's summary does not render");
  if (!/Unresolved deadlines/.test(txt)) fail("unresolved-deadline warning missing");
  if (!/closed: satisfied/.test(txt)) fail("closed counts line missing");
  if (!/States advance only when the tick runs/.test(ob.textContent)) fail("the no-tick-here honesty line is missing");

  // empty bins are dimmed, not hidden ("due" has no rows in this seed)
  const bins = [...D.getElementById("oblout").children].filter(el => el.getAttribute("style") || "");
  const dimmed = bins.filter(el => /opacity:\s*\.45/.test(el.getAttribute("style") || ""));
  if (!dimmed.length) fail("no empty bin renders dimmed — empty bins must stay visible");

  // drill-in: the breached row expands to its recorded transitions
  const rowEl = [...ob.querySelectorAll(".oblrow")].find(r => /notify the controller/.test(r.textContent));
  if (!rowEl) fail("no clickable row for the breached obligation");
  click(rowEl);
  let hist = "";
  for (let i = 0; i < 40; i++) { await sleep(60); hist = (ob.querySelector('[data-hist="' + rowEl.dataset.oid + '"]') || {}).textContent || ""; if (/breached_candidate/.test(hist)) break; }
  if (!/breached_candidate/.test(hist)) fail("history drill-in shows no transition — got: " + hist.slice(0, 160));

  if (ob.querySelectorAll("button").length) fail("obligations board must carry no write control — found button(s)");

  console.log("PASS: obligations board — severity bins with glosses (empty dimmed, never hidden); breach candidate + unresolved deadlines surfaced; history drill-in; read-only, tick stays in Contracts");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
