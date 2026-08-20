// Real DOM test for the federated-verdict composition inspector (Connected
// tools drawer). Seeded: two live channels on one use case — one whose tool
// said deny, one contributing only through its hold floor — plus a muted
// channel that had said deny. Asserts: the composition toggle opens; the
// dominator is named in a sentence; each row names its binding input; the
// disagreement banner is worded; the muted channel renders struck-through
// with its last state; rows sort strictest first.
// The drawer ships as its own pack bundle (app/src/panels/federation.js),
// registered through the panel-mount contract
// (docs/loomground-proposals/panel-mount-contract.md), so this gate loads the
// composed page (GET /classic) rather than a bare readFileSync of the shell
// source — a raw index.html would open a frame with nothing inside it.
// Usage: node federation_comp_render.mjs <PORT> <FOLDER>
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
const D = window.document;
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  await assertBridgeAlive(window, fail);
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openFederationPanel();
  let out = "";
  for (let i = 0; i < 60; i++) { await sleep(80); out = (D.getElementById("fdout") || {}).textContent || ""; if (/composition/.test(out)) break; }
  const toggle = D.querySelector('[data-fedcomp="score"]');
  if (!toggle) fail("no composition toggle on the joined verdict — got: " + out.slice(0, 240));

  click(toggle); await sleep(60);
  const box = D.querySelector('[data-comp="score"]');
  if (!box || box.style.display === "none") fail("composition did not open");
  const t = box.textContent;

  if (!/Strictest: .*deny-bot/.test(t)) fail("dominator sentence does not name the deny source — got: " + t.slice(0, 200));
  if (!/binding: tool said/.test(t)) fail("the deny row does not name its binding input");
  if (!/no verdict — the floor holds the line/.test(t)) fail("the floor-only channel does not state that the floor holds");
  if (!/binding: (channel|group) floor/.test(t)) fail("the floor-bound row does not name the floor as binding");
  if (!/Sources disagree/.test(t) || !/strictest wins/.test(t)) fail("disagreement is not a worded banner");
  if (!/Muted channels/.test(t) || !/last said deny/.test(t)) fail("muted channel with its last state missing");
  const struck = [...box.querySelectorAll("div")].some(el => /line-through/.test(el.getAttribute("style") || "") && /muted-bot/.test(el.textContent));
  if (!struck) fail("muted channel is not struck-through");
  if (!/the workspace's own gate/.test(t)) fail("local contribution row missing");

  // strictest first: the deny contributor renders above the hold contributor
  if (t.indexOf("deny-bot") > t.indexOf("floor-bot")) fail("rows are not strictest-first");

  // toggle closes again
  click(toggle); await sleep(40);
  if (box.style.display !== "none") fail("composition did not close on second toggle");

  console.log("PASS: federation composition — dominator named; binding input per row; worded disagreement; muted struck with last state; strictest-first; toggle closes");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
