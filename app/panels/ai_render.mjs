// Real DOM test for the AI & Capture drawer (workspace_model / workspace_capture /
// workspace_dispatch; reads + pins, never invokes a model). Opens the drawer, asserts the labelled section
// headers render (Models / Capture / Dispatch), an honest Models state (the
// test env has no LLM backend → unreachable/empty), no percentage/dial, the
// read-only honesty line, and a read-only modal (no <button>). The drawer
// ships as its own pack bundle (app/src/panels/ai.js), registered through the
// panel-mount contract, so this gate loads the composed page (GET /classic)
// rather than a bare readFileSync of the shell source — a raw index.html
// would open a frame with nothing inside it.
// Usage: node ai_render.mjs <PORT> <FOLDER_CONTEXT>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
import { assertBridgeAlive } from "../harness/rvnd_gate_guards.mjs";
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
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openAIPanel();
  await sleep(160);
  const ap = window.document.getElementById("aipanel");
  if (!ap) fail("AI & Capture panel did not open");
  if (ap.getAttribute("aria-modal") !== "true") fail("AI panel is not a modal dialog");
  if (!/it never invokes a model/i.test(ap.textContent)) fail("missing the read-only honesty line");

  let txt = "";
  for (let i = 0; i < 40; i++) { await sleep(60); txt = window.document.getElementById("aiout").textContent; if (/Models/.test(txt) && /Dispatch/.test(txt)) break; }
  for (const sec of ["Models", "Capture", "Dispatch"])
    if (!txt.includes(sec)) fail("section header missing: " + sec + " — got: " + txt.slice(0, 240));
  if (!/(available|unreachable|could not read|no models)/i.test(txt)) fail("Models section did not render an honest available/empty/unreachable state");
  if (/%/.test(txt)) fail("drawer renders a percentage — doctrine forbids a 0-1 dial/score");

  // runtime status: per-task readiness with declared degrades, Tier C backend, endpoint health
  if (!/Task readiness/.test(txt)) fail("no Task readiness card");
  if (!/(ready|degraded)/.test(txt)) fail("readiness card names no ready/degraded state");
  if (!/degraded → [a-z_]+/.test(txt) && !/\d+ ready · 0 degraded/.test(txt)) fail("a degraded task must declare its bounded action — got: " + txt.slice(0, 300));
  if (!/Tier C semantic scan/.test(txt)) fail("no Tier C backend card");
  if (!/(fail-closed|permissive mock)/.test(txt)) fail("Tier C card does not state its failure posture");
  if (!/Local-LLM endpoint/.test(txt)) fail("no endpoint health card");

  // The drawer reads the facades; its ONE governed write is pinning a skill (recorded).
  // It must expose NO model-invoking / skill-running control (complete / classify /
  // capture / dispatch / run) — those are deferred. Any button present must be pin/suggest/unpin.
  const btns = [...ap.querySelectorAll("button")];
  const invoking = btns.filter(b => /complete|classif|capture|dispatch|\brun\b|invoke/i.test((b.textContent || "") + " " + (b.id || "")));
  if (invoking.length) fail("AI drawer exposes a model-invoking/skill-running control: " + invoking.map(b => (b.textContent || b.id).trim()).join(", "));
  const stray = btns.filter(b => !/pin|suggest/i.test((b.textContent || "") + " " + (b.id || "")));
  if (stray.length) fail("unexpected control in the read-mostly AI drawer (only pin/suggest/unpin allowed): " + stray.map(b => (b.textContent || b.id).trim()).join(", "));

  console.log("PASS: AI & Capture drawer — sections (Models / Capture / Dispatch); task readiness with declared degrades; Tier C posture; endpoint health; reads + pins (no model-invoking controls); modal dialog");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
