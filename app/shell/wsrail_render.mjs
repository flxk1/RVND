// Real DOM test for the Workspaces rail — a channel per workspace. Seeds a
// parent, a registered child under it, and an independent workspace; asserts the
// rail renders one strip per workspace each with discrete L0–L4 autonomy LEDs and
// no nested interactive controls (name + mute are siblings, strip is a group),
// the current workspace is marked, the child shows a group-bus tag, the parent
// shows a send→, the master All-Stop is wired to allStopAll, and clicking another
// workspace's NAME button focuses it (S.path switches).
// Usage: node wsrail_render.mjs <PORT> <PARENT> <CHILD> <INDEP>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], PARENT = process.argv[3], CHILD = process.argv[4], INDEP = process.argv[5];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const base = (p) => (p || "").replace(/\/+$/, "").split("/").pop();
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
  window.S.path = PARENT; await window.reload(); await sleep(60);

  // master All-Stop is wired to the all-workspaces handler
  const master = D.getElementById("wsmaster");
  if (!master) fail("master All-Stop button missing");
  if (!/allStopAll/.test(master.getAttribute("onclick") || "")) fail("master All-Stop is not wired to the all-workspaces handler");

  // one strip per workspace
  let strips = [];
  for (let i = 0; i < 50; i++) { await sleep(60); strips = [...D.querySelectorAll(".wschan")]; if (strips.length >= 3) break; }
  if (strips.length < 3) fail("expected a channel strip per workspace (>=3), got " + strips.length);
  const txt = D.getElementById("wschans").textContent;
  for (const f of [PARENT, CHILD, INDEP]) if (!txt.includes(base(f))) fail("workspace strip missing: " + base(f));

  // NO nested interactive controls (the WCAG nesting bug): strip is a group; name + mute are buttons
  for (const s of strips) {
    if (s.getAttribute("role") === "button") fail("strip is role=button — interactive controls must not nest inside it");
    if (!s.querySelector("button.wsname")) fail("strip has no name focus-button");
    if (!s.querySelector("button.wmute")) fail("strip has no mute button");
  }

  // discrete L0–L4 autonomy LEDs — five pips per strip, labelled non-visually
  for (const s of strips) {
    const leds = s.querySelector(".wsleds");
    if (!leds) fail("strip missing the L0–L4 autonomy LEDs");
    if (leds.querySelectorAll(".wsled").length !== 5) fail("autonomy ladder must have exactly 5 discrete LEDs");
    if (!/autonomy/i.test(leds.getAttribute("aria-label") || "")) fail("LED ladder has no non-visual (aria) autonomy label");
  }

  // the current workspace (PARENT) is marked
  const onStrip = D.querySelector(".wschan.on");
  if (!onStrip || !onStrip.textContent.includes(base(PARENT))) fail("current workspace not marked on its strip");

  // hierarchy: the child shows a group-bus tag naming its parent; the parent shows a send→
  const childStrip = strips.find((s) => s.querySelector(".wsname").textContent.trim() === base(CHILD));
  if (!childStrip) fail("child strip not found");
  const grp = childStrip.querySelector(".wsgroup");
  if (!grp || !grp.textContent.includes(base(PARENT))) fail("child strip missing group-bus tag naming its parent");
  const parentStrip = strips.find((s) => s.querySelector(".wsname").textContent.trim() === base(PARENT));
  if (!parentStrip.querySelector(".wssend")) fail("parent strip missing the send→ (publish-downward) affordance");
  // an independent workspace has neither
  const indepStrip = strips.find((s) => s.querySelector(".wsname").textContent.trim() === base(INDEP));
  if (indepStrip.querySelector(".wsgroup") || indepStrip.querySelector(".wssend")) fail("independent workspace should show neither group nor send");

  // click the INDEPENDENT workspace's NAME button → focus switches
  const nameBtn = indepStrip.querySelector(".wsname");
  nameBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  for (let i = 0; i < 40 && base(window.S.path || "") !== base(INDEP); i++) await sleep(50);
  if (base(window.S.path || "") !== base(INDEP)) fail("clicking a workspace name did not focus it (S.path stayed " + window.S.path + ")");

  // consistency: focusing a folder that is NOT a saved workspace must still get a
  // rail channel (never an empty rail while the canvas shows that folder's agents),
  // flagged unsaved with a "save to rail" affordance.
  // (let the focus-click's boot() fully settle first — it resets S.path at its tail)
  await sleep(350);
  const ghost = INDEP + "/scratch-" + base(INDEP);
  window.S.path = ghost; await window.loadWsRail(); await sleep(120);
  const ghostStrip = [...D.querySelectorAll(".wschan")].find((s) => s.querySelector(".wsname").textContent.trim() === base(ghost));
  if (!ghostStrip) fail("an unsaved focused workspace has no rail channel — rail/canvas can disagree");
  if (!ghostStrip.querySelector("[data-save]")) fail("unsaved workspace strip has no 'save to rail' affordance");

  // MUTE — the per-channel safety control must actually suspend that workspace's
  // agents (not merely exist). Mute CHILD, then prove its agent is suspended on the chain.
  const muteStrip = [...D.querySelectorAll(".wschan")].find((s) => s.querySelector(".wsname").textContent.trim() === base(CHILD));
  if (!muteStrip) fail("child strip not found for mute test");
  const childBefore = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: CHILD } });
  const cB = (Array.isArray(childBefore) ? childBefore : (childBefore.parties || childBefore.rows || []));
  if (!cB.some((p) => (p.status || "active") === "active")) fail("precondition: CHILD should start with an active agent");
  muteStrip.querySelector(".wmute").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  for (let i = 0; i < 40; i++) { await sleep(60); const r = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: CHILD } }); const a = (Array.isArray(r) ? r : (r.parties || r.rows || [])); if (a.length && a.every((p) => (p.status || "active") !== "active")) break; }
  const childAfter = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: CHILD } });
  const cA = (Array.isArray(childAfter) ? childAfter : (childAfter.parties || childAfter.rows || []));
  if (cA.some((p) => (p.status || "active") === "active")) fail("clicking mute did not suspend the workspace's agent on the chain");

  // SOLO — per-channel, two flavors. View-solo: dims others, NO governance change.
  // Govern-solo (isolate): suspends every OTHER workspace's agents, not the soloed one's.
  const fresh = () => [...D.querySelectorAll(".wschan")];
  const byName = (n) => fresh().find((s) => s.querySelector(".wsname").textContent.trim() === base(n));
  const pStrip = byName(PARENT);
  if (!pStrip.querySelector(".wsolo")) fail("strip has no per-channel solo button");
  pStrip.querySelector(".wsolo").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  if (!byName(PARENT).classList.contains("solo")) fail("soloing a strip did not mark it .solo");
  if (!byName(INDEP).classList.contains("dim")) fail("view-solo did not dim the non-soloed strips");
  if (D.getElementById("wsisolate").style.display === "none") fail("govern-solo (isolate) control did not appear when soloing");
  // view-solo is purely visual — the non-soloed workspace's agent is still active
  const indepBefore = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: INDEP } });
  const aB = (Array.isArray(indepBefore) ? indepBefore : (indepBefore.parties || indepBefore.rows || []));
  if (!aB.some((p) => (p.status || "active") === "active")) fail("view-solo wrongly changed governance (agent suspended)");

  // govern-solo: isolate suspends the OTHERS, leaves the soloed one running
  D.getElementById("wsisolate").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  for (let i = 0; i < 40; i++) { await sleep(60); const r = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: INDEP } }); const a = (Array.isArray(r) ? r : (r.parties || r.rows || [])); if (a.length && a.every((p) => (p.status || "active") !== "active")) break; }
  const indepAfter = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: INDEP } });
  const aA = (Array.isArray(indepAfter) ? indepAfter : (indepAfter.parties || indepAfter.rows || []));
  if (aA.some((p) => (p.status || "active") === "active")) fail("isolate did not suspend a non-soloed workspace's agent");
  const parentAfter = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: PARENT } });
  const aP = (Array.isArray(parentAfter) ? parentAfter : (parentAfter.parties || parentAfter.rows || []));
  if (!aP.some((p) => (p.status || "active") === "active")) fail("isolate wrongly suspended the SOLOED workspace's own agent");

  console.log("PASS: workspaces rail — strip per workspace; no nested interactives; discrete L0–L4 LEDs (aria-labelled); current marked; child group-bus + parent send→; master All-Stop spans all; name-click focuses; unsaved gets a channel + save; mute suspends the workspace's agents on the chain; per-channel solo: view-solo dims (no governance change), govern-solo isolates the others");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
