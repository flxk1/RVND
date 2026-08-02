// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for S5 slice 1 — "Save env": the header action captures the whole
// environment (every registered workspace) as one signed .rvnd via
// workspace_session build, and the browser downloads it. We intercept the download,
// then re-verify the captured bundle through the live MCP (verify_bytes) to prove
// the frontend produced a genuine, verifiable, referentially-complete session.
// Usage: node session_env_render.mjs <PORT> <FOLDER_A> <FOLDER_B>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], A = process.argv[3], B = process.argv[4];
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
  if (!D.getElementById("sesaveenv")) fail("missing Save-env control #sesaveenv");

  window.S.path = A; await window.reload(); await sleep(60);
  // give A some presentation state — it must travel in the bundle
  window.POS[A] = Object.assign(window.POS[A] || {}, { "uc:t": { x: 5, y: 6 } });

  // intercept the download and run Save env
  let captured = null;
  window._downloadText = (name, text) => { captured = { name, text }; };
  const r = await window.saveEnvironment();
  if (!r || !r.ok) fail("saveEnvironment did not succeed");
  if (!captured) fail("Save env did not download a file");
  if (!captured.name.endsWith(".rvnd")) fail("download is not a .rvnd file");
  if (r.card.workspace_count !== 2) fail("expected 2 workspaces, got " + r.card.workspace_count);

  // the downloaded bytes must be a genuine, verifiable session (re-check via MCP)
  let bundle; try { bundle = JSON.parse(captured.text); } catch (e) { fail("downloaded bytes are not JSON"); }
  if (bundle.format !== "rvnd-session") fail("not an rvnd-session bundle");
  const v = await window.tool("workspace_session", { op: "verify_bytes", params: { bundle } });
  if (!v.ok) fail("saved environment did not verify: " + JSON.stringify(v.report && v.report.refusal));
  if (!v.report.referential.ok) fail("saved environment has dangling refs");
  if (!v.continuation.continuable) fail("same-machine save should be continuable");
  // presentation for A rode along (id is a stable token now, not the path)
  if (!bundle.workspaces.some((w) => w.presentation && w.presentation["uc:t"]))
    fail("A's presentation did not travel in the bundle");

  // --- Open env: adopt a session, replacing the active environment ----------
  if (!D.getElementById("seopenenv")) fail("missing Open-env control #seopenenv");
  window.confirm = () => true;                 // accept the replace guard
  const openR = await window.openEnvironment(bundle, "replace"); await sleep(120);
  if (!openR || !openR.ok) fail("openEnvironment failed: " + JSON.stringify(openR && openR.report && openR.report.refusal));
  if (Object.keys(openR.adopted || {}).length !== 2) fail("expected 2 workspaces adopted");
  // the registry switched to the RESTORED folders; the originals were retired...
  const reg = await window.tool("workspace_workspace", { op: "list" });
  const paths = new Set((reg.workspaces || []).map((w) => w.path));
  for (const p of Object.values(openR.adopted)) if (!paths.has(p)) fail("adopted workspace missing from registry");
  if (paths.has(A) || paths.has(B)) fail("originals should be deregistered after replace");
  if (!(openR.retired || []).includes(A)) fail("A should be retired");
  // ...but the original folders are NOT destroyed (non-destructive)
  const fs = await import("node:fs");
  if (!fs.existsSync(A) || !fs.existsSync(B)) fail("replace destroyed an original folder");

  // a refused (dangling) session must be a no-op on the registry
  const before = (await window.tool("workspace_workspace", { op: "list" })).workspaces.length;
  const bad = JSON.parse(JSON.stringify(bundle));
  bad.rail.focused = "does-not-exist";         // dangling -> referential refusal
  const badR = await window.openEnvironment(bad, "replace");
  if (badR && badR.ok) fail("a dangling session must be refused");
  const after = (await window.tool("workspace_workspace", { op: "list" })).workspaces.length;
  if (after !== before) fail("a refused open must not change the registry");

  // --- the Open DIALOG: no mode -> a real modal with verify + guard + Open ---
  const pending = window.openEnvironment(bundle);   // no mode -> shows the dialog
  for (let i = 0; i < 40 && !D.getElementById("envdlgok"); i++) await sleep(25);
  const okBtn = D.getElementById("envdlgok");
  if (!okBtn) fail("Open dialog did not appear (no #envdlgok)");
  const dlgText = D.getElementById("envopendlg").textContent;
  if (!dlgText.includes("signature verified")) fail("dialog missing the verify banner");
  if (!dlgText.includes("replaces your current environment")) fail("dialog missing the replace guard");
  okBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  const dr = await pending; await sleep(80);
  if (!dr || !dr.ok) fail("dialog Open(replace) did not adopt");
  if (D.getElementById("envopendlg")) fail("dialog did not close after Open");

  // Cancel path: dialog opens, Cancel -> no adopt, dialog closes
  const before2 = (await window.tool("workspace_workspace", { op: "list" })).workspaces.length;
  const pending2 = window.openEnvironment(bundle);
  for (let i = 0; i < 40 && !D.getElementById("envdlgcancel"); i++) await sleep(25);
  D.getElementById("envdlgcancel").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await pending2; await sleep(40);
  if (D.getElementById("envopendlg")) fail("dialog did not close after Cancel");
  const after2 = (await window.tool("workspace_workspace", { op: "list" })).workspaces.length;
  if (after2 !== before2) fail("Cancel must not adopt");

  console.log("PASS: Save env captures + signs the whole rail; Open env verifies then ADOPTS (registry swaps to restored folders, originals retired but kept on disk); a refused session is a no-op; and the Open dialog (verify + provenance + replace guard) drives Open(replace) and Cancel");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
