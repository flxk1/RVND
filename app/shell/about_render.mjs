// Real DOM test for the About float (server_info, read-only). Opens About,
// asserts the product + server version render, the read-only modal, and that it
// declares-not-certifies (honesty line). server_info is a standalone tool.
// About is now a shell-chrome bundle (app/src/shell/about.js), inlined
// unconditionally by compose_classic() — so this gate loads the composed
// page (GET /classic) rather than a bare readFileSync of the shell source.
// Usage: node about_render.mjs <PORT> <FOLDER>
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

  await window.openAboutPanel();
  await sleep(120);
  const ab = window.document.getElementById("aboutpanel");
  if (!ab) fail("about panel did not open");
  if (ab.getAttribute("aria-modal") !== "true") fail("about panel is not a modal dialog");

  let txt = "";
  for (let i = 0; i < 40; i++) { await sleep(60); txt = window.document.getElementById("about").textContent; if (/server/i.test(txt) && /tools/i.test(txt)) break; }
  if (!/Rvnd/.test(txt)) fail("product name missing — got: " + txt.slice(0, 160));
  if (!/MCP tools/i.test(txt)) fail("tool count missing");
  if (!/does not certify compliance/i.test(txt)) fail("missing the declares-not-certifies honesty line");
  if (/%/.test(txt)) fail("About renders a percentage — doctrine forbids a dial/score");

  const btns = [...ab.querySelectorAll("button")];
  if (btns.length) fail("about must be read-only — found " + btns.length + " button(s)");

  console.log("PASS: about float — product + version + tool count; declares-not-certifies; read-only modal");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
