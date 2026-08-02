// Real DOM test for the Help drawer (the operation reference, read-only).
// Opens Help, asserts the four goal sections render from the live op:'help'
// catalogues, a known op + its params show, standalone tools carry their fixed
// one-liner, the filter narrows rows, and the panel is a read-only modal.
// Usage: node help_render.mjs <PORT> <FOLDER>
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
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openHelpPanel();
  const hp = window.document.getElementById("helppanel");
  if (!hp) fail("help panel did not open");
  if (hp.getAttribute("aria-modal") !== "true") fail("help panel is not a modal dialog");
  if (!hp.querySelector(".robadge")) fail("help panel carries no read-only badge");

  let txt = "";
  for (let i = 0; i < 60; i++) { await sleep(80); txt = window.document.getElementById("hlpout").textContent; if (/verify_chain/.test(txt)) break; }
  for (const sect of ["Set up", "Rules", "Pending", "Record"])
    if (!txt.includes(sect) && ![...hp.querySelectorAll(".hlpsect")].some(s => s.textContent.includes(sect)))
      fail("goal section missing: " + sect);
  if (!/verify_chain/.test(txt)) fail("known audit op missing — got: " + txt.slice(0, 200));
  if (!/requires folder_context/.test(txt)) fail("required params do not render");
  if (!/workspace_ask/.test(txt)) fail("standalone tool missing from the reference");
  if (!/one governed chat turn/.test(txt)) fail("standalone tool one-liner missing");
  const rows = [...hp.querySelectorAll(".hlprow")];
  if (rows.length < 100) fail("registry too small — only " + rows.length + " rows rendered");
  if (!rows.some(r => r.querySelector("div"))) fail("no op renders a note/hook basis line");

  window.document.getElementById("hlpq").value = "verify_chain";
  window.filterHelp();
  const visible = rows.filter(r => r.style.display !== "none");
  if (visible.length >= rows.length) fail("filter hides nothing");
  if (!visible.some(r => r.dataset.k.includes("verify_chain"))) fail("filter hid its own match");
  window.document.getElementById("hlpq").value = "";
  window.filterHelp();
  if (rows.filter(r => r.style.display !== "none").length !== rows.length) fail("clearing the filter did not restore all rows");

  if (hp.querySelectorAll("button").length) fail("help must be read-only — found button(s)");

  console.log("PASS: help drawer — four sections from live catalogues; params + basis lines; filter narrows and restores; read-only modal");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
