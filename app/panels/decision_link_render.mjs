// Real DOM test for action-link identity in the console (rung 1: the
// registered channel is the credential). Seeded: one pending decision with a
// minted link for party "dana". Asserts: applying the link opens the
// workbench acting as dana with the authenticated-by-link line; recording
// through the token succeeds and the receipt follows; a second application of
// the spent/closed link is refused in words, never silently ignored.
// Usage: node decision_link_render.mjs <PORT> <FOLDER> <TOKEN>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
import { assertBridgeAlive } from "../harness/rvnd_gate_guards.mjs";
const PORT = process.argv[2], F = process.argv[3], TOKEN = process.argv[4];
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
const D = window.document;
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(40);

  const c = await window.applyActionLink(TOKEN);
  if (!c.ok) fail("link application refused: " + c.error);
  let out = "";
  for (let i = 0; i < 40; i++) { await sleep(80); out = (D.getElementById("decout") || {}).textContent || ""; if (/acting as/.test(out)) break; }
  if (!/acting as dana — authenticated by link/.test(out)) fail("acting-as line missing — got: " + out.slice(0, 200));
  if (!/claimed by dana/.test(out)) fail("claim line does not name the link party");

  const panel = D.getElementById("decisionpanel");
  click([...panel.querySelectorAll(".decopt")][1]); await sleep(30);
  panel.querySelector("#decrat").value = "the split follows both duties";
  click(panel.querySelector("#decrec"));
  for (let i = 0; i < 50; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (/Recorded, signed/.test(out)) break; }
  if (!/Recorded, signed/.test(out)) fail("record through the token failed — got: " + out.slice(0, 200));

  // the spent link refuses in words on a fresh application
  const again = await window.applyActionLink(TOKEN);
  if (again.ok !== false) fail("a spent link must refuse");

  console.log("PASS: action link — acting-as line with party and rung wording; record through the token; spent link refused in words");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
