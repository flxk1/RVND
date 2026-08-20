// Real DOM test for the Conformity drawer (workspace_conformity, read-only).
// Opens it, asserts the six projections render, the regime selector
// re-projects, the "does not certify compliance" honesty line shows, the
// drawer is modal and READ-ONLY (no <button>). The drawer ships as its own
// pack bundle (app/src/panels/conformity.js), registered through the
// panel-mount contract, so this gate loads the composed page (GET /classic)
// rather than a bare readFileSync of the shell source — a raw index.html
// would open a frame with nothing inside it.
// Usage: node conformity_render.mjs <PORT> <FOLDER_CONTEXT>
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

  await window.openConformityPanel();
  await sleep(160);
  const cp = window.document.getElementById("conformitypanel");
  if (!cp) fail("conformity panel did not open");
  if (cp.getAttribute("aria-modal") !== "true") fail("conformity panel is not a modal dialog");
  if (!/does not certify compliance/i.test(cp.textContent)) fail("missing the 'does not certify compliance' honesty line");

  let txt = "";
  for (let i = 0; i < 40; i++) { await sleep(60); txt = window.document.getElementById("cfout").textContent; if (/Evidence pack/.test(txt)) break; }
  for (const card of ["Evidence pack", "Oversight attestation", "Trigger map", "Drift report", "Risk register", "Threat model"])
    if (!txt.includes(card)) fail("projection card missing: " + card + " — got: " + txt.slice(0, 160));
  if (/\bcompliant\b/i.test(txt)) fail("drawer asserts 'compliant' — doctrine forbids it");

  // regime selector re-projects and attributes labels (not asserts)
  const sel = cp.querySelector("#cfreg");
  if (!sel) fail("regime selector missing");
  sel.value = "eu-ai-act"; sel.dispatchEvent(new window.Event("change"));
  for (let i = 0; i < 30; i++) { await sleep(60); txt = window.document.getElementById("cfout").textContent; if (/attributed to .*eu-ai-act/i.test(txt)) break; }
  if (!/attributed to .*eu-ai-act/i.test(txt)) fail("regime change did not attribute eu-ai-act labels: " + txt.slice(-160));

  // read-only: only the ✕ close (role=button span) + the <select>; no <button> writes
  const btns = [...cp.querySelectorAll("button")];
  if (btns.length) fail("conformity drawer must be read-only — found " + btns.length + " button(s)");

  console.log("PASS: conformity drawer — 6 projections + regime attribution; no 'compliant'; read-only; modal dialog");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
