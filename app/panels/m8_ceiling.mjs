// M8 gate — the autonomy fader must render the SERVER-composed ceiling
// (use_case node `grade_ceiling`), never recompute it client-side from a
// risk→cap map (E3). Loads the real index.html in jsdom, injects a graph where
// the server ceiling DISAGREES with the old risk-map, and asserts the server
// value wins. No serve.py needed.
// Usage: node m8_ceiling.mjs
import { JSDOM } from "jsdom";
import { composeStatic } from "../harness/render_harness.mjs";

const html = composeStatic(new URL("../src/index.html", import.meta.url));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    window.__WORKSPACES_HTTP__ = "http://127.0.0.1:1/tool";
    window.fetch = () => Promise.reject(new Error("offline"));
  },
});
const { window } = dom;

function inspectorFader() {
  return window.document.getElementById("inspectBody").querySelector(".fader");
}

// render() is sync but the use_case fader is built in the async fillUcOps (after
// two awaited tool() calls that reject offline) — let those microtasks drain.
async function renderAndSettle() {
  window.render();
  for (let i = 0; i < 20 && !inspectorFader(); i++) await sleep(10);
}

async function main() {
  for (let i = 0; i < 50 && typeof window.render !== "function"; i++) await sleep(20);
  if (typeof window.render !== "function" || !window.S) fail("page did not load render/S");
  // let boot()/reload() fully settle so its async load can't overwrite our S.g.
  await sleep(400);

  // A high-risk use case whose SERVER ceiling is L4 — the retired client risk-map
  // would have said L2. If the client renders L4, it's using the server value.
  window.S.path = "x";
  window.S.g = {
    nodes: [
      { id: "uc:t", kind: "use_case", label: "t", risk: "high", grade: 1, grade_ceiling: 4, reserved: [] },
      { id: "master", kind: "master", label: "boundary" },
    ],
    edges: [{ from: "uc:t", to: "master", kind: "egress", verdict: "auto" }],
  };
  window.S.sel = "uc:t";
  await renderAndSettle();

  const fader = inspectorFader();
  if (!fader) fail("no autonomy fader rendered in the inspector");
  const aria = fader.getAttribute("aria-label") || "";
  if (!/ceiling L4/.test(aria)) fail("fader did not render the SERVER ceiling L4 (aria: " + aria + ")");
  const over = window.document.querySelectorAll("#inspectBody .fcell.over").length;
  if (over !== 0) fail("server ceiling L4 leaves no 'over' cells, got " + over + " — client recomputed from risk?");

  // A reserved-by-law use case shows the law clamp.
  window.S.g.nodes[0].reserved = ["review"];
  await renderAndSettle();
  if (!/(^|\s)clamped(\s|$)/.test(inspectorFader().className)) fail("reserved use case must show the law clamp");

  // Server ceiling L1 → cells L2..L4 are 'over' (still server-driven, not risk-map).
  window.S.sel = "uc:t";
  window.S.g.nodes[0].reserved = [];
  window.S.g.nodes[0].grade_ceiling = 1;
  await renderAndSettle();
  const over1 = window.document.querySelectorAll("#inspectBody .fcell.over").length;
  if (over1 !== 3) fail("server ceiling L1 should mark 3 'over' cells (L2-L4), got " + over1);

  // Offline path: a use case CREATED on the disconnected canvas must still carry
  // a grade_ceiling (offline mirror), so the fader doesn't fall back to L0. The
  // disagreement test above proves render never recomputes; this proves the
  // offline DATA layer stamps the ceiling (panel findings #2/#5/#8).
  window.S.path = null;
  window.S.g = { nodes: [{ id: "master", kind: "master", label: "boundary" }], edges: [] };
  window.S.sel = null;
  if (typeof window.addNode !== "function") fail("addNode not exposed for the offline test");
  await window.addNode("use_case");
  for (let i = 0; i < 20 && !inspectorFader(); i++) await sleep(10);
  const created = window.S.g.nodes.find((n) => n.kind === "use_case");
  if (!created) fail("offline addNode did not create a use_case node");
  if (created.grade_ceiling !== 4) fail("offline-created low-risk use case must carry grade_ceiling 4, got " + created.grade_ceiling);
  const af = inspectorFader();
  if (!af || !/ceiling L4/.test(af.getAttribute("aria-label") || "")) fail("offline-created node fader did not render ceiling L4");

  console.log("PASS: M8 fader renders the SERVER-composed ceiling (no client recompute) + law clamp + offline mirror");
  process.exit(0);
}
main().catch((e) => fail(e && e.stack ? e.stack : String(e)));
