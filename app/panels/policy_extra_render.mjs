// Real DOM test for the Protections panel exposes the jurisdiction-pack
// stack (juris_packs) and delegate-signing (delegate_signing) — two workspace_policy
// governance ops with no prior UI path. Sets a pack stack and delegates signing,
// asserting each writes through. The drawer ships as its own pack bundle
// (app/src/panels/protections.js), registered through the panel-mount
// contract, so this gate loads the composed page (GET /classic) rather than
// a bare readFileSync of the shell source — a raw index.html would open a
// frame with nothing inside it.
// Usage: node policy_extra_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
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
const txt = () => ((D.getElementById("psout") || {}).textContent || "");
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(60);

  window.openPolicySettingsPanel();
  for (let i = 0; i < 60 && !/Jurisdiction packs/.test(txt()); i++) await sleep(80);
  if (!/Jurisdiction packs/.test(txt())) fail("Protections panel has no Jurisdiction packs section");
  if (!/Delegate signing/.test(txt())) fail("Protections panel has no Delegate signing section");

  // set a jurisdiction-pack stack (valid reference packs)
  D.getElementById("jpStack").value = "eu-base, de-overlay";
  D.querySelector('[data-jpset]').click();
  for (let i = 0; i < 60 && !/current:.*eu-base/.test(txt()); i++) await sleep(80);
  if (!/current:.*eu-base/.test(txt())) fail("jurisdiction packs not persisted/shown after set; got: " + txt().replace(/\s+/g, " ").slice(0, 200));

  // delegate signing from boss → deputy (both seeded active humans). Success is
  // announced to the aria-live region only when the op returns without error.
  D.getElementById("dsFrom").value = "boss";
  D.getElementById("dsTo").value = "deputy";
  D.querySelector('[data-dsdel]').click();
  const live = () => ((D.getElementById("srlive") || {}).textContent || "");
  for (let i = 0; i < 60 && !/delegated|could not delegate/i.test(live()); i++) await sleep(80);
  if (/could not delegate/i.test(live())) fail("delegate_signing was refused: " + live());
  if (!/Signing delegated/i.test(live())) fail("delegate_signing did not confirm; live=" + live().slice(0, 120));

  console.log("PASS: — Protections panel sets the jurisdiction-pack stack and delegates signing authority; both workspace_policy ops write through");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
