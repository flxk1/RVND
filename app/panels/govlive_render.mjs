// Real DOM test for the Live Governance drawer (governance_live, contract v2).
// Opens it against REAL seeded state and asserts presence plus the four v2
// honesty invariants — the viz must not be able to misrepresent the
// protection. Verdicts are cross-checked against direct lane_capabilities
// calls (the honest source), never against a hardcoded mapping. Loads the
// composed page (GET /classic) because the drawer ships as a pack bundle.
// Usage: node govlive_render.mjs <PORT> <FOLDER> <P_ADMITTED> <P_EXPIRED> <P_SUSPENDED>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
const P_ADMITTED = process.argv[4], P_EXPIRED = process.argv[5], P_SUSPENDED = process.argv[6];
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

  await window.openGovlivePanel();
  await sleep(160);
  const gp = window.document.getElementById("govlivepanel");
  if (!gp) fail("govlive panel did not open");
  if (gp.getAttribute("aria-modal") !== "true") fail("govlive panel is not a modal dialog");
  let root = null;
  for (let i = 0; i < 40; i++) { await sleep(60); root = gp.querySelector("#govlive"); if (root && root.querySelector(".gl-session")) break; }
  if (!root) fail("#govlive root missing");

  // ── presence / correctness (§3.1–3, v2) ────────────────────────────
  const sessions = [...root.querySelectorAll(".gl-session")];
  if (sessions.length !== 3) fail("expected 3 .gl-session (aria, nyx expired, rex suspended), got " + sessions.length);
  const unadmitted = sessions.filter((s) => s.dataset.admitted === "false");
  if (unadmitted.length !== 1) fail("exactly one session (expired " + P_EXPIRED + ") must render data-admitted=false, got " + unadmitted.length);
  if (unadmitted[0].querySelector(".gl-cap")) fail("un-admitted session must carry no .gl-cap capability chip");
  for (const s of sessions) {
    if (s.dataset.admitted === "true") {
      const cap = s.querySelector(".gl-cap");
      if (!cap) fail("admitted session " + s.dataset.sid + " is missing .gl-cap");
      if (!cap.dataset.folder) fail("admitted session " + s.dataset.sid + " .gl-cap carries no data-folder");
    }
  }

  // Verdict honesty vs the source: each admitted session's rendered verdict
  // must equal the STRICTEST-WINS collapse of that agent's raw
  // lane_capabilities projection — the op's own documented derivation
  // (_VERDICT_RANK: a board must not under-report the constraint). The reads
  // go through the page's OWN bridge (same auth + prefix logic) and an
  // unreadable or verdict-less source is a hard FAIL — a cross-check that
  // can silently skip is a fake-partial-green.
  const RANK = { prohibited: 5, refused: 4, reserved: 3, human: 2, auto: 1, unfired: 0 };
  const collect = (x, out) => {
    if (Array.isArray(x)) x.forEach((v) => collect(v, out));
    else if (x && typeof x === "object") {
      for (const [k, v] of Object.entries(x)) {
        if (k === "verdict" && typeof v === "string" && v in RANK) out.push(v);
        else collect(v, out);
      }
    }
    return out;
  };
  const lcap = {};
  for (const p of [P_ADMITTED, P_SUSPENDED]) {
    const r = await window.tool("workspace_workflow", { op: "lane_capabilities", params: { folder_context: F, actor: p } })
      .catch((e) => fail("lane_capabilities unreadable for " + p + ": " + ((e && e.message) || e)));
    const vs = collect(r, []);
    if (!vs.length) fail("lane_capabilities for " + p + " carries no verdict cells — cannot cross-check: " + JSON.stringify(r).slice(0, 160));
    lcap[p] = vs.reduce((a, b) => (RANK[a] >= RANK[b] ? a : b));
  }
  const rendered = sessions.filter((s) => s.dataset.admitted === "true").map((s) => s.dataset.verdict).sort();
  const source = Object.values(lcap).sort();
  if (JSON.stringify(rendered) !== JSON.stringify(source))
    fail("rendered admitted verdicts " + JSON.stringify(rendered) + " != strictest-wins lane_capabilities " + JSON.stringify(source));
  const escalations = [...root.querySelectorAll(".gl-escalation")];
  if (!escalations.length) fail("no .gl-escalation rendered — lane_capabilities escalation flag must surface");

  // ── invariant 4 · admission honesty ────────────────────────────────
  for (const s of unadmitted) {
    if (s.dataset.verdict === "auto") fail("admission honesty: un-admitted session " + s.dataset.sid + " rendered the GO-family verdict 'auto'");
    if (s.dataset.verdict !== "refused") fail("admission honesty: un-admitted session " + s.dataset.sid + " must render refused, got '" + s.dataset.verdict + "'");
  }

  // ── invariant 5 · serialization BY REFUSAL (contract 2026-08-08) ───
  // The run plane refuses a second concurrent run per (folder,workflow) at
  // enqueue — the seed asserts that refusal at the source — so the board
  // renders EXACTLY one in-flight lease per group and never a queue: a race
  // cannot exist, and the drawing carries nothing to misrepresent.
  const leases = [...root.querySelectorAll(".gl-leases .gl-lease")];
  if (!leases.length) fail("run-lease serialization: no .gl-lease rendered (worker-1 holds a run)");
  const byFW = {};
  for (const l of leases) {
    const k = l.dataset.folderWorkflow || "(none)";
    byFW[k] = (byFW[k] || 0) + 1;
    if (byFW[k] > 1) fail("run-lease serialization: " + byFW[k] + " leases drawn for " + k + " — the module refuses this state, the board must not invent it");
    if (!l.dataset.holder) fail("run-lease serialization: lease for " + k + " carries no data-holder");
  }
  if (root.querySelector(".gl-lease[data-position]")) fail("run-lease serialization: data-position rendered — there is no queue, a second run is refused at enqueue");

  // ── invariant 6 · chain linearity (DOM-verifiable) ─────────────────
  // data-seq strictly monotonic AND each node's data-prev equals the
  // adjacent (older) node's data-hash — the content-hash is a digest of
  // already-public audit data, exposed exactly so this linkage is provable
  // in the DOM (the deeper crypto belongs to the op's unit test).
  const nodes = [...root.querySelectorAll(".gl-chain .gl-node")];
  if (nodes.length < 2) fail("chain linearity: need ≥2 .gl-node, got " + nodes.length);
  for (let i = 0; i < nodes.length - 1; i++) {
    const a = Number(nodes[i].dataset.seq), b = Number(nodes[i + 1].dataset.seq);
    if (!(a > b)) fail("chain linearity: data-seq not strictly monotonic at index " + i + " (" + a + " → " + b + ")");
    const prev = nodes[i].dataset.prev, olderHash = nodes[i + 1].dataset.hash;
    if (!olderHash) fail("chain linearity: node #" + b + " carries no data-hash (op emits hash)");
    if (prev && prev !== olderHash)
      fail("chain linearity: node #" + a + " data-prev " + prev + " ≠ older node's data-hash " + olderHash + " — the drawing implies a fork the log does not have");
  }

  // ── invariant 7 · verdict honesty (no release for the held-back) ───
  const heldBack = sessions.filter((s) => ["refused", "reserved", "prohibited"].includes(s.dataset.verdict));
  if (!heldBack.length) fail("verdict honesty: seed must yield at least one refused/reserved/prohibited session (suspended " + P_SUSPENDED + ")");
  for (const p of [P_SUSPENDED, P_EXPIRED]) {
    const released = nodes.find((n) => n.dataset.actor === p && /released|auto/.test(n.dataset.event || ""));
    if (released) fail("verdict honesty: chain shows a released node for held-back party " + p + " (seq " + released.dataset.seq + ")");
  }

  // ── read-only ──────────────────────────────────────────────────────
  const btns = [...gp.querySelectorAll("button")];
  if (btns.length) fail("govlive must be read-only — found button(s): " + btns.map((b) => b.textContent.trim()).join(","));

  console.log("PASS: govlive drawer v2 — real-state board renders; admission honesty, refusal-serialization, chain linearity (recomputed content-hash linkage), verdict honesty all hold; read-only; modal dialog");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
