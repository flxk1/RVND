// Real DOM test for the FEDERATED verdict in the Check panel. A tool channel
// records a deny while local is permit; Check must show a federated row with the
// joined strictest-wins verdict (deny) and a ⚠ disagreement badge.
// Usage: node federated_verdict_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
import { assertBridgeAlive } from "../harness/rvnd_gate_guards.mjs";
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
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(80);

  // federatedCheck is async (connector_list + federated_decision per uc); wait for it
  let host = null;
  for (let i = 0; i < 60; i++) { host = D.getElementById("fedcheck"); if (host && /federated/.test(host.textContent)) break; await sleep(80); }
  if (!host || !/federated/.test(host.textContent)) fail("Check panel shows no federated verdict row");
  const t = host.textContent;
  if (!/score/.test(t)) fail("the federated use case 'score' is not shown in Check");
  if (!/deny/.test(t)) fail("a lone tool deny did not drive the joined Check verdict to deny");
  if (!/disagreement/.test(t)) fail("local-permit vs tool-deny disagreement not surfaced in Check");
  if (!/strictest-wins/.test(t)) fail("the join rule is not stated");

  console.log("PASS: federated verdict — Check panel shows the joined strictest-wins verdict (local + tool channel → deny) with a ⚠ disagreement badge");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
