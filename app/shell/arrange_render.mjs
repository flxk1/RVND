// Real DOM test for the ARRANGE view's two forms. LANES: agents as lanes with
// verdict-coloured run clips, read-only. MIX: a channel strip per agent (status
// word, per-grade ladder rendering the server matrix cell per rung verbatim —
// the seed paints a non-monotonic band, so a client-composed scalar ceiling
// would render false state — mono readout, oversight word, pending count, Hold)
// plus a bus header (name, pending total, Hold-all). Strips carry screens under
// one global mode selector (Overview | Autonomy | Checks | Reach | Time): each
// follows the mode, shows at most three value rows from its track_strip payload;
// a law-locked strip shows the reserved plate and no control. Stops on Checks
// and in CHECK·VERDICTS render through the shared stop card; state alone decides
// the affordance, reserved renders none. Writes: Hold / Hold-all + Pending link.
// Usage: node arrange_render.mjs <PORT> <FOLDER_CONTEXT> <AGENT_ID> <LOCKED_AGENT_ID>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3], AGENT = process.argv[4], LOCKED = process.argv[5];
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
  window.S.path = F; await window.reload(); await sleep(60);

  const arr = D.getElementById("arrange");
  if (!arr) fail("no #arrange container");
  if (arr.classList.contains("show")) fail("arrange should be hidden in PATCH view by default");
  // the self-description must state the mixer's writes, not claim read-only
  const al = arr.getAttribute("aria-label") || "";
  if (!/hold/i.test(al)) fail("#arrange aria-label does not state the mixer's Hold writes");
  if (/read-only|read only|changes nothing/i.test(al)) fail("#arrange aria-label still claims read-only");

  // switch to ARRANGE — LANES is the default form
  window.setView("arrange");
  for (let i = 0; i < 50 && !arr.querySelector(".arr-group"); i++) await sleep(60);
  if (!arr.classList.contains("show")) fail("arrange view did not show on toggle");
  const grp = arr.querySelector(".arr-group");
  if (!grp || !grp.textContent.includes(base(F))) fail("arrange group header does not name the workspace");
  const lanes = [...arr.querySelectorAll(".arr-row")];
  if (!lanes.length) fail("arrange shows no lanes");
  if (!lanes.some(l => l.querySelector(".arr-lh").textContent.includes(AGENT))) fail("no lane for the seeded agent " + AGENT);
  if (!arr.querySelector(".arr-clip")) fail("arrange shows no run clips (the signed events)");
  if (!arr.querySelector(".arr-legend")) fail("arrange has no verdict legend");
  // LANES stays a read-only projection: the only controls are the form sub-toggle
  const laneCtrls = [...arr.querySelectorAll("button")].filter(b => !b.classList.contains("arr-vt"));
  if (laneCtrls.length || arr.querySelector(".port")) fail("LANES must carry no control beyond the LANES|MIX sub-toggle");
  // the toggle reflects state non-visually
  const arrBtn = D.querySelector('.viewtog button[data-view="arrange"]');
  if (arrBtn.getAttribute("aria-checked") !== "true") fail("ARRANGE toggle not marked aria-checked");

  // MIX — one channel strip per agent, one bus header, Hold writes
  const mixBtn = arr.querySelector('[data-arrmode="mix"]');
  if (!mixBtn) fail("no LANES|MIX sub-toggle");
  mixBtn.click();
  for (let i = 0; i < 80 && !arr.querySelector(".mix-strip"); i++) await sleep(60);
  const bus = arr.querySelector(".mix-bus");
  if (!bus) fail("mixer shows no bus header");
  if (!bus.textContent.includes(base(F))) fail("bus header does not name the workspace");
  if (!/sign-off/.test(bus.textContent)) fail("bus header shows no pending total");
  if (!bus.querySelector(".mix-holdall")) fail("bus header has no Hold-all control");
  const env = bus.querySelector(".mix-busenv");
  if (!env) fail("bus header has no environment rollup line (console_snapshot)");
  if (!/workspace/.test(env.textContent)) fail("environment rollup names no workspace count");
  const strip = [...arr.querySelectorAll(".mix-strip")]
    .find(s => (s.querySelector(".mix-name") || { textContent: "" }).textContent.includes(AGENT));
  if (!strip) fail("no channel strip for the seeded agent " + AGENT);
  if (!/active|suspended|killed/.test((strip.querySelector(".mix-status") || { textContent: "" }).textContent)) fail("strip has no status word");
  // every rung shows its own server matrix cell verbatim — read the matrix and
  // the oversight band back from the server and compare cell for cell; the
  // seeded band is non-monotonic, so a client-composed ceiling cannot match.
  // The ladder is remappable policy (any number of grades), so the rung count is
  // the server's published grade count, not a fixed 5 (mirrors matrix_render).
  const snap = await window.tool("workspace_policy", { op: "snapshot", params: { folder_context: F } });
  const mxr = await window.tool("workspace_matrix", { op: "show", params: { folder_context: F } });
  const bandName = snap && snap.oversight_default_level;
  if (!bandName) fail("seed left no oversight band to read");
  const grades = Object.keys(mxr.matrix || {}).filter((k) => /^L\d+$/.test(k)).sort((a, b) => +a.slice(1) - +b.slice(1));
  const nG = grades.length;
  if (nG < 3) fail("server published too few grades to test a non-monotonic band: " + nG);
  if (strip.querySelectorAll(".fcell").length !== nG) fail("strip ladder rungs (" + strip.querySelectorAll(".fcell").length + ") must match the server's " + nG + " grades");
  const want = grades.map((gk) => mxr.matrix[gk][bandName]);
  if (!(want[1] === "block" && want[2] === "go")) fail("seed did not paint a non-monotonic band (got " + want.join(",") + ") — on a monotonic band a composed ceiling is indistinguishable from verbatim cells");
  const lights = [...strip.querySelectorAll(".mix-ladder .wsled")];
  if (lights.length !== nG) fail("ladder does not carry one per-rung matrix light per grade (" + nG + "), got " + lights.length);
  lights.forEach((el, i) => {
    const t = el.getAttribute("title") || "";
    if (t !== "L" + i + ": " + want[i]) fail("rung light L" + i + " does not show the server cell verbatim: got " + JSON.stringify(t) + ", server says " + want[i]);
  });
  const ladder = strip.querySelector(".mix-ladder");
  if (!ladder || !/ladder/.test(ladder.getAttribute("aria-label") || "")) fail("strip ladder carries no aria-label (colour must never be the only signal)");
  const lal = ladder.getAttribute("aria-label") || "";
  want.forEach((w, i) => { if (!lal.includes("L" + i + " " + w)) fail("ladder aria-label misses the server cell L" + i + " " + w + ": " + lal); });
  if (/\bcap\b|ceiling/i.test(lal + " " + strip.textContent)) fail("strip renders a ceiling the server did not decide");
  const ro = strip.querySelector(".mix-read");
  if (!ro || ro.textContent !== "L2 · matrix: " + want[2]) fail("mono readout does not state the earned level with its own matrix cell: " + (ro ? ro.textContent : "missing"));
  if (!/oversight/.test(strip.textContent)) fail("strip shows no oversight band word");
  if (!/sign-off/.test((strip.querySelector(".mix-pend") || { textContent: "" }).textContent)) fail("strip shows no pending count");
  const hold = strip.querySelector(".mix-hold");
  if (!hold) fail("strip has no Hold control");
  if (!/hold/i.test(hold.getAttribute("aria-label") || "")) fail("Hold control carries no aria-label");

  // STRIP SCREENS — one global mode selector; every strip's screen follows it
  const modes = arr.querySelector(".mix-modes");
  if (!modes) fail("mixer has no strip-screen mode selector");
  const mLabels = [...modes.querySelectorAll("[data-mixmode]")].map(b => b.textContent.trim()).join("|");
  if (mLabels !== "Overview|Autonomy|Checks|Reach|Time") fail("mode selector is not Overview|Autonomy|Checks|Reach|Time: " + mLabels);
  const screens = () => [...arr.querySelectorAll(".mix-screen")];
  if (screens().length !== 2) fail("expected one screen per strip (2 agents seeded), got " + screens().length);
  const scr = (pid) => screens().find(s => s.dataset.screen === pid) || fail("no screen for " + pid);
  const rowsOk = () => screens().every(s => { const n = s.querySelectorAll(".mix-srow").length; return n >= 1 && n <= 3; });
  const setMode = async (k) => {
    modes.querySelector('[data-mixmode="' + k + '"]').click();
    for (let i = 0; i < 50 && !screens().every(s => s.dataset.mode === k); i++) await sleep(30);
    if (!screens().every(s => s.dataset.mode === k)) fail("mode " + k + " did not swap every strip's screen");
    if (!rowsOk()) fail("a screen page in mode " + k + " renders more than three value rows (or none)");
  };
  // Overview is the default and speaks in sentences
  if (!screens().every(s => s.dataset.mode === "overview")) fail("screens do not default to Overview");
  if (!rowsOk()) fail("a screen page in Overview renders more than three value rows (or none)");
  if (!/1 sign-off waits on you/.test(scr(AGENT).textContent)) fail("Overview does not state the waiting count as a sentence: " + scr(AGENT).textContent);
  if (!/is active/.test(scr(AGENT).textContent)) fail("Overview shows no status sentence: " + scr(AGENT).textContent);
  // Autonomy — level with its server matrix cell, ask+block share field-to-field
  // from the meter tally with its window word stated (the tally is windowless)
  await setMode("autonomy");
  const ts = await window.tool("workspace_workflow", { op: "track_strip", params: { folder_context: F, party_id: AGENT } });
  const mv = (((ts || {}).strip || {}).meter || {}).verdicts || {};
  const den = (mv.auto || 0) + (mv.human || 0) + (mv.refused || 0);
  const wantShare = den ? (((mv.human || 0) + (mv.refused || 0)) + "/" + den) : "0/0 —";
  const at = scr(AGENT).textContent;
  if (!at.includes("L2 · matrix: " + want[2])) fail("Autonomy screen does not state the level with its server matrix cell: " + at);
  if (!at.includes("ask+block " + wantShare + " · all time")) fail("Autonomy ask+block share is not the meter's field-to-field value with its window word: " + at);
  if (/attenuation/i.test(arr.textContent)) fail("the ask+block share must never be labelled attenuation");
  // Checks — every stop renders through the shared stop card (class .stopcard,
  // data-stopcard = the state): the hold card carries the frozen phrase, the
  // form + signed X of Y as its bound-by line, and the one affordance — the
  // Pending deep-link; the law-locked strip renders the reserved card, which
  // carries no action whatever the cause
  await setMode("checks");
  const ct = scr(AGENT).textContent;
  if (!/four_eyes/.test(ct)) fail("Checks screen does not name the control form: " + ct);
  if (!/signed 0 of 2/.test(ct)) fail("Checks screen does not show the m-of-n meter: " + ct);
  const holdCard = scr(AGENT).querySelector('.stopcard[data-stopcard="hold"]');
  if (!holdCard) fail("Checks page does not render its stop through the shared stop card");
  if (!/needs a person/.test(holdCard.textContent)) fail("hold card does not carry the frozen phrase (needs a person): " + holdCard.textContent);
  const plink = holdCard.querySelector("[data-pending-link]");
  if (!plink) fail("hold stop card carries no deep-link to Pending");
  if (!/pending/i.test(plink.getAttribute("aria-label") || "")) fail("Pending deep-link carries no aria-label");
  const lscr = scr(LOCKED);
  if (!/reserved · by law/.test(lscr.textContent)) fail("law-locked strip screen shows no reserved plate: " + lscr.textContent);
  const resCard = lscr.querySelector('.stopcard[data-stopcard="reserved"]');
  if (!resCard) fail("law-locked Checks page does not render the reserved stop card");
  if (resCard.querySelector("button, [data-pending-link], [data-rules-link]")) fail("a reserved stop card must render no action");
  if (lscr.querySelector("button")) fail("a reserved screen must render no control");
  // Time — every clock carries its elapse direction, never a bare countdown
  await setMode("time");
  const tt = scr(AGENT).textContent;
  if (!/t−\d+[dhms] → halt/.test(tt)) fail("Time screen does not render the deadline with its direction (t−… → halt): " + tt);
  if (!/no clocks/.test(scr(LOCKED).textContent)) fail("a strip with no deadline does not say no clocks: " + scr(LOCKED).textContent);
  // Reach — channels with their floors; the channel-less strip states its emptiness in words
  await setMode("reach");
  const rt = scr(AGENT).textContent;
  if (!/1 channel/.test(rt)) fail("Reach screen does not count the channels: " + rt);
  if (!/feed · ingress · floor hold/.test(rt)) fail("Reach screen does not show the channel with its floor: " + rt);
  if (!/no channels/.test(scr(LOCKED).textContent)) fail("a channel-less strip does not say no channels: " + scr(LOCKED).textContent);
  // the persistent mode buttons are bound once per full render: N clicks on one
  // mode repaint each screen exactly N times — stacked handlers would double the
  // repaints on every click
  const paint0 = window._mixScreenHTML;
  let paints = 0;
  window._mixScreenHTML = function () { paints++; return paint0.apply(this, arguments); };
  const nScreens = screens().length;
  const checksBtn = modes.querySelector('[data-mixmode="checks"]');
  const CLICKS = 6;   // few enough that stacked handlers fail the count below instead of hanging the harness
  for (let k = 0; k < CLICKS; k++) { checksBtn.click(); await sleep(5); }
  window._mixScreenHTML = paint0;
  if (paints > CLICKS * nScreens) fail("mode clicks repaint beyond one per screen per click — handlers are stacking: " + paints + " repaints for " + CLICKS + " clicks × " + nScreens + " screens");
  if (paints < CLICKS * nScreens) fail("a mode click did not repaint every screen: " + paints + " repaints for " + CLICKS + " clicks × " + nScreens + " screens");
  if (!scr(AGENT).querySelector("[data-pending-link]")) fail("Checks deep-link lost after repeated mode clicks");
  await setMode("overview");

  // Hold writes through the existing party-status suspend (confirm is stubbed true)
  hold.click();
  let held = false;
  for (let i = 0; i < 80 && !held; i++) { await sleep(80);
    try { const pl = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: F } });
      const a = Array.isArray(pl) ? pl : ((pl && (pl.parties || pl.rows)) || []);
      const rec = a.find(p => (p.party_id || p.id) === AGENT);
      held = !!rec && rec.status === "suspended"; } catch (_) {} }
  if (!held) fail("Hold did not suspend the agent through the party-status write");
  for (let i = 0; i < 80 && !arr.querySelector(".mix-held"); i++) await sleep(60);
  if (!arr.querySelector(".mix-held")) fail("held strip does not state it is held");

  // back to LANES, then to PATCH — the node graph returns, arrange hides
  const lanesBtn = arr.querySelector('[data-arrmode="lanes"]');
  if (!lanesBtn) fail("no way back to LANES");
  lanesBtn.click();
  for (let i = 0; i < 50 && !arr.querySelector(".arr-row"); i++) await sleep(60);
  if (!arr.querySelector(".arr-row")) fail("LANES did not restore after MIX");
  window.setView("patch");
  await sleep(60);
  if (arr.classList.contains("show")) fail("arrange did not hide when switching to PATCH");
  if (!D.querySelector("#stage .node")) fail("PATCH view did not restore the node graph");

  // the inspector's CHECK·VERDICTS card renders its stops through the same
  // shared component as the strip screens: task2 is law-reserved, so a
  // reserved stop card must sit in #findings — and carry no action
  const fCard = D.querySelector('#findings .stopcard[data-stopcard="reserved"]');
  if (!fCard) fail("CHECK·VERDICTS does not render its reserved stop through the shared stop card");
  if (fCard.querySelector("button, [data-pending-link], [data-rules-link]")) fail("a reserved card in CHECK·VERDICTS must render no action");

  console.log("PASS: PATCH ⇄ ARRANGE — LANES (workspace group, agent lanes, verdict-coloured clips, legend; no controls beyond the sub-toggle) and MIX (bus header with pending total + Hold-all, per-agent strips with status word, per-grade ladder whose rung lights match the server matrix cell for cell on a non-monotonic band, oversight word, pending count; Hold suspends via party-status); strip screens follow one global mode selector (Overview | Autonomy | Checks | Reach | Time) with at most three value rows per page, deadline directions shown, and the law-locked strip's reserved plate with no control; Checks and CHECK·VERDICTS both render stops through the shared stop card, whose reserved state carries no action; self-description states the writes; PATCH restores the node graph");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
