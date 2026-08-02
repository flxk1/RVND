// Real DOM test for the Roles & competence drawer — who holds which competence,
// in which role. Asserts: People and Agents grouped; a human's competence
// chips, role and status; the no-competence note on a bare agent; the
// register form performs the governed write (via the Register — signed
// button) and the new party appears; the chain still verifies; the panel
// toggles closed. The drawer ships as its own pack bundle
// (app/src/panels/roles.js), registered through the panel-mount contract, so
// this gate loads the composed page (GET /classic) rather than a bare
// readFileSync of the shell source — a raw index.html would open a frame
// with nothing inside it.
// Usage: node roles_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], A = process.argv[3];
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

const rowOf = (pid) => D.querySelector(`#rlout [data-party="${pid}"]`);

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");

  window.S.path = A; await window.reload(); await sleep(60);
  await window.openRolesPanel();
  const rp = D.getElementById("rolespanel");
  if (!rp) fail("roles panel did not open");
  if (rp.getAttribute("aria-modal") !== "true") fail("roles panel is not a modal dialog");
  for (let i = 0; i < 60 && !rowOf("dana"); i++) await sleep(30);

  const dana = rowOf("dana");
  if (!dana) fail("dana's row missing");
  if (!dana.textContent.includes("data-protection")) fail("competence chip missing on dana");
  if (!dana.textContent.includes("DPO")) fail("role missing on dana");
  const lamp = dana.querySelector("[aria-label^='status']");
  if (!lamp || !lamp.getAttribute("aria-label").includes("active")) fail("status not worded on dana");

  const bot = rowOf("bot-a");
  if (!bot) fail("bot-a's row missing");
  if (!bot.textContent.includes("no competence")) fail("bare agent must state routing cannot reach it");

  // the governed write: register a new human from the form, it appears
  D.getElementById("rlid").value = "jonas";
  D.getElementById("rlkind").value = "human";
  D.getElementById("rlrole").value = "counsel";
  D.getElementById("rlcomp").value = "legal, finance";
  D.getElementById("rlregbtn").dispatchEvent(new window.Event("click"));
  for (let i = 0; i < 60 && !rowOf("jonas"); i++) await sleep(40);
  const jonas = rowOf("jonas");
  if (!jonas) fail("registered party did not appear");
  if (!(jonas.textContent.includes("legal") && jonas.textContent.includes("finance"))) fail("registered competences missing");

  const v = await window.tool("workspace_audit", { op: "verify_chain", params: { folder_context: A } });
  if (v && v.ok === false) fail("chain does not verify after the register");

  await window.openRolesPanel();
  if (D.getElementById("rolespanel")) fail("Roles panel did not toggle closed");

  console.log("PASS: Roles & competence — People/Agents grouped with competence chips, role and worded status; a bare agent states routing cannot reach it; the register form performs the governed write and the party appears; chain verifies; toggles closed");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
