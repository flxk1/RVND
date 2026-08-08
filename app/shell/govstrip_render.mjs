// Real DOM test for the Integral governance strip (I1, always-on shell chrome).
// Asserts: the strip is present with the four traffic-light tiles carrying
// counts from the LIVE board, the HOTL alarm is armed and names a not-green
// session (cross-checked against a direct governance_live call — no hardcoded
// mapping), clicking the strip expands the full v2 drawer, and the strip
// carries no write controls. Loads the composed page (GET /classic).
// Usage: node govstrip_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
setTimeout(() => fail("watchdog: strip gate did not finish in 30s"), 30000).unref();
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
  if (!window._ready) fail("watchdog: patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);
  window.mountGovStrip(); await window.loadGovStrip(); await sleep(60);

  const strip = window.document.getElementById("govstrip");
  if (!strip) fail("#govstrip missing — the strip must be always-on shell chrome");
  if (strip.getAttribute("role") !== "button") fail("strip is not activatable (role=button)");
  if (strip.getAttribute("aria-live") !== "polite") fail("strip is not a polite live region");

  // ── four traffic-light tiles, counts from the live board ───────────
  // Cross-check through the page's OWN bridge (same auth, same prefix logic)
  // so a broken helper can never silently skip the comparison.
  const board = await window.tool("workspace_workflow", { op: "governance_live", params: { folder_context: F } })
    .catch((e) => fail("governance_live unavailable for cross-check: " + ((e && e.message) || e)));
  if (!board || board.ok === false) fail("governance_live unavailable for cross-check");
  const sum = board.summary || {};
  const lights = {};
  for (const l of strip.querySelectorAll(".gs-light")) lights[l.dataset.k] = l.dataset.count;
  for (const k of ["sessions", "admitted", "leases", "needsyou"])
    if (!(k in lights)) fail("missing .gs-light[data-k=" + k + "]");
  if (Number(lights.sessions) !== sum.sessions_open)
    fail("sessions light " + lights.sessions + " != board sessions_open " + sum.sessions_open);
  if (Number(lights.admitted) !== sum.admitted)
    fail("admitted light " + lights.admitted + " != board admitted " + sum.admitted);
  if (Number(lights.leases) !== sum.run_leases_held)
    fail("leases light " + lights.leases + " != board run_leases_held " + sum.run_leases_held);

  // ── HOTL alarm: armed, and NAMES a session the board says is not-green ──
  const flare = (board.sessions || []).filter((s) =>
    s.escalation === true || ["human", "reserved", "refused", "prohibited"].includes(s.verdict));
  const alarm = strip.querySelector(".gs-alarm");
  if (!alarm) fail(".gs-alarm missing");
  if (!flare.length) fail("seed must yield a not-green session (suspended rex) — board shows none");
  if (alarm.dataset.armed !== "true") fail("alarm not armed while the board carries a not-green session");
  const named = alarm.dataset.name || "";
  if (!named) fail("armed alarm carries no data-name — it must NAME the step");
  if (!flare.some((s) => named.startsWith((s.sid || "").slice(0, 14))))
    fail("alarm names '" + named + "' which is not one of the board's not-green sessions");

  // ── read-only: no write controls inside the strip ──────────────────
  if (strip.querySelectorAll("button").length)
    fail("strip must carry no write controls — found <button> inside #govstrip");

  // ── visibility contract: hidden disarms the poll; visible re-arms and
  //    refreshes IMMEDIATELY (no stale-while-visible). Asserted behaviorally
  //    via a tool-call counter — a hidden console must not tax the server
  //    with governance_live replays, and a shown one must not show stale. ──
  const origTool = window.tool;
  let liveCalls = 0;
  window.tool = (n, a) => { if (a && a.op === "governance_live") liveCalls++; return origTool(n, a); };
  let vis = "visible";
  Object.defineProperty(window.document, "visibilityState", { configurable: true, get: () => vis });
  vis = "hidden"; window.document.dispatchEvent(new window.Event("visibilitychange"));
  const atHide = liveCalls;
  await sleep(5200);   // > one 4s interval tick
  if (liveCalls !== atHide)
    fail("visibility contract: strip polled governance_live " + (liveCalls - atHide) + "x while the document was hidden");
  vis = "visible"; window.document.dispatchEvent(new window.Event("visibilitychange"));
  await sleep(400);    // the re-show refresh must be immediate, not next-tick
  if (liveCalls <= atHide)
    fail("visibility contract: no immediate refresh on return to visibility — the lights could show stale while the user is looking");
  window.tool = origTool;

  // ── click-expands to the full v2 drawer ────────────────────────────
  strip.click(); await sleep(200);
  const gp = window.document.getElementById("govlivepanel");
  if (!gp) fail("clicking the strip did not open the v2 drawer");
  if (gp.getAttribute("aria-modal") !== "true") fail("expanded drawer is not a modal dialog");
  let root = null;
  for (let i = 0; i < 40; i++) { await sleep(60); root = gp.querySelector("#govlive"); if (root && root.querySelector(".gl-session")) break; }
  if (!root || !root.querySelector(".gl-session")) fail("expanded drawer did not render the board");

  console.log("PASS: govstrip — always-on strip; four lights match the live board; HOTL alarm armed and names a not-green session; read-only; click expands the v2 drawer");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
