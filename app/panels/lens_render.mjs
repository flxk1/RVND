// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the Spend & limits drawer (Rules > Spend & limits,
// workspace_lens). Asserts: the Rules menu entry; header + read+write badge;
// the Spend, Spend log and Precedents cards render; setting an initial cap
// (no prior) is direct; raising the cap is gated by confirm() + a required
// reason and changes server state; raising with no reason is refused before
// any confirm(); declaring a precedent is gated by confirm() and lands on
// the server shelf. The drawer ships as its own pack bundle
// (app/src/panels/lens.js), registered through the panel-mount contract, so
// this gate loads the composed page (GET /classic) rather than a bare
// readFileSync of the shell source — a raw index.html would open a frame
// with nothing inside it.
// Usage: node lens_render.mjs <PORT> <FOLDER_CONTEXT>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
let confirmCalls = [];
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    bridgeGlobals(window, PORT);
    window.fetch = (u, o) => fetch(u, o);
    window.confirm = (m) => { confirmCalls.push(String(m || "")); return true; };
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom; const D = window.document;
const call = (op, params) => window.tool("workspace_lens", { op, params: Object.assign({ folder_context: F }, params || {}) });
const set = (id, val) => { const el = D.getElementById(id); if (!el) fail("missing input #" + id); el.value = val; };
const click = (id) => { const el = D.getElementById(id); if (!el) fail("missing button #" + id); el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); };
async function waitLout(re, label) { let t = ""; for (let i = 0; i < 60; i++) { await sleep(60); t = (D.getElementById("lout") || {}).textContent || ""; if (re.test(t)) return t; } fail(label + " — got: " + t.slice(0, 200)); }
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  const rulesMi = [...D.querySelectorAll('[aria-label="Rules"] .mi .mil')];
  if (!rulesMi.some((s) => /^Spend & limits$/.test(s.textContent))) fail("Rules menu has no Spend & limits entry");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openLensPanel();
  await sleep(160);
  const lp = D.getElementById("lenspanel");
  if (!lp) fail("spend & limits panel did not open");
  if (lp.getAttribute("aria-modal") !== "true") fail("spend & limits panel is not a modal dialog");
  if (!/Spend/.test(lp.querySelector("b").textContent)) fail("header wrong: " + lp.querySelector("b").textContent);
  const badge = lp.querySelector(".robadge");
  if (!badge || !/cap|precedent/i.test(badge.textContent)) fail("missing the read+write badge");

  let txt = await waitLout(/Spend/, "spend card did not render");
  for (const c of ["Spend", "Spend log", "Precedents"]) if (!txt.includes(c)) fail("card missing: " + c);
  for (const id of ["lenscapbtn", "lenspbtn"]) if (!D.getElementById(id)) fail("write control missing: #" + id);

  // initial cap (no prior) = direct
  let c0 = confirmCalls.length;
  set("lenscap", "3");
  click("lenscapbtn");
  await waitLout(/of 3 spent/, "initial cap did not take effect");
  if (confirmCalls.length !== c0) fail("setting an initial cap must not require confirm()");
  let g = await call("budget_cap_get");
  if (Number(g.cap) !== 3) fail("server cap not set — " + JSON.stringify(g));

  // raise (loosen) = confirm + reason
  let c1 = confirmCalls.length;
  set("lenscap", "8");
  set("lenscapreason", "campaign needs more headroom");
  click("lenscapbtn");
  await waitLout(/of 8 spent/, "raised cap did not take effect");
  if (confirmCalls.length <= c1) fail("raising the cap must be gated by confirm() — none fired");
  if (!/raise/i.test(confirmCalls[confirmCalls.length - 1])) fail("confirm() must name the consequence (raise)");
  g = await call("budget_cap_get");
  if (Number(g.cap) !== 8) fail("server cap did not change on raise — " + JSON.stringify(g));

  // raise with no reason → refused
  let c2 = confirmCalls.length;
  set("lenscap", "20");
  set("lenscapreason", "");
  click("lenscapbtn");
  await sleep(150);
  g = await call("budget_cap_get");
  if (Number(g.cap) !== 8) fail("raising without a reason must be refused — cap became " + g.cap);
  if (confirmCalls.length !== c2) fail("raising without a reason must not reach confirm()");

  // declare a precedent (loosen) = confirm + rationale → lands on the shelf
  let c3 = confirmCalls.length;
  set("lenspid", "refund_under_50");
  set("lensprationale", "policy: auto-approve small refunds");
  click("lenspbtn");
  await sleep(220);
  if (confirmCalls.length <= c3) fail("declaring a precedent must be gated by confirm()");
  const pl = await call("precedent_list");
  if (!((pl.precedents || []).some((p) => (p.id || p) === "refund_under_50"))) fail("declared precedent not on the server shelf — " + JSON.stringify(pl));

  console.log("PASS: spend & limits — Rules entry; read+write badge; read cards + write controls; initial cap direct, raise=confirm+reason, declare=confirm; server state changed");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
