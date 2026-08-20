// Real DOM test for data-lineage tags in the Inspector. Selects a use_case
// node and asserts the Tags field shows the AUTHORED tags ∪ the CONNECTOR-derived
// tags (attributed "from channel"), and that a tags-guarded reservation reads as a
// "only when tagged <t>" guard. Usage: node tags_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
import { assertBridgeAlive } from "../harness/rvnd_gate_guards.mjs";
const PORT = process.argv[2], F = process.argv[3];
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
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(80);

  // find the seeded use_case node and select it (Inspector renders on select)
  const uc = (window.S.g.nodes || []).find((n) => n.kind === "use_case" && /uc-score/.test(n.id));
  if (!uc) fail("seeded use_case node uc-score not in the graph");
  // sanity: the server projected the tag union onto the node
  if (!(uc.tags || []).includes("pii")) fail("authored tag 'pii' not projected onto the node");
  if (!(uc.tags || []).includes("eu-region")) fail("connector tag 'eu-region' not projected onto the node");

  window.S.sel = uc.id; window.S.selEdge = null; window.render(); await sleep(40);
  const body = D.getElementById("inspectBody"); const t = body.textContent;

  // the Tags field is present and shows both sources
  if (!/Data tags/.test(t)) fail("Inspector has no Data tags field");
  const chips = [...body.querySelectorAll(".tagchip")].map((c) => c.textContent);
  if (!chips.some((c) => /pii/.test(c))) fail("authored tag 'pii' chip not rendered");
  const conn = chips.find((c) => /eu-region/.test(c));
  if (!conn) fail("connector tag 'eu-region' chip not rendered");
  if (!/from channel/.test(conn)) fail("connector tag not attributed 'from channel'");

  // a tags-guarded reservation reads as a plain-words tag guard
  if (!/only when tagged/.test(t)) fail("tags-guarded reservation not surfaced as a 'only when tagged' guard");

  console.log("PASS: tags — Inspector Data tags field shows authored ∪ connector tags (attributed from channel); tags-guarded reservation reads as 'only when tagged'");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
