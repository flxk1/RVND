// Real DOM test for a workflow definition can be DELETED from the board, and
// a pinned skill can be UNPINNED — two "list with no remove" gaps from the
// UI↔backend alignment audit. Drives both deletes and asserts the item
// disappears. Both halves are now pack bundles: the workflow delete half
// exercises app/src/panels/workflow.js, the skill-unpin half exercises
// app/src/panels/ai.js — both registered through the panel-mount contract,
// so this gate loads the composed page (GET /classic) rather than a bare
// readFileSync of the shell source, and both remove controls are
// data-attribute-bound click handlers now (inline onclick can't reach a
// bundle-scoped function, and the contract forbids new window globals).
// Usage: node wf_unpin_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
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
const txt = (id) => ((D.getElementById(id) || {}).textContent || "");
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(60);

  // ── workflow delete (act mode — the delete control renders only there) ───
  window.openWorkflowPanel("act");
  for (let i = 0; i < 60 && !/nightly/.test(txt("wfout")); i++) await sleep(80);
  if (!/nightly/.test(txt("wfout"))) fail("seeded workflow 'nightly' not on the board");
  const del = D.querySelector("#wfout button[data-wfdelete]");
  if (!del) fail("workflow row has no delete control");
  del.click();
  for (let i = 0; i < 60 && /nightly/.test(txt("wfout")); i++) await sleep(80);
  if (/nightly/.test(txt("wfout"))) fail("workflow still on the board after delete");

  // ── unpin a skill ────────────────────────────────────────────────────────
  window.openAIPanel();
  for (let i = 0; i < 60 && !/demo:skill/.test(txt("aiout")); i++) await sleep(80);
  if (!/demo:skill/.test(txt("aiout"))) fail("seeded pinned skill 'demo:skill' not shown");
  const unp = D.querySelector("#aiout [data-aiunpin]");
  if (!unp) fail("pinned skill has no unpin control");
  unp.click();
  for (let i = 0; i < 60 && /demo:skill/.test(txt("aiout")); i++) await sleep(80);
  if (/demo:skill/.test(txt("aiout"))) fail("skill still pinned after unpin");

  console.log("PASS: — workflow definition deletes from the board, and a pinned skill unpins; both removals write through and the item disappears");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
