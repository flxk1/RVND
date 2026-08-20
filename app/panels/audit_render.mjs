// Real DOM test for the Audit drawer (workspace_audit checks). Opens it,
// asserts verify_chain renders "intact", discipline shows, the drawer is
// modal, and the checks stay read-only — the only write controls are the
// attestation battery's governed ops (workspace_model). The drawer ships as
// its own pack bundle (app/src/panels/audit.js), registered through the
// panel-mount contract, so this gate loads the composed page (GET /classic)
// rather than a bare readFileSync of the shell source — a raw index.html
// would open a frame with nothing inside it.
// Usage: node audit_render.mjs <PORT> <FOLDER_CONTEXT>
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

  await window.openAuditPanel();
  await sleep(160);
  const ap = window.document.getElementById("auditpanel");
  if (!ap) fail("audit panel did not open");
  if (ap.getAttribute("aria-modal") !== "true") fail("audit panel is not a modal dialog");

  let txt = "";
  for (let i = 0; i < 40; i++) { await sleep(60); txt = window.document.getElementById("auout").textContent; if (/Signed record/.test(txt)) break; }
  if (!/Signed record intact/.test(txt)) fail("verify_chain 'intact' card not rendered: " + txt.slice(0, 140));
  if (!/Discipline/.test(txt)) fail("discipline card missing");
  if (!/overrides/i.test(txt)) fail("overrides card missing");

  // the checks stay read-only: the only <button>s are the attestation battery's
  // governed writes (here: the Baseline affordance on the no-battery card)
  const btns = [...ap.querySelectorAll("button")];
  const stray = btns.filter((b) => !b.hasAttribute("data-atbase") && !b.dataset.atrun && !b.dataset.atadmit);
  if (stray.length) fail("audit checks must stay read-only — found non-attest button(s): " + stray.map((b) => b.textContent).join(","));
  if (!ap.querySelector("[data-atbase]")) fail("no-battery card is missing its Baseline affordance");

  console.log("PASS: audit drawer — verify_chain intact + discipline + overrides; checks read-only, attest writes only; modal dialog");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
