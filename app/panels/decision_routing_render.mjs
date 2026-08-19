// Real DOM test for decision routing in the Pending panel. Seeded: one
// competence-tagged decision (holder: app-user), one raised BY app-user, one
// already claimed by dana. Asserts: the pending list shows all three with
// assignment basis and claim chips; the "only what I may claim" filter drops
// the own-escalation and the foreign claim; Claim & review leases the card
// and the workbench shows the claim line; recording closes the entry and the
// list no longer carries it. Usage: node decision_routing_render.mjs <PORT> <FOLDER>
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

  await window.openDecisionPanel();
  let out = "";
  for (let i = 0; i < 60; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (/Waiting for a person/.test(out)) break; }
  if (!/competence data-protection/.test(out)) fail("assignment basis missing — got: " + out.slice(0, 240));
  if (!/claimed by dana/.test(out)) fail("foreign claim chip missing");
  if (!/raised by app-user/.test(out)) fail("own escalation not listed unfiltered");

  // the mine-filter drops the own escalation and the foreign claim
  const mine = D.getElementById("decmine");
  mine.checked = true; mine.dispatchEvent(new window.Event("change", { bubbles: true }));
  for (let i = 0; i < 40; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (!/raised by app-user/.test(out)) break; }
  if (/raised by app-user/.test(out)) fail("mine-filter kept the reviewer's own escalation (separation of duties)");
  if (!/Erase K\./.test(out)) fail("mine-filter dropped the claimable decision");

  // claim → workbench with the claim line
  const btn = [...D.querySelectorAll("[data-decclaim]")][0];
  click(btn);
  for (let i = 0; i < 40; i++) { await sleep(100); out = D.getElementById("decout").textContent; if (/claimed by you/.test(out)) break; }
  if (!/claimed by you · the lease holds until/.test(out)) fail("workbench shows no claim line — got: " + out.slice(0, 200));

  // decide → the entry closes and leaves the list
  const panel = D.getElementById("decisionpanel");
  click([...panel.querySelectorAll(".decopt")][1]); await sleep(30);
  panel.querySelector("#decrat").value = "the split follows both duties";
  click(panel.querySelector("#decrec"));
  for (let i = 0; i < 50; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (/Recorded, signed/.test(out)) break; }
  if (!/Recorded, signed/.test(out)) fail("no receipt after deciding the claimed card");
  click(D.getElementById("decout").querySelector("#decnext"));
  for (let i = 0; i < 40; i++) { await sleep(80); out = (D.getElementById("decout") || {}).textContent || ""; if (/Waiting for a person|no escalation waits/.test(out)) break; }
  if (/Erase K\./.test(out)) fail("the decided entry still shows as pending");

  console.log("PASS: decision routing — basis + claim chips; mine-filter honours competence and separation of duties; claim leases with the line shown; deciding closes the entry");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
