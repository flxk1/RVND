// Real DOM test for the Workspace / workspace-creator (workspace_workspace + workspace_folder).
// Opens the drawer, creates a NEW workspace, asserts it registers (workspace_workspace list),
// appears in the toolbar #folder select, and becomes the active folder context.
// Usage: node workspace_render.mjs <PORT> <NEW_WORKSPACE_PATH>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], NEW = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const base = (p) => (p || "").replace(/\/+$/, "").split("/").pop();
const dom = new JSDOM(html, { runScripts: "dangerously", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o); window.confirm = () => true;
  Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
  Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
} });
const { window } = dom;
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  await window.openWorkspacePanel(); await sleep(180);
  const wp = window.document.getElementById("wspanel");
  if (!wp) fail("workspace panel did not open");
  if (wp.getAttribute("aria-modal") !== "true") fail("not a modal dialog");
  window.document.getElementById("wsnew").value = NEW;
  window.document.getElementById("wslabel").value = "render-test workspace";
  window.document.getElementById("wscreate").click();
  // the server resolves the path (macOS /var -> /private/var symlink), so match by basename
  const b = base(NEW);
  let listed = false;
  for (let i = 0; i < 60; i++) { await sleep(60); const r = await window.tool("workspace_workspace", { op: "list" }); if (((r && r.workspaces) || []).some((w) => base(w.path) === b)) { listed = true; break; } }
  if (!listed) fail("new workspace not registered in workspace_workspace list");
  let inSelect = false;
  for (let i = 0; i < 40; i++) { await sleep(50); const sel = window.document.getElementById("folder"); if (sel && [...sel.options].some((o) => o.textContent === b)) { inSelect = true; break; } }
  if (!inSelect) fail("new workspace did not appear in the toolbar folder select");
  for (let i = 0; i < 40 && base(window.S.path || "") !== b; i++) await sleep(50);
  if (base(window.S.path || "") !== b) fail("app did not switch folder context to the new workspace");
  console.log("PASS: workspace — create makes + registers a workspace; it appears in the list and toolbar select; app switches to it");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
