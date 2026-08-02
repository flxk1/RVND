// Real DOM test for #40 — authoring declarations on a gate from the Inspector.
// Loads index.html in jsdom against serve.py, selects a use_case, and drives
// all four declaration types through the add-form: reserve (reaches the
// chain; a second one accumulates — sticky), obligation (persists as a duty),
// redress (persists as a remedy), prohibit (node severed: prohibited flag set,
// egress cord verdict prohibited).
// Usage: node declarations_render.mjs <PORT> <FOLDER_CONTEXT>
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
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;

const graph = () => window.tool("workspace_workflow", { op: "governance_graph", params: { folder_context: F } });
const nodeOn = async (uid) => {
  const g = await graph();
  return { node: (g.nodes || []).find((n) => n.id === uid), edges: g.edges || [] };
};
const reservedOn = async (uid) => {
  const { node } = await nodeOn(uid);
  return ((node && node.reservations) || []).map((r) => r.reserved_to);
};

async function author(type, val) {
  window.S.sel = "uc:uc-draft"; window.render(); await sleep(40);
  const btn = window.document.getElementById("daddbtn");
  if (!btn) fail("Declarations add-form not present in the Inspector");
  window.document.getElementById("dtype").value = type;
  window.document.getElementById("dval").value = val;
  btn.click();
}

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  // author the first reservation
  await author("reserve", "dpo");
  let acts = [];
  for (let i = 0; i < 40; i++) { await sleep(60); acts = await reservedOn("uc:uc-draft"); if (acts.includes("dpo")) break; }
  if (!acts.includes("dpo")) fail("authored reservation did not reach the chain: " + JSON.stringify(acts));

  // author a second — must ACCUMULATE (sticky), not drop the first
  await author("reserve", "ciso");
  for (let i = 0; i < 40; i++) { await sleep(60); acts = await reservedOn("uc:uc-draft"); if (acts.includes("ciso") && acts.includes("dpo")) break; }
  if (!(acts.includes("dpo") && acts.includes("ciso"))) fail("a prior reservation was dropped authoring via the UI (sticky failed): " + JSON.stringify(acts));

  // author an obligation — persists as a duty on the gate
  await author("obligation", "ai-disclosure");
  let obs = [];
  for (let i = 0; i < 40; i++) {
    await sleep(60);
    const g = await window.tool("workspace_workflow", { op: "governance_graph", params: { folder_context: F } });
    const uc = (g.nodes || []).find((n) => n.id === "uc:uc-draft");
    obs = ((uc && uc.obligations) || []).map((o) => o.obligation);
    if (obs.includes("ai-disclosure")) break;
  }
  if (!obs.includes("ai-disclosure")) fail("authored obligation did not reach the chain: " + JSON.stringify(obs));

  // author a redress route — persists as a remedy carried by the gate
  await author("redress", "ombud");
  let reds = [];
  for (let i = 0; i < 40; i++) {
    await sleep(60);
    const { node } = await nodeOn("uc:uc-draft");
    reds = ((node && node.redress) || []).map((r) => r.by);
    if (reds.includes("ombud")) break;
  }
  if (!reds.includes("ombud")) fail("authored redress did not reach the chain: " + JSON.stringify(reds));

  // author a prohibit — the gate is severed: prohibited flag set on the node
  // and the egress cord carries the prohibited verdict (no run needed)
  await author("prohibit", "");
  let node = null, edges = [];
  for (let i = 0; i < 40; i++) {
    await sleep(60);
    ({ node, edges } = await nodeOn("uc:uc-draft"));
    if (node && node.prohibited === true) break;
  }
  if (!node || node.prohibited !== true) fail("authored prohibit did not set the prohibited flag: " + JSON.stringify(node && { prohibited: node.prohibited }));
  const eg = edges.find((e) => e.from === "uc:uc-draft" && e.kind === "egress");
  if (!eg || eg.verdict !== "prohibited") fail("egress cord does not carry the prohibited verdict: " + JSON.stringify(eg));
  // sticky across types: the earlier declarations survive the prohibit
  const still = ((node.reservations) || []).map((r) => r.reserved_to);
  if (!(still.includes("dpo") && still.includes("ciso"))) fail("a prior reservation was dropped by the prohibit: " + JSON.stringify(still));

  console.log("PASS: declaration authoring — reserve reaches the chain + accumulates (sticky), obligation persists as a duty, redress persists as a remedy, prohibit severs (flag + egress verdict)");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
