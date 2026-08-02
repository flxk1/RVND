// Real DOM render test for the Patch canvas (the #stage governance patchbay).
// Loads the actual index.html in jsdom, points its HTTP bridge at a running
// serve.py, drives the LIVE render path (S.path + reload() -> render()), and
// asserts: the canvas draws the seeded graph as .node elements; egress verdicts
// come from the SERVER (surfaced in the node aria-labels via effVerdict); the
// refusing-master inspector splits reserved vs prohibited (E6); and a write-back
// (register an agent through the same bridge) round-trips into a redraw with the
// reservation preserved.
// Usage: node patch_render.mjs <PORT> <FOLDER_CONTEXT>
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
    window.fetch = (u, o) => fetch(u, o);          // node global fetch
  },
});
const { window } = dom;
const stage = () => window.document.getElementById("stage");
const nodesOf = (kind) => [...stage().querySelectorAll(".node." + kind)];

async function main() {
  // wait for the page script to define the live API
  for (let i = 0; i < 80 && typeof window.render !== "function"; i++) await sleep(20);
  if (typeof window.render !== "function" || !window.S || typeof window.tool !== "function")
    fail("page script did not load (render/tool/S missing)");

  // Drive the canvas from the LIVE graph directly. We deliberately bypass the
  // app's reload(): at boot the first governance_graph(F) can race the log and
  // return empty, and reload() then nulls S.path and strands on the static
  // SAMPLE. Fetch the graph ourselves (retry past the boot race) and render —
  // so the test asserts the REAL server graph, never the sample.
  async function draw(minAgents) {
    window.S.path = F;
    for (let i = 0; i < 40; i++) {
      const g = await window.tool("workspace_workflow", { op: "governance_graph", params: { folder_context: F } });
      if (g && g.nodes && g.nodes.filter((n) => n.kind === "agent").length >= minAgents) {
        window.S.g = g; window.render(); return;
      }
      await sleep(40);
    }
    fail("governance_graph never reached " + minAgents + " agent(s) — boot race or write not projected");
  }
  await draw(1);

  // 1. the canvas drew the seeded graph as .node elements
  if (nodesOf("agent").length !== 1) fail("expected 1 agent node, got " + nodesOf("agent").length);
  if (nodesOf("human").length !== 1) fail("expected 1 human node, got " + nodesOf("human").length);
  if (nodesOf("use_case").length !== 2) fail("expected 2 use_case nodes, got " + nodesOf("use_case").length);
  if (nodesOf("master").length !== 1) fail("expected 1 master node");

  // 2. verdicts come from the SERVER (rendered via effVerdict into the aria-label):
  //    one use case earned 'auto', one is 'reserved by law'.
  const ucAria = nodesOf("use_case").map((el) => (el.getAttribute("aria-label") || "").toLowerCase());
  if (!ucAria.some((a) => a.includes("verdict auto"))) fail("no use_case rendered with server verdict 'auto' (" + JSON.stringify(ucAria) + ")");
  if (!ucAria.some((a) => a.includes("your policy"))) fail("no use_case rendered 'reserved by law' (" + JSON.stringify(ucAria) + ")");

  // 3. refusing-master inspector splits reserved vs prohibited (E6) — never one
  //    folded 'law' tally. Select the master, re-render, read the lamps.
  window.S.sel = "master";
  window.render();
  const insp = window.document.getElementById("inspectBody");
  const reservedLamp = insp.querySelector(".lamp[data-reserved]");
  const prohibitedLamp = insp.querySelector(".lamp[data-prohibited]");
  if (!reservedLamp) fail("master inspector has no separate 'reserved by law' lamp");
  if (!prohibitedLamp) fail("master inspector folds prohibited into reserved (no data-prohibited lamp) — E6");
  if (+reservedLamp.getAttribute("data-reserved") < 1) fail("inspector data-reserved should be >=1 for the seeded reservation");

  // 4. write-back round-trip through the REAL canvas path: register an agent,
  //    then window.wire() it onto the RESERVED use case. wire()->reReg() must
  //    add the agent AND preserve the use case's fingerprint (so it stays
  //    'reserved by law'). The bug this guards: reReg re-registered with an
  //    empty fingerprint, so the wire silently no-op'd (agent never appeared)
  //    and would have blanked the verdict.
  window.S.sel = null;
  const reservedUc = nodesOf("use_case")
    .find((el) => (el.getAttribute("aria-label") || "").toLowerCase().includes("your policy"))
    .getAttribute("data-id");
  await window.tool("workspace_policy", { op: "party_register", params: { folder_context: F, party_id: "bot9", kind: "agent", grade: "L1" } });
  await window.wire("party:bot9", reservedUc);   // exercises the fixed reReg (fingerprint preserved)
  await draw(2);   // redraw from the live graph; retry until the new edge projects
  if (nodesOf("agent").length !== 2) fail("wire() did not add the new agent (agents=" + nodesOf("agent").length + ")");
  const ids = nodesOf("agent").map((el) => el.getAttribute("data-id"));
  if (!ids.includes("party:bot9")) fail("new agent party:bot9 not in redrawn graph (" + JSON.stringify(ids) + ")");
  // fingerprint preserved across the wire → the reserved use case is STILL reserved
  const stillReserved = nodesOf("use_case")
    .some((el) => el.getAttribute("data-id") === reservedUc &&
                  (el.getAttribute("aria-label") || "").toLowerCase().includes("your policy"));
  if (!stillReserved) fail("wire() blanked the use case's fingerprint — verdict no longer reserved");

  // 5. reload() must KEEP the real folder + live graph. The sample-reset trap
  //    previously nulled S.path on a transient empty read and stranded the app
  //    on the static SAMPLE for the rest of the session.
  await window.reload();
  if (window.S.path !== F) fail("reload() nulled S.path for a real folder (sample-reset trap)");
  if (window.S.g && window.S.g._sample) fail("reload() fell back to SAMPLE for a real folder");
  if (nodesOf("agent").length !== 2) fail("reload() lost the live graph (agents=" + nodesOf("agent").length + ")");

  console.log("PASS: patch canvas renders from governance_graph; verdicts server-side (effVerdict); master inspector splits reserved/prohibited (E6); wire() round-trips and preserves the fingerprint/verdict; reload() keeps the real folder (no sample-reset trap)");
  process.exit(0);
}
main().catch((e) => fail(e && e.stack ? e.stack : String(e)));
