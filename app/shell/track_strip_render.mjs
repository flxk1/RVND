// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the track channel strip — per-track selection detail in the
// Inspector (per-track-binding concept, step 2). Selecting an agent lane renders
// its strip from the live track_strip op: status word + LED, the per-grade autonomy
// ladder with the earned rung lit, the channel join with its floor word, the
// sign-off state in words, and the verdict meter. Selecting an egress connector
// renders the floor and the CABLE — arm state as glyph + word + the reference,
// with the secret value never reaching the DOM. Read-only throughout.
// Usage: node track_strip_render.mjs <PORT> <FOLDER>
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

async function stripFor(nodeId) {
  window.S.sel = nodeId; window.render();
  for (let i = 0; i < 60; i++) {
    const el = D.getElementById("trackstrip");
    if (el && !el.textContent.includes("loading track")) return el;
    await sleep(25);
  }
  return null;
}

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = A; await window.reload(); await sleep(60);

  // --- the agent lane strip ---------------------------------------------------
  if (!window.S.g.nodes.some((n) => n.id === "party:scout")) fail("seeded agent not on the patch");
  const ps = await stripFor("party:scout");
  if (!ps) fail("party strip did not render");
  const pt = ps.textContent;
  if (!pt.includes("active")) fail("status word missing: " + pt);
  // the autonomy ladder: one discrete cell per grade on the active ladder, the
  // earned rung (L2) lit. Remappable policy → derive the rung count from the
  // server's published grades, not a fixed 5.
  const tsmx = await window.tool("workspace_matrix", { op: "show", params: { folder_context: A } });
  const nG = Object.keys((tsmx && tsmx.matrix) || {}).filter((k) => /^L\d+$/.test(k)).length;
  const cells = [...ps.querySelectorAll(".fcell")];
  if (nG < 2 || cells.length !== nG) fail("ladder must have one discrete cell per grade (" + nG + "), got " + cells.length);
  const earned = cells.filter((c) => c.classList.contains("earned"));
  if (earned.length !== 1 || earned[0].textContent !== "L2") fail("earned rung should be exactly L2");
  if (!pt.includes("competences: legal")) fail("competences missing: " + pt);
  if (!pt.includes("out-llm") || !pt.includes("floor hold")) fail("channel join + floor word missing: " + pt);
  if (!pt.includes("no sign-offs waiting")) fail("sign-off state must be worded even when empty: " + pt);
  if (!pt.includes("meter:")) fail("verdict meter line missing: " + pt);

  // --- the connector (egress track) strip --------------------------------------
  if (!window.S.g.nodes.some((n) => n.id === "conn:out-llm")) fail("seeded connector not on the patch");
  const cs = await stripFor("conn:out-llm");
  if (!cs) fail("connector strip did not render");
  const ct = cs.textContent;
  if (!ct.includes("floor hold")) fail("connector floor word missing: " + ct);
  if (!ct.includes("armed")) fail("cable arm state not worded: " + ct);
  if (!ct.includes("env:STRIP_TOK")) fail("cable must show the reference: " + ct);
  if (!ct.includes("1 driver")) fail("driver count missing: " + ct);
  if (!ct.includes("meter:")) fail("connector verdict meter missing: " + ct);

  // the secret value must NEVER reach the DOM (only the reference may)
  if (D.documentElement.outerHTML.includes("STRIP-SECRET-VALUE")) fail("secret value leaked into the DOM");

  // pure lens: rendering strips wrote nothing to the chain
  const v = await window.tool("workspace_audit", { op: "verify_chain", params: { folder_context: A } });
  if (v && v.ok === false) fail("rendering the strip disturbed the chain");

  console.log("PASS: track channel strip — agent lane renders status word, the per-grade ladder with L2 earned, competences, channel floor word, worded empty sign-off state and meter; egress connector renders floor + armed cable with the reference; the secret never reaches the DOM; read-only");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
