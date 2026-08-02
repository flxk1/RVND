// Real DOM test for the Policy lock-mode write (workspace_policy set_lock_mode).
// Opens the Protections drawer, asserts 3 discrete lock modes (no dial), tightening
// is direct + writes through, and loosening (step-down) requires accepted_by +
// reason before it writes through. The drawer ships as its own pack bundle
// (app/src/panels/protections.js), registered through the panel-mount
// contract, so this gate loads the composed page (GET /classic) rather than
// a bare readFileSync of the shell source — a raw index.html would open a
// frame with nothing inside it.
// Usage: node policy_write_render.mjs <PORT> <FOLDER_CONTEXT>
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
const { window } = dom;
const D = window.document;
const snap = async () => window.tool("workspace_policy", { op: "snapshot", params: { folder_context: F } });
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);
  await window.openPolicySettingsPanel(); await sleep(240);
  if (!D.getElementById("protectionspanel")) fail("protections panel did not open");
  let modes = [];
  for (let i = 0; i < 40; i++) { await sleep(60); modes = [...D.querySelectorAll('[data-lm]')]; if (modes.length) break; }
  if (modes.length !== 3) fail("expected 3 discrete lock modes, got " + modes.length);
  if ([...D.querySelectorAll('[data-lm] input[type=range]')].length) fail("lock mode must not be a dial");

  // step down to clean_room (looser than the strict default) → governed form, then confirm with acc+reason
  const toClean = D.querySelector('[data-lm="clean_room"]'); if (!toClean) fail("clean_room button missing");
  toClean.click(); await sleep(100);
  let cfm = D.querySelector("#cfm_lm"); if (!cfm) fail("governed loosen form did not appear for a step-down");
  cfm.click(); await sleep(140);
  if ((await snap()).lock_mode === "clean_room") fail("lock mode loosened without accepted_by+reason — governance bypass");
  D.querySelector("#acc_lm").value = "alex"; D.querySelector("#rsn_lm").value = "render test";
  D.querySelector("#cfm_lm").click();
  let s = null; for (let i = 0; i < 40; i++) { await sleep(50); s = await snap(); if (s.lock_mode === "clean_room") break; }
  if (!s || s.lock_mode !== "clean_room") fail("governed loosen did not set clean_room");

  // wait for the DOM reload to settle (clean_room now marked) so the tighten handler's curMode is fresh
  for (let i = 0; i < 40; i++) { await sleep(50); const cr = D.querySelector('[data-lm="clean_room"]'); if (cr && cr.getAttribute("aria-pressed") === "true") break; }
  // tighten back up = direct (no form)
  const up = D.querySelector('[data-lm="clean_room_with_algo"]'); if (!up) fail("strict mode button missing");
  up.click();
  let s2 = null; for (let i = 0; i < 40; i++) { await sleep(50); s2 = await snap(); if (s2.lock_mode === "clean_room_with_algo") break; }
  if (!s2 || s2.lock_mode !== "clean_room_with_algo") fail("tightening did not write through directly");

  console.log("PASS: policy lock-mode — 3 discrete modes; tighten direct; loosen needs accepted_by+reason then writes through");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
