// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the Egress board — "which of my tracks can act outside?"
// Seeds three egress tracks in three cable states (armed via a resolvable env ref,
// no-cable, unplugged via a dangling ref) plus one ingress connector that must NOT
// appear. Asserts: egress-only rows; the headline count; each arm state
// rendered as glyph + WORD (fail-closed states are visible, never
// silent); mode honestly reads "attested" (no broker holds a plug yet); the
// credential REFERENCE is shown but the secret value never reaches the DOM; and
// the panel toggles closed. Read-only — opening it writes nothing to the chain.
// The drawer ships as its own pack bundle (app/src/panels/egress.js),
// registered through the panel-mount contract, so this gate loads the
// composed page (GET /classic) rather than a bare readFileSync of the shell
// source — a raw index.html would open a frame with nothing inside it.
// Usage: node egress_render.mjs <PORT> <FOLDER>
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

const rowOf = (name) => [...D.querySelectorAll("#egout tbody tr")]
  .find((tr) => tr.querySelector("td b")?.textContent === name);

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");

  window.S.path = A; await window.reload(); await sleep(60);
  await window.openEgressPanel();
  for (let i = 0; i < 60 && !D.querySelector("#egout .egtable"); i++) await sleep(25);
  if (!D.querySelector("#egout .egtable")) fail("Egress board did not render");

  // egress-only rows; the ingress connector must not appear
  const names = [...D.querySelectorAll("#egout tbody td b")].map((b) => b.textContent);
  if (names.length !== 3) fail("expected 3 egress tracks, got " + JSON.stringify(names));
  if (names.includes("mail-in")) fail("ingress connector leaked onto the egress board");

  // the headline: exactly one track is armed
  const head = D.querySelector("#egout [role='status']").textContent;
  if (!/1.*of.*3.*can act outside/.test(head)) fail("headline wrong: " + head);

  // board-level LLM-egress attestation: no broker runs in the test, so the live
  // probe resolves to not-bound and the board must honestly read attested
  const llm = D.querySelector("#egout [aria-label='LLM egress enforcement']");
  if (!llm) fail("missing the board-level LLM-egress attestation line");
  if (!llm.textContent.includes("LLM egress attested")) fail("LLM egress must read attested with no broker bound: " + llm.textContent);

  // armed: glyph+word, the REFERENCE shown, honest attested mode
  const armed = rowOf("jira-out"); if (!armed) fail("armed track row missing");
  if (!armed.textContent.includes("armed")) fail("armed state not worded");
  if (!armed.textContent.includes("env:EG_TOK")) fail("armed row must show the reference");
  if (!armed.textContent.includes("◌ attested")) fail("mode must honestly read attested");
  if (!armed.textContent.includes("hold")) fail("floor word missing on armed row");

  // destination class: declared renders as its word, unset reads undeclared
  if (!armed.textContent.includes("tool_api")) fail("declared destination class missing: " + armed.textContent);
  const undecl = rowOf("mail-out"); if (!undecl) fail("mail-out row missing");
  if (!undecl.textContent.includes("undeclared")) fail("unset destination must read undeclared");

  // fail-closed states are VISIBLE, as words
  const bare = rowOf("mail-out"); if (!bare) fail("no-cable track row missing");
  if (!bare.textContent.includes("no cable — cannot reach outside")) fail("no-cable state not worded");
  const dangling = rowOf("dead-out"); if (!dangling) fail("unplugged track row missing");
  if (!dangling.textContent.includes("unplugged")) fail("unplugged state not worded");

  // the secret value must NEVER reach the DOM (only the reference may)
  if (D.documentElement.outerHTML.includes("EG-SECRET-VALUE")) fail("secret value leaked into the DOM");

  // pure lens: opening it wrote nothing to the chain
  const v = await window.tool("workspace_audit", { op: "verify_chain", params: { folder_context: A } });
  if (v && v.ok === false) fail("opening the board disturbed the chain");

  // toggle closes
  await window.openEgressPanel();
  if (D.getElementById("egresspanel")) fail("Egress board did not toggle closed");

  console.log("PASS: Egress board — egress-only tracks with floor lamp + honest attested mode; board-level LLM-egress attestation worded from the live broker probe; armed/no-cable/unplugged all worded (fail-closed visible); the reference shown, the secret never in the DOM; headline count correct; read-only; toggles closed");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
