// Real DOM render test for the Loom canvas (the unified Pd-DAW home surface).
// Loads the actual index.html in jsdom against a running serve.py, renders the
// whole canvas for a seeded workspace, and asserts every section is present and
// driven by live data: master limiter, patch wiring, governed faders (with the
// law clamp), agent lanes, and the oversight-real readout.
// Usage: node loom_render.mjs <PORT> <FOLDER_CONTEXT>
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
  },
});
const { window } = dom;

async function main() {
  for (let i = 0; i < 50 && typeof window.drawLoom !== "function"; i++) await sleep(20);
  if (typeof window.drawLoom !== "function") fail("page did not load drawLoom (Loom home missing)");

  const host = window.document.createElement("div");
  window.document.body.appendChild(host);
  await window.drawLoom(F, host);
  await sleep(120);  // patch sub-render + lane/calibration fetches

  // 1. master limiter (the bus) — tallies from server verdicts
  const master = host.querySelector("#loom-master");
  if (!master) fail("no master limiter section");
  const auto = +master.getAttribute("data-auto"), reserved = +master.getAttribute("data-reserved");
  if (auto < 1) fail("master shows no 'auto' cleared egress (data-auto=" + auto + ")");
  if (reserved < 1) fail("master shows no 'reserved' egress (data-reserved=" + reserved + ")");
  // prohibited is tallied SEPARATELY from reserved (E6) — the attribute must
  // exist and be reserved-disjoint (here the seed has no prohibited egress, so 0).
  if (!master.hasAttribute("data-prohibited")) fail("master missing data-prohibited (E6 tally not split)");
  const prohibited = +master.getAttribute("data-prohibited");
  if (!(prohibited >= 0)) fail("master data-prohibited not numeric (data-prohibited=" + master.getAttribute("data-prohibited") + ")");

  // 2. the wiring (patch canvas) embedded
  if (!host.querySelector("#patchsvg")) fail("patch canvas not embedded in Loom");

  // 3. governed faders — one per use-case; the law clamp shows on uc-decide
  const faders = [...host.querySelectorAll(".fader[data-fader]")];
  if (faders.length !== 2) fail("expected 2 governed faders, got " + faders.length);
  const decide = faders.find((f) => f.getAttribute("data-fader") === "uc:uc-decide");
  const draft = faders.find((f) => f.getAttribute("data-fader") === "uc:uc-draft");
  if (!decide || decide.getAttribute("data-reserved") !== "true") fail("uc-decide fader not clamped (reserved by law)");
  if (!draft || draft.getAttribute("data-reserved") !== "false") fail("uc-draft fader should not be clamped");

  // 4. agent lanes (tracks) rendered
  const lanes = host.querySelector("#loom-lanes");
  if (!lanes || !lanes.querySelector(".card")) fail("no agent lane (track) rendered");

  // 5. oversight-real readout (learning budget + calibration)
  const ad = host.querySelector("#loom-adequacy");
  if (!ad || !/drift budget cap/.test(ad.textContent)) fail("no oversight-real (learning/calibration) readout");
  if (!/calibration/.test(ad.textContent)) fail("no calibration readout");

  console.log("PASS: Loom renders master + patch + governed faders (law clamp) + agent lanes + oversight-real, all from live ops");
  process.exit(0);
}
main().catch((e) => fail(e && e.stack ? e.stack : String(e)));
