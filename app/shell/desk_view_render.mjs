// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the DESK stage view — the mixing-desk projection as a
// view-toggle state (PATCH | ARRANGE | DESK | MATRIX). Asserts: the toggle
// order; switching to DESK fills the stage region with the drawLoom projection
// (master bus, governed faders with the law clamp); the faders carry no
// draggable input (read-only, discrete L-cells); the Record menu "Desk view"
// entry is a shortcut that lands on the same view; PATCH hides it again.
// Usage: node desk_view_render.mjs <PORT> <FOLDER>
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

async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");

  // toggle order: PATCH | ARRANGE | DESK | MATRIX
  const order = [...D.querySelectorAll(".viewtog button")].map((b) => b.dataset.view);
  if (JSON.stringify(order) !== JSON.stringify(["patch", "arrange", "desk", "matrix"]))
    fail("view-toggle order wrong: " + JSON.stringify(order));

  window.S.path = F; await window.reload(); await sleep(60);
  window.setView("desk");
  const host = D.getElementById("deskview");
  for (let i = 0; i < 60 && !host.querySelector(".fader[data-fader]"); i++) await sleep(30);
  if (!host.classList.contains("show")) fail("desk view not shown after setView");
  const btn = D.querySelector('.viewtog button[data-view="desk"]');
  if (btn.getAttribute("aria-checked") !== "true") fail("DESK toggle not aria-checked");

  // the same drawLoom projection: master bus + governed faders
  if (!host.querySelector("#loom-master")) fail("master bus not rendered in the desk view");
  const faders = [...host.querySelectorAll(".fader[data-fader]")];
  if (faders.length !== 2) fail("expected 2 governed faders, got " + faders.length);
  const decide = faders.find((f) => f.getAttribute("data-fader") === "uc:uc-decide");
  if (!decide || decide.getAttribute("data-reserved") !== "true") fail("uc-decide fader not clamped (reserved by law)");
  if (!decide.classList.contains("clamped")) fail("clamp affordance missing on the reserved strip");
  if (!host.querySelector(".fcell.earned")) fail("no earned autonomy level cell rendered");
  for (const f of faders) {
    if (f.getAttribute("aria-disabled") !== "true") fail("a fader is not marked read-only (aria-disabled)");
    if (!/read-only/.test(f.getAttribute("aria-label") || "")) fail("a fader aria-label does not state read-only");
  }
  // read-only view: no draggable/act control anywhere in the stage region
  if (host.querySelector("input,select,textarea,button")) fail("desk view renders an interactive control — faders must stay read-only");

  // the Record menu entry is a shortcut to the same view
  window.setView("patch");
  if (host.classList.contains("show")) fail("desk view still shown after switching to PATCH");
  const mi = D.querySelector('.sectmenu button[onclick="setView(\'desk\')"]');
  if (!mi) fail("Record menu has no Desk view shortcut");
  mi.click(); await sleep(120);
  if (!host.classList.contains("show")) fail("menu shortcut did not open the DESK view");
  if (btn.getAttribute("aria-checked") !== "true") fail("menu shortcut did not check the DESK toggle");
  if (!host.querySelector(".fader[data-fader]")) fail("menu shortcut did not render the desk projection");

  console.log("PASS: DESK stage view — fourth toggle state in PATCH|ARRANGE|DESK|MATRIX order; drawLoom projection in-stage; clamped read-only faders with no draggable input; the Record menu entry shortcuts to the same view");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
