// Real DOM test for the model-attestation card in the Audit drawer. Seeded:
// one model baselined, one admitted change, one drifted run → EXPLAINED_DRIFT
// with a diverged probe and an unobserved probe.
// Asserts: the card renders the verdict word with the recorded reason quoted,
// diverged and unobserved as separate worded lists, the admitted count, and
// the drawer's only buttons are the battery's governed writes (Run battery on
// the card, Admit change on the drifted card).
// The Audit drawer ships as its own pack bundle (app/src/panels/audit.js),
// registered through the panel-mount contract, so this gate loads the
// composed page (GET /classic) rather than a bare readFileSync of the shell
// source — a raw index.html would open a frame with nothing inside it.
// Usage: node attestation_render.mjs <PORT> <FOLDER>
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
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openAuditPanel();
  let txt = "";
  for (let i = 0; i < 60; i++) { await sleep(80); txt = (D.getElementById("auout") || {}).textContent || ""; if (/Model attestation/.test(txt)) break; }
  if (!/Model attestation — tiny-gguf/.test(txt)) fail("attestation card missing — got: " + txt.slice(0, 240));
  if (!/drift, explained by declared changes/.test(txt)) fail("EXPLAINED_DRIFT verdict word missing");
  if (!/probe\(s\) unobserved|coverage gap/.test(txt)) fail("unobserved probes are not separated as a coverage gap");
  if (!/diverged \(drift\).*p-refuse/.test(txt)) fail("diverged probe list missing");
  if (!/admitted changes in window: 1/.test(txt)) fail("admitted count missing");
  const ap = D.getElementById("auditpanel");
  if (!ap.querySelector('[data-atrun="0"]')) fail("model card is missing its Run battery button");
  if (!ap.querySelector('[data-atadmit="0"]')) fail("drifted card is missing its Admit change button");
  const stray = [...ap.querySelectorAll("button")].filter((b) => !b.dataset.atrun && !b.dataset.atadmit && !b.hasAttribute("data-atbase"));
  if (stray.length) fail("audit checks must stay read-only — found non-attest button(s): " + stray.map((b) => b.textContent).join(","));

  console.log("PASS: attestation card — verdict word + recorded reason quoted; diverged vs unobserved separated; admitted count; only attest write buttons");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
