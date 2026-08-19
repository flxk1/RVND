// Real DOM test for co-decision panel state in the console. Seeded: one
// pending panel decision (3 seats, m_concordant m=2) with one seat already
// recorded. Asserts: the pending row shows the sealed panel badge (1/3) and
// a seat-claim button; claiming a seat renders the workbench co-decision
// line with counts and the seal wording; NOTHING anywhere carries the
// recorded seat's rationale.
// Usage: node decision_panel_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
import { assertBridgeAlive } from "../harness/rvnd_gate_guards.mjs";
const PORT = process.argv[2], F = process.argv[3];
const SECRET = "my private seat grounds";
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

  await window.openDecisionPanel();
  let out = "";
  for (let i = 0; i < 60; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (/panel 1\/3/.test(out)) break; }
  if (!/panel 1\/3/.test(out)) fail("pending row missing the panel badge — got: " + out.slice(0, 200));
  if (out.includes(SECRET)) fail("a sealed seat rationale leaked into the pending list");
  const btn = [...D.querySelectorAll("[data-decclaim]")][0];
  if (!/Claim a seat/.test(btn.textContent)) fail("seat-claim button wording missing");

  click(btn);
  for (let i = 0; i < 40; i++) { await sleep(100); out = D.getElementById("decout").textContent; if (/co-decision/.test(out)) break; }
  if (!/co-decision — 1 of 3 seats recorded/.test(out)) fail("workbench co-decision line missing — got: " + out.slice(0, 220));
  if (!/sealed until the panel resolves/.test(out)) fail("the seal is not stated");
  if (out.includes(SECRET)) fail("a sealed seat rationale leaked into the workbench");

  console.log("PASS: co-decision panel — sealed badge in the list, seat claim, workbench counts with the seal stated, no rationale leak");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
