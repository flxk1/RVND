// Real DOM test for the Matrix drawer (workspace_matrix). Loads the actual index.html
// in jsdom against a running serve.py, opens the policy matrix, asserts the
// autonomy×oversight grid renders with text-labelled lights, that a tighten
// (go→ask) writes through to the chain, that 'block' is the strictest (no
// click-loosen), and that the panel is a modal dialog.
// Usage: node matrix_render.mjs <PORT> <FOLDER_CONTEXT>
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

  await window.openMatrixPanel();
  await sleep(140);
  const mp = window.document.getElementById("matrixpanel");
  if (!mp) fail("matrix panel did not open");
  if (mp.getAttribute("aria-modal") !== "true") fail("matrix panel is not a modal dialog (no focus trap)");

  const cells = [...window.document.querySelectorAll(".mxcell")];
  // The autonomy ladder is remappable policy — a deployment may define any number
  // of grades — so assert the grid is COMPLETE: one cell per (grade row × oversight
  // column), derived from the rendered .mxrowh / .mxhdr, not a fixed magic count.
  const nGrades = window.document.querySelectorAll(".mxrowh").length;
  const nOv = window.document.querySelectorAll(".mxhdr").length;
  if (nGrades < 2 || nOv < 2) fail("matrix did not render a grid: " + nGrades + " grades × " + nOv + " oversight");
  if (cells.length !== nGrades * nOv)
    fail("expected " + (nGrades * nOv) + " cells (" + nGrades + " grades × " + nOv + " oversight), got " + cells.length);
  // colour-blind safety: the light is carried as TEXT, not colour alone
  if (!cells.every((c) => /^(go|ask|block)$/.test(c.textContent.trim())))
    fail("matrix cells must label the light as text (colour-blind)");

  // tighten-only: a 'go' cell tightens to 'ask' and writes through to the chain
  const go = cells.find((c) => c.dataset.light === "go");
  if (!go) fail("no 'go' cell available to tighten");
  const g = go.dataset.g, o = go.dataset.o;
  go.click();
  // the cell tightens via an async set→reload; poll for the re-render (no fixed-sleep race)
  let after = null;
  for (let i = 0; i < 40; i++) {
    await sleep(50);
    after = [...window.document.querySelectorAll(".mxcell")].find((c) => c.dataset.g === g && c.dataset.o === o);
    if (after && after.dataset.light === "ask") break;
  }
  if (!after || after.dataset.light !== "ask") fail("tighten go→ask did not render (got " + (after && after.dataset.light) + ")");
  const show = await window.tool("workspace_matrix", { op: "show", params: { folder_context: F } });
  if (((show.matrix[g] || {})[o]) !== "ask") fail("server matrix not tightened: " + JSON.stringify((show.matrix[g] || {})[o]));

  // 'block' is strictest → not click-loosenable (loosen is the deliberate Reset)
  const blk = [...window.document.querySelectorAll(".mxcell")].find((c) => c.dataset.light === "block");
  if (blk && !blk.disabled) fail("'block' cell must be disabled (no click-loosen; loosen is via Reset)");

  console.log("PASS: matrix grid renders (grades × oversight, complete); lights text-labelled; tighten go→ask writes through; block strictest; modal dialog");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
