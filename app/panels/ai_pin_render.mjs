// Real DOM test for pin_many + suggest in the AI & Capture drawer. Pins two skills
// in one batch (pin_many) and confirms both land in the pinned list; runs suggest
// and confirms it renders a coherent (non-error) companions response. The
// drawer ships as its own pack bundle (app/src/panels/ai.js), registered
// through the panel-mount contract, so this gate loads the composed page
// (GET /classic) rather than a bare readFileSync of the shell source.
// Usage: node ai_pin_render.mjs <PORT> <FOLDER>
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
const aiTxt = () => ((D.getElementById("aiout") || {}).textContent || "");
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(60);

  window.openAIPanel();
  for (let i = 0; i < 60 && !D.getElementById("aipinbtn"); i++) await sleep(80);
  if (!D.getElementById("aipinbtn")) fail("no pin control in the AI drawer");
  if (!D.getElementById("aisugbtn")) fail("no suggest control in the AI drawer");

  // pin_many: two ids in one batch
  D.getElementById("aiPinIds").value = "demo:alpha, demo:beta";
  D.getElementById("aipinbtn").click();
  for (let i = 0; i < 60 && !(/demo:alpha/.test(aiTxt()) && /demo:beta/.test(aiTxt())); i++) await sleep(80);
  if (!/demo:alpha/.test(aiTxt()) || !/demo:beta/.test(aiTxt())) fail("pin_many did not pin both skills; pinned text: " + aiTxt().replace(/\s+/g, " ").slice(0, 200));

  // suggest: renders a coherent response (companions or 'no companion skills'), no error
  D.getElementById("aiSugId").value = "demo:alpha";
  D.getElementById("aisugbtn").click();
  const o = () => ((D.getElementById("aiSugOut") || {}).textContent || "");
  for (let i = 0; i < 60 && /^$|loading…/.test(o()); i++) await sleep(80);
  if (/could not suggest/.test(o())) fail("suggest errored: " + o().slice(0, 120));
  if (!/companion/.test(o())) fail("suggest produced no coherent companions response; got: " + o().slice(0, 120));

  console.log("PASS: pin_many pins multiple skills in one batch (both land in the pinned list) and suggest renders companions — both workspace_dispatch ops wired");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
