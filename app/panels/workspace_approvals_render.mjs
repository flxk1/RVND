// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Render test for the §1.5 reservation-approval card (role quorum + temporal) in the
// Sign-offs inbox — it must appear BESIDE the named-signer contract reviews, showing the
// m-of-n meter and the role set (no identities). The drawer ships as its own pack bundle
// (app/src/panels/approvals.js), registered through the panel-mount contract, so this
// gate loads the composed page (GET /classic) rather than a bare readFileSync of the
// shell source — a raw index.html has no panel bundle to register "approvals".
// Usage: node workspace_approvals_render.mjs <PORT> <FOLDER_CONTEXT>
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
    window.confirm = () => true; window.alert = () => {};
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const txt = () => (window.document.getElementById("apout") || {}).textContent || "";
async function waitFor(re, n = 50) { let t = ""; for (let i = 0; i < n; i++){ await sleep(60); t = txt(); if (re.test(t)) return t; } return t; }
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(40);
  await window.openApprovalsPanel();
  const t = await waitFor(/reservation approvals/i);
  if (!/reservation approvals/i.test(t)) fail("§1.5 reservation-approval section missing: " + t.slice(0, 200));
  if (!/0 of 2 signed/.test(t)) fail("quorum meter (0 of 2) missing: " + t.slice(0, 200));
  if (!/any of \{[^}]*legal[^}]*\}/.test(t)) fail("role set not shown (no identities expected): " + t.slice(0, 200));
  if (!/contract sign-offs/i.test(t)) fail("named-signer reviews should coexist in the same inbox: " + t.slice(0, 200));
  console.log("PASS: §1.5 reservation approval renders beside contract reviews — role quorum meter (0 of 2), role set shown, no identities");
  // Explicit success exit (fleet convention): the composed page now carries
  // always-on chrome with a live refresh interval, so node's event loop never
  // drains on its own — without this the gate PASSES and then hangs to timeout.
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
