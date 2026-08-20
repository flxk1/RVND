// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test: the Policy map panel MAPS pasted policy text over the LIVE governance_map
// op (paste → "Map it" → grouped rules), the ask box narrows via a question over the same
// op's question path, and the universal chat routes a pasted policy over governance_chat.
// It drives the real wired buttons against the real server — the paste→map→ask a user
// actually performs — not a synthetic payload (that is what the panel-pin test already covers).
// The drawer ships as its own pack bundle (app/src/panels/map.js), registered through the
// panel-mount contract, so this gate loads the composed page (GET /classic) rather than a
// bare readFileSync of the shell source — a raw index.html would open a frame with nothing
// inside it.
// Usage: node governance_map_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
import { assertBridgeAlive } from "../harness/rvnd_gate_guards.mjs";
const PORT = process.argv[2], F = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const waitFor = async (fn, ms = 4000) => {
  for (let i = 0; i < ms / 25; i++) { if (fn()) return true; await sleep(25); } return false;
};
const dom = new JSDOM(html, { runScripts: "dangerously", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o); window.confirm = () => true;
  Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
  Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
} });
const { window } = dom; const D = window.document;
const out = () => (D.getElementById("mpout") || {}).innerHTML || "";
const POLICY = [
  "Providers of high-risk AI systems shall ensure that their systems undergo the relevant conformity assessment procedure.",
  "Deployers of high-risk AI systems shall take appropriate technical and organisational measures to use them in accordance with the instructions.",
].join("\n");

async function main() {
  if (!await waitFor(() => window._ready, 3000)) fail("app did not boot");
  await assertBridgeAlive(window, fail);
  window.S.path = F;

  // 1) open the Policy map panel — the same entry a user clicks
  if (typeof window.openMapPanel !== "function") fail("openMapPanel not exposed");
  window.openMapPanel();
  if (!D.getElementById("mappanel")) fail("map panel did not open");
  if (!D.getElementById("mptext")) fail("map panel has no policy textarea");

  // 2) paste policy, group by role, click "Map it" → the LIVE governance_map op
  D.getElementById("mptext").value = POLICY;
  D.getElementById("mpgroup").value = "role";
  D.getElementById("mpbuild").click();
  if (!await waitFor(() => /grouped by/.test(out()))) fail("map never rendered the contract summary");
  const mapped = out();
  if (/Unexpected map version/.test(mapped)) fail("panel refused the live payload version (contract drift)");
  if (!/<details/.test(mapped)) fail("no rule groups rendered — paste→map produced nothing");

  // 3) ask a question → the same op's question path narrows and echoes the inferred filter
  D.getElementById("mpask").value = "which rules need a human?";
  D.getElementById("mpaskbtn").click();
  if (!await waitFor(() => /asked:/.test(out()))) fail("ask never echoed the question→filter");

  // 4) universal chat: ONE box routes a pasted policy over governance_chat
  window.openChatPanel();
  if (!D.getElementById("chatpanel")) fail("chat panel did not open");
  D.getElementById("chatin").value = POLICY;
  D.getElementById("chatsend").click();
  if (!await waitFor(() => /rvnd ·/.test((D.getElementById("chatlog") || {}).innerHTML || "")))
    fail("chat produced no routed rvnd response");

  console.log("PASS: paste→map→ask renders live governance_map rules + question filter; chat routes a pasted policy");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
