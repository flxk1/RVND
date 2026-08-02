// End-to-end governance-story gate, driven through the real visualiser.
// Loads the actual index.html in jsdom against a running serve.py and walks
// the whole story: identify the non-mutating demo (sample) mode; switch to a
// real seeded folder and inspect the agent, task, person and boundary; read
// the SERVER-declared egress verdicts and prove the DOM renders those, not the
// client's hardcoded sample; open the pending decision, claim it as a person,
// choose an option with a rationale, and record it; confirm the choice is on
// the workspace's signed chain; then reload (re-fetch from the server) and
// prove the decision closed server-side and the signed choice survived.
//
// The verdicts asserted are never recomputed here: the reserved/auto egress
// verdicts come from an independent governance_graph server round-trip, and the
// decision's recorded state is read back from workspace_audit (verify_chain +
// tail), the server's signed mutation log. The sample graph carries the SAME
// node ids and a 'reserved' verdict, so proving server-origin means proving the
// rendered graph is NOT the sample: real basis "your policy" (server) vs the
// sample's "required by law", and real labels "uc-decide" vs sample "Loan
// decision".
// Usage: node governance_demo_render.mjs <PORT> <FOLDER> <DECISION_ID>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";

const PORT = process.argv[2], F = process.argv[3], DID = process.argv[4];
const RATIONALE = "Reviewed the assembled grounds; a person signs off, satisfying the reserved floor for this high-risk task.";
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    bridgeGlobals(window, PORT);
    window.fetch = (u, o) => fetch(u, o);
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const D = window.document;
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("visualiser did not boot");

  // ---- 1. the non-mutating demonstration mode (no folder → client sample) ----
  window.S.path = null;
  await window.reload();
  await sleep(50);
  if (window.S.g._sample !== true) fail("no-folder view is not the non-mutating sample graph");
  if (!D.getElementById("stagewm")) fail("sample mode missing its 'DEMO PATCH · not the signed record' watermark");
  // the sample is a client fixture: it attributes uc-decide's reservation to LAW
  const sampleFindings = D.getElementById("findings").textContent;
  if (!/required by law/.test(sampleFindings)) fail("sample findings did not show the client fixture's law basis — got: " + sampleFindings.slice(0, 160));

  // ---- 2. switch to the REAL seeded folder; prove it is NOT the sample ----
  window.S.path = F;
  await window.reload();
  await sleep(70);
  if (window.S.g._sample) fail("real folder still rendered the client sample graph");
  if (window.S.path !== F) fail("S.path is not the real folder: " + window.S.path);
  if (D.getElementById("stagewm")) fail("demo watermark present on a real folder");

  // ---- 3. inspect the agent, task, person and boundary ----
  const stage = D.getElementById("stage");
  const kind = (k) => stage.querySelectorAll(".node." + k).length;
  if (kind("agent") !== 1) fail("expected 1 agent node, got " + kind("agent"));
  if (kind("human") !== 1) fail("expected 1 human (person) node, got " + kind("human"));
  if (kind("use_case") !== 2) fail("expected 2 task nodes, got " + kind("use_case"));
  if (kind("master") !== 1) fail("expected 1 boundary node, got " + kind("master"));
  const stageTxt = stage.textContent;
  for (const lbl of ["Agent", "Task", "The boundary"])
    if (!stageTxt.includes(lbl)) fail("lay label missing on canvas: " + lbl);

  // ---- 4. the egress verdicts come from the SERVER, not the client ----
  // independent server round-trip: the authoritative graph
  const gg = await window.tool("workspace_workflow", { op: "governance_graph", params: { folder_context: F } });
  const egVerdict = (uc) => { const e = gg.edges.find((e) => e.kind === "egress" && e.from === uc); return e && e.verdict; };
  const vDecide = egVerdict("uc:uc-decide"), vDraft = egVerdict("uc:uc-draft");
  if (vDecide !== "reserved") fail("server did not declare uc-decide reserved, got " + vDecide);
  if (vDraft !== "auto") fail("server did not declare uc-draft auto, got " + vDraft);
  // the graph the DOM rendered from carries the SAME server verdict on that edge
  const domEdge = window.S.g.edges.find((e) => e.kind === "egress" && e.from === "uc:uc-decide");
  if (!domEdge || domEdge.verdict !== vDecide) fail("DOM graph edge verdict diverged from the server graph");
  // the Check panel reflects the SERVER verdict + the SERVER's attributed basis.
  // The client sample would say "required by law" (its fixture); the real folder
  // reserves "by data-protection" → basis policy → "your policy". Seeing the
  // server's basis and NOT the fixture's proves the render is server-origin.
  const find = D.getElementById("findings").textContent;
  if (!/Never automatic/.test(find)) fail("Check panel did not render the reserved verdict note — got: " + find.slice(0, 160));
  if (!/your policy/.test(find)) fail("Check panel did not show the server's attributed basis (your policy)");
  if (/required by law/.test(find)) fail("real folder rendered the client sample's law basis — verdict is not server-origin");
  if (!/uc-decide/.test(find)) fail("Check panel shows the sample's labels, not the server's task id (uc-decide)");
  if (/Loan decision/.test(find)) fail("Check panel shows the client sample label 'Loan decision', not the server task");
  if (!/cleared/.test(find)) fail("Check panel did not render the auto (cleared) verdict for uc-draft");
  // the boundary node carries the reserved treatment (server-declared floor)
  if (!stage.querySelector(".node.master.reserved")) fail("boundary node missing the reserved treatment");

  // ---- 5. open the pending decision (served by the SERVER) ----
  const pend = await window.tool("workspace_dispatch", { op: "decision_pending", params: { folder_context: F } });
  if (!(pend.pending || []).some((p) => p.decision_id === DID)) fail("server has no pending decision " + DID);
  await window.openDecisionPanel();
  let out = "";
  for (let i = 0; i < 60; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (out.includes(DID)) break; }
  if (!out.includes(DID)) fail("pending decision from the server did not render in the panel — got: " + out.slice(0, 200));
  const claimBtn = D.querySelector('[data-decclaim="' + DID + '"]');
  if (!claimBtn || !/Claim & review/.test(claimBtn.textContent)) fail("claim-and-review control not offered for the pending decision");

  // ---- 6. perform the permitted human action: claim, choose, record ----
  click(claimBtn);
  for (let i = 0; i < 60; i++) { await sleep(80); if (D.querySelector("#decout .decopt")) break; }
  const opts = [...D.querySelectorAll("#decout .decopt")];
  if (opts.length !== 2) fail("claimed decision did not render its 2 server options, got " + opts.length);
  const approve = opts.find((o) => o.dataset.opt === "approve");
  if (!approve) fail("the 'approve' option (from the server surface) was not rendered");
  click(approve);
  const rat = D.getElementById("decrat");
  rat.value = RATIONALE;
  click(D.getElementById("decrec"));
  for (let i = 0; i < 60; i++) { await sleep(90); out = D.getElementById("decout").textContent; if (/Recorded, signed/.test(out)) break; }
  if (!/Recorded, signed/.test(out)) fail("the decision was not recorded — got: " + out.slice(0, 220));
  const m = out.match(/Recorded, signed — ([0-9a-f-]{36})/);
  if (!m) fail("no server audit id in the signed-record confirmation — got: " + out.slice(0, 220));
  const AUD = m[1];

  // ---- 7. the verdict/state is SERVER-declared and on the signed chain ----
  const vc = await window.tool("workspace_audit", { op: "verify_chain", params: { folder_context: F } });
  if (vc.ok !== true) fail("the signed chain does not verify: " + JSON.stringify(vc.broken_links || vc.signature_failures || vc));
  const tail = await window.tool("workspace_audit", { op: "tail", params: { folder_context: F, limit: 200 } });
  const ev = (tail.events || []).find((e) => e.audit_id === AUD);
  if (!ev) fail("recorded decision's audit id is not on the server chain");
  if (ev.pair_id !== "decision:approve") fail("chain event does not carry the chosen option, got " + ev.pair_id);
  if (ev.actor !== "app-user") fail("chain event's decider is not the acting person, got " + ev.actor);
  if (ev.signed !== true) fail("the decision event is not signed");

  // ---- 8. reload (re-fetch from the server) and prove persistence ----
  const dp0 = D.getElementById("decisionpanel"); if (dp0) dp0.remove();
  window._decisionQueue = [];
  await window.reload();
  await sleep(70);
  const pend2 = await window.tool("workspace_dispatch", { op: "decision_pending", params: { folder_context: F } });
  if ((pend2.pending || []).some((p) => p.decision_id === DID)) fail("decision still pending after reload — it did not close/persist server-side");
  const tail2 = await window.tool("workspace_audit", { op: "tail", params: { folder_context: F, limit: 200 } });
  const ev2 = (tail2.events || []).find((e) => e.audit_id === AUD);
  if (!ev2 || ev2.pair_id !== "decision:approve") fail("recorded choice missing from the chain after reload — not persisted");
  // reopen the panel: the resolved decision is no longer offered for claim
  await window.openDecisionPanel();
  for (let i = 0; i < 40; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (out.length) break; }
  if (out.includes(DID) && /Claim & review/.test(out)) fail("resolved decision still offered for claim after reload");

  console.log("PASS: governance demo — sample mode identified; agent/task/person/boundary inspected; "
    + "server reserved+auto verdicts rendered (not the client sample); decision claimed, chosen and recorded ("
    + AUD + "); on the signed chain as decision:approve by app-user; closed + persisted across reload");
  process.exit(0);
}
main().catch((e) => fail(e && e.stack ? e.stack : String(e)));
