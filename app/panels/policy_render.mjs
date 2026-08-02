// Real DOM test for the Protections drawer (workspace_policy). Loads the
// composed page in jsdom against a running serve.py, opens the policy
// settings, and asserts: 6 discrete oversight levels (no dial), the
// privacy-lock dial offers a GOVERNED turn-off that is REJECTED without
// accepted_by+reason (no governance bypass) and writes through once both are
// given, the parties list renders, and the panel is modal. The drawer ships
// as its own pack bundle (app/src/panels/protections.js), registered through
// the panel-mount contract, so this gate loads the composed page (GET
// /classic) rather than a bare readFileSync of the shell source — a raw
// index.html would open a frame with nothing inside it.
// Usage: node policy_render.mjs <PORT> <FOLDER_CONTEXT>
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
    window.confirm = () => true;
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const snap = async () => window.tool("workspace_policy", { op: "snapshot", params: { folder_context: F } });

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openPolicySettingsPanel();
  await sleep(180);
  const sp = window.document.getElementById("protectionspanel");
  if (!sp) fail("policy panel did not open");
  if (sp.getAttribute("aria-modal") !== "true") fail("policy panel is not a modal dialog");

  // oversight = 6 discrete states (no dial), current one marked
  const lvls = [...window.document.querySelectorAll("[data-ovl]")];
  if (lvls.length !== 6) fail("expected 6 discrete oversight levels, got " + lvls.length);
  if (!lvls.some((b) => b.getAttribute("aria-pressed") === "true")) fail("no current oversight level marked");

  // privacy lock shows on, with a governed 'Turn off…'
  const off = window.document.querySelector('[data-off="lock"]');
  if (!off) fail("privacy lock not shown as on with a Turn off control");
  off.click();
  await sleep(80);
  const cfm = window.document.querySelector("#cfm_lock");
  if (!cfm) fail("governed-disable form did not appear");

  // governance gate: confirming WITHOUT accepted_by + reason must NOT disable it
  cfm.click();
  await sleep(140);
  if ((await snap()).lock_is_active !== true) fail("lock disabled without accepted_by+reason — governance bypass!");

  // with both given, it writes through
  window.document.querySelector("#acc_lock").value = "alex";
  window.document.querySelector("#rsn_lock").value = "audit test";
  window.document.querySelector("#cfm_lock").click();
  let s = null;
  for (let i = 0; i < 40; i++) { await sleep(50); s = await snap(); if (s.lock_is_active === false) break; }
  if (!s || s.lock_is_active !== false) fail("governed disable did not turn the lock off");

  // server-side gate (not just the UI): a direct disable without a reason is refused
  const bypass = await window.tool("workspace_policy", { op: "disable", params: { folder_context: F, dial: "oversight", accepted_by: "x", reason: "" } });
  const refused = !bypass || bypass.ok === false || !!bypass.error;   // schema-reject ({error}) or handler-reject ({ok:false}) — both are refusals
  if (!refused) fail("server allowed a disable without a reason — governance bypass");
  if ((await snap()).oversight_is_active !== true) fail("oversight was disabled despite the missing reason");

  // parties list shows the seeded agent
  const ps = window.document.getElementById("psparties");
  for (let i = 0; i < 20 && !(ps && /bot7/.test(ps.textContent)); i++) await sleep(50);
  if (!/bot7/.test(ps.textContent)) fail("parties list did not show the registered party");

  console.log("PASS: policy drawer — 6 discrete oversight levels; governed disable needs accepted_by+reason then writes through; parties listed; modal");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
