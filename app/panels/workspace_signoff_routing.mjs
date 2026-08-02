// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Routing test: requestSignoff() must send a role-QUORUM reservation to the §1.5 engine
// (role-abstract, any m qualified hands) — NOT resolve it to named signers. Drives the
// real app function over serve.py, then checks the §1.5 inbox shows the quorum approval.
// The Sign-offs inbox is registered through the panel-mount contract
// (app/src/panels/approvals.js), so this loads the composed page (GET /classic) rather
// than a bare readFileSync of the shell source — a raw index.html has no panel bundle
// to register "approvals".
// Usage: node workspace_signoff_routing.mjs <PORT> <FOLDER_CONTEXT>
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
  window.S.path = F; await window.reload(); await sleep(40);

  // sanity: the routing parse classifies a quorum as role-abstract (m>=2)
  const t1 = window.parseResvTarget("2 of {legal, finance, risk}");
  if (!(t1.m === 2 && t1.roles.length === 3)) fail("parseResvTarget quorum wrong: " + JSON.stringify(t1));
  const t2 = window.parseResvTarget("data-protection");
  if (!(t2.m === 1)) fail("parseResvTarget single-role wrong: " + JSON.stringify(t2));

  // drive the real routing: a role-quorum reservation must open a §1.5 approval
  const ok = await window.requestSignoff({ id: "uc:loan", label: "Loan decision" },
                                         "2 of {legal, finance, risk}", true);
  if (!ok) fail("requestSignoff returned false for a role quorum");

  const resolve = (rid) => window.tool("workspace_workflow",
    { op: "approval_resolve", params: { folder_context: F, request_id: rid, now: Date.now() / 1000 } });

  // #3: the reservation's declared term + elapse mode thread through to the approval
  await window.requestSignoff({ id: "uc:term", label: "Term", reservations: [
    { reserved_to: "2 of {legal, finance, risk}", duration: "30d", on_elapse: "proceed", basis_kind: "policy" }] },
    "2 of {legal, finance, risk}", true);
  const r3 = await resolve("resv:term");
  if (r3.on_elapse !== "proceed") fail("#3 on_elapse not threaded: " + JSON.stringify(r3));
  const span = r3.deadline - r3.requested_at;
  if (Math.abs(span - 2592000) > 5) fail("#3 duration 30d not applied (span=" + span + "s)");

  // #4: a law-basis reservation can never time out into action — proceed is forced to halt
  await window.requestSignoff({ id: "uc:law", label: "Law", reservations: [
    { reserved_to: "2 of {legal, finance}", duration: "7d", on_elapse: "proceed", basis_kind: "law" }] },
    "2 of {legal, finance}", true);
  const r4 = await resolve("resv:law");
  if (r4.on_elapse !== "halt") fail("#4 law basis must force halt, got: " + JSON.stringify(r4));

  await window.openApprovalsPanel();
  const t = await waitFor(/reservation approvals/i);
  if (!/reservation approvals/i.test(t)) fail("quorum reservation did not land in the §1.5 inbox: " + t.slice(0, 200));
  if (!/any of \{[^}]*finance[^}]*\}/.test(t)) fail("role set missing — it must stay role-abstract, not named: " + t.slice(0, 200));
  if (/\bo'?brien\b|named signers/i.test(t) && !/role quorum/i.test(t)) fail("a quorum was wrongly routed to named signers");
  console.log("PASS: role-quorum reservation routed to §1.5 (role-abstract, any of {legal, finance, risk}) — not named signers");
}
main().catch((e) => fail(String((e && e.stack) || e)));
