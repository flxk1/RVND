// Real DOM test for the rebuilt Governance Patchbay (the new home app).
// Loads the actual index.html in jsdom against a running serve.py, points it at
// a seeded folder, asserts the dark patchbay renders nodes + server-verdict
// cords, then wires bot7 -> uc-decide and asserts it wrote through and redrew.
// Usage: node patchbay_render.mjs <PORT> <FOLDER_CONTEXT>
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
    window.fetch = (u, o = {}) => fetch(u, {
      ...o,
      headers: {
        ...(o.headers || {}),
        "X-Test-Principal": "bot7",
        "X-RVND-Proxy-Proof": "render-fixture-proof",
      },
    });
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);   // wait for boot() to settle (no race)
  if (!window._ready) fail("patchbay did not boot");

  // point at the seeded folder deterministically and render
  window.S.path = F;
  await window.reload();
  await sleep(60);

  const stage = window.document.getElementById("stage");
  const kind = (k) => stage.querySelectorAll('.node.' + k).length;
  if (kind("agent") !== 1) fail("expected 1 agent node, got " + kind("agent"));
  if (kind("human") !== 1) fail("expected 1 human node, got " + kind("human"));
  if (kind("use_case") !== 2) fail("expected 2 use_case nodes, got " + kind("use_case"));
  if (kind("master") !== 1) fail("expected 1 master node");

  // server verdicts on egress cords (color-coded, decided server-side)
  const eg = [...window.document.querySelectorAll("#cords path[stroke]")].map((p) => p.getAttribute("stroke"));
  if (!eg.includes("#5aa886")) fail("no auto (green) egress cord");        // uc-draft auto
  if (!eg.includes("#e2554a")) fail("no reserved (red) egress cord");      // uc-decide reserved

  // findings reflect the reservation
  const find = window.document.getElementById("findings").textContent;
  if (!/your policy/.test(find)) fail("findings missing the attributed basis (your policy)");

  // v0.5 surface: lay labels, the verdict carried as a label on the
  // egress cord (not colour alone), and reserved attached to the boundary node.
  const stageTxt = stage.textContent;
  for (const lbl of ["Agent", "Task", "The boundary"])
    if (!stageTxt.includes(lbl)) fail("lay label missing on canvas: " + lbl);
  const cordText = [...window.document.querySelectorAll("#cords text")].map((t) => t.textContent).join(" | ");
  if (!/reserved/.test(cordText)) fail("reserved verdict label not rendered on egress cord: " + cordText);
  if (!/auto/.test(cordText)) fail("auto verdict label not rendered on egress cord: " + cordText);
  if (!stage.querySelector(".node.master.reserved")) fail("boundary node missing the reserved treatment");

  // v0.5 accessibility: roles, live regions, focusable+labelled nodes
  // and ports, a screen-reader list, and a working keyboard select.
  if (window.document.getElementById("stage").getAttribute("role") !== "application") fail("stage missing role=application");
  if (window.document.getElementById("findings").getAttribute("aria-live") !== "polite") fail("findings is not a live region");
  const anode = stage.querySelector(".node.agent");
  if (!anode || anode.getAttribute("tabindex") !== "0" || !/Agent/.test(anode.getAttribute("aria-label") || "")) fail("agent node not focusable/labelled in lay vocabulary");
  const outport = anode.querySelector(".outlet.port");
  if (!outport || outport.getAttribute("tabindex") !== "0" || !outport.getAttribute("aria-label")) fail("outlet port not keyboard-reachable/labelled");
  const a11y = window.document.getElementById("a11ylist");
  if (!a11y || a11y.querySelectorAll("li").length < 4) fail("screen-reader node/edge list not populated");
  anode.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await sleep(30);
  if (window.S.sel !== anode.dataset.id) fail("keyboard Enter did not select the focused node");

  // wire bot7 -> uc-decide (drop action) writes through Loomground
  await window.wire("party:bot7", "uc:uc-decide");
  await sleep(80);
  const g = await window.tool("workspace_workflow", { op: "governance_graph", params: { folder_context: F } });
  const wired = g.edges.some((e) => e.kind === "authority" && e.from === "party:bot7" && e.to === "uc:uc-decide");
  if (!wired) fail("wire not persisted to the chain · S.path=" + window.S.path + " · edges=" + JSON.stringify(g.edges.filter(e=>e.kind==='authority')));

  // Run uc-decide (operate) and satisfy a reserved step via per-step override
  await window.reload();
  const uc = window.S.g.nodes.find((n) => n.id === "uc:uc-decide");
  await window.runUC(uc);
  await sleep(90);
  const runs = (await window.tool("workspace_workflow", { op: "runs", params: { folder_context: F } })).runs || [];
  const run = runs.filter((r) => r.use_case_id === "uc-decide").pop();
  if (!run) fail("Run produced no operate run for uc-decide");
  const step = (run.steps || []).find((s) => s.disposition === "reserved");
  if (!step) fail("expected a reserved step from operate, got " + JSON.stringify((run.steps || []).map((s) => s.disposition)));

  await window.overrideStep(uc, step.issue_id, "approved");
  await sleep(70);
  const ov = await window.tool("workspace_audit", { op: "overrides", params: { folder_context: F } });
  const ovlist = Array.isArray(ov) ? ov : (ov.overrides || ov.items || []);
  if (!ovlist.length) fail("per-step override not recorded: " + JSON.stringify(ov));

  // 3b keyboard reach for the two formerly mouse-only actions
  if (!window.document.querySelector('.toggle[role="radiogroup"] .tb[role="radio"]')) fail("risk toggle is not a keyboard radiogroup");
  if (![...window.document.querySelectorAll('#cords path[role="button"]')].length) fail("authority cord not keyboard-operable (unwire)");

  // 3c honest widgets — clamped fader, drift pips, calibration (uc-decide is selected + reserved)
  if (!window.document.querySelector(".fader .fcell.earned")) fail("clamped fader not rendered for the selected task");
  if (window.document.querySelectorAll(".pips .pip").length < 5) fail("drift-budget pips not rendered");
  if (!/calibration/i.test(window.document.getElementById("inspectBody").textContent)) fail("calibration readout missing");
  // refusing dual-limiter master: select the boundary, expect the lamps + a reserved count
  window.S.sel = "master"; window.render();
  await sleep(30);
  if (!window.document.querySelector(".lamp[data-reserved]")) fail("refusing-master dual-limiter lamps not rendered");

  // query bar (governance_query) — ask the patch
  await window.runQuery("agent_reach");
  await sleep(70);
  const qo = window.document.getElementById("queryout");
  if (!qo || !/bot7/.test(qo.textContent)) fail("query bar did not render results: " + (qo ? qo.textContent : "no #queryout"));

  // connectors — the boundary ports (task spine)
  await window.addConnector("ingress", "email", "uc:uc-draft");
  await sleep(90);
  const conns = window.document.querySelectorAll("#stage .node.connector").length;
  if (conns < 1) fail("connector node not rendered after addConnector (" + conns + ")");
  const g2 = await window.tool("workspace_workflow", { op: "governance_graph", params: { folder_context: F } });
  if (!g2.edges.some((e) => e.kind === "ingress")) fail("ingress connector edge not persisted to chain");

  // P4b: policy ingest → twin (applied:false) → confirm-apply writes the chain
  const twin = await window.ingestPolicy("Loan approvals must be reviewed by a risk officer. The model shall not use protected attributes.");
  if (!twin || !twin.ok || twin.classification.express.length < 2) fail("policy ingest did not produce a twin: " + JSON.stringify(twin && twin.classification));
  if (twin.applied !== false) fail("twin must be applied:false before confirmation");
  await window.applyTwin();
  await sleep(140);
  const g3 = await window.tool("workspace_workflow", { op: "governance_graph", params: { folder_context: F } });
  if (!g3.nodes.some((n) => n.kind === "use_case" && n.id === "uc:loan_approval")) fail("confirmed twin did not add the loan_approval gate to the chain (classifier singularises the act)");

  // P4c: the local-model opt-in is a choice, and a degrade is declared, never silent.
  // With no capable model the twin carries the gate's verdict and the panel says so;
  // with a capable model it reports llm_used instead — both are declared states.
  await window.openPolicyPanel();
  await sleep(60);
  const ppllm = window.document.getElementById("ppllm");
  if (!ppllm) fail("policy panel has no local-model opt-in");
  ppllm.checked = true;
  const twin2 = await window.ingestPolicy("Automated decisions must be reviewed by a compliance officer.");
  if (!twin2 || !twin2.ok) fail("opt-in ingest failed: " + JSON.stringify(twin2 && twin2.errors));
  const ppout = window.document.getElementById("ppout").textContent;
  if (twin2.capability && twin2.capability.capable === false) {
    if (!/Drafted without the local model/.test(ppout)) fail("degraded twin does not declare its degrade reason in the panel");
  } else if (twin2.llm_used !== true) fail("opt-in ingest neither used a model nor declared a degrade: " + JSON.stringify(twin2.capability));
  window.document.getElementById("ppx").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);

  // P5: register panel lists agents + tasks with categorical verdict + reserved-by-law
  await window.openRegisterPanel();
  await sleep(90);
  const reg = window.document.getElementById("rgout");
  if (!reg || !/uc-decide/.test(reg.textContent)) fail("register panel did not list tasks");
  if (!/your policy/.test(reg.textContent)) fail("register did not flag the reserved task with its attributed basis (your policy)");

  // P6 editor: load current → edit → validate (fail-closed) → apply → chain
  await window.openEditorPanel();
  await sleep(60);
  await window.editorLoad();
  await sleep(80);
  const ed = window.document.getElementById("edtext");
  if (!ed || !/gate/.test(ed.value) || !/cord/.test(ed.value)) fail("editor 'Load current' did not render the netlist");
  ed.value = "actor auditor\ngate review risk medium\ncord auditor -> review\ncord review -> master\n";
  await window.editorValidate();
  await sleep(40);
  if (window._edvalid !== true) fail("editor validate failed on a well-formed patch");
  // fail-closed: garbage must not validate
  ed.value = "frobnicate nonsense";
  await window.editorValidate();
  await sleep(40);
  if (window._edvalid !== false) fail("editor validate accepted an ill-formed patch (not fail-closed)");
  ed.value = "actor auditor\ngate review risk medium\ncord auditor -> review\ncord review -> master\n";
  await window.editorApply();
  await sleep(140);
  const g4 = await window.tool("workspace_workflow", { op: "governance_graph", params: { folder_context: F } });
  if (!g4.nodes.some((n) => n.kind === "use_case" && n.id === "uc:review")) fail("editor apply did not add the review gate to the chain");

  console.log("PASS: patchbay + Loomground; override; query; connector; policy→twin→apply (model opt-in, degrade declared); register; editor load/validate/apply");
  process.exit(0);
}
main().catch((e) => fail(e && e.stack ? e.stack : String(e)));
