// Real DOM test for the Knob bindings drawer (MIDI-learn + All-Stop). jsdom has no Web MIDI,
// so we STUB navigator.requestMIDIAccess with a fake input. Learned CCs drive
// the discrete steps both ways: tighten is instant; loosen (ov_loosen,
// floor_down) is confirm-gated — a declined confirm changes nothing on the
// server, an accepted one moves exactly one step and the lamp shows the
// granted value. Reserved (all_stop) carries no continuous dial; All-Stop
// works without MIDI and SUSPENDS (not kills) parties.
// Usage: node controller_render.mjs <PORT> <FOLDER_CONTEXT>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const fakeInput = { type: "input", state: "connected", onmidimessage: null };
const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://localhost/", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o);
  window.navigator.requestMIDIAccess = () => Promise.resolve({ inputs: new Map([["in0", fakeInput]]), onstatechange: null });
  Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
  Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
} });
const { window } = dom; const D = window.document;
// confirm spy: the loosen paths are confirm-gated, so the tests flip the
// answer and read back what was asked.
let confirmOk = true, confirmMsgs = [];
window.confirm = (m) => { confirmMsgs.push(String(m || "")); return confirmOk; };
const emitCC = (d1, d2) => { if (fakeInput.onmidimessage) fakeInput.onmidimessage({ data: [0xb0, d1, d2] }); };
const snapOv = async () => { const p = await window.tool("workspace_policy", { op: "snapshot", params: { folder_context: F } }); return p && p.oversight_default_level; };
const ORDER = ["autonomous","notify","review","approve","supervised","manual"];
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);
  await window.openControllerPanel(); await sleep(200);
  const cp = D.getElementById("ctrlpanel");
  if (!cp) fail("knob bindings panel did not open");
  if (cp.getAttribute("aria-modal") !== "true") fail("not a modal dialog");
  if (!D.getElementById("ctl-allstop")) fail("no ALL-STOP button");
  let stxt = ""; for (let i = 0; i < 40; i++) { await sleep(60); stxt = D.getElementById("ctl-state").textContent; if (/Oversight \(server\)/.test(stxt)) break; }
  if (!/Oversight \(server\)/.test(stxt)) fail("lamps never read the server oversight state");
  // reserved control (all_stop) must carry no continuous dial / %
  const asRow = cp.querySelector('[data-action="all_stop"]');
  if (!asRow) fail("all_stop binding row missing");
  if (asRow.querySelector('input[type=range]') || /%/.test(asRow.textContent)) fail("a reserved control has a continuous dial — forbidden");
  // MIDI-learn: learn ov_tighten, emit a CC, assert server oversight tightened
  const learn = cp.querySelector('.ctl-learn[data-act="ov_tighten"]'); if (!learn) fail("no Learn for ov_tighten");
  const ovBefore = await snapOv();
  learn.click(); await sleep(20); emitCC(21, 64); await sleep(40);
  if (!/cc:21/.test(window.localStorage.getItem("rvnd.controller.bindings.v1") || "")) fail("learn did not persist the cc:21 binding");
  emitCC(21, 100); await sleep(240);
  const ovAfter = await snapOv();
  if (!(ORDER.indexOf(ovAfter) > ORDER.indexOf(ovBefore))) fail("MIDI CC did not tighten oversight on the SERVER (" + ovBefore + " -> " + ovAfter + ")");
  let lampTxt = ""; for (let i = 0; i < 30; i++) { await sleep(60); lampTxt = D.getElementById("ctl-state").textContent; if (lampTxt.includes(ovAfter)) break; }
  if (!lampTxt.includes(ovAfter)) fail("lamp does not show the server-granted oversight " + ovAfter);

  // ov_loosen: confirm-gated. Declined -> no server change (fail closed);
  // accepted -> exactly one step down and the lamp shows the granted value.
  const learnLoosen = cp.querySelector('.ctl-learn[data-act="ov_loosen"]'); if (!learnLoosen) fail("no Learn for ov_loosen");
  learnLoosen.click(); await sleep(20); emitCC(22, 64); await sleep(40);
  if (!/cc:22/.test(window.localStorage.getItem("rvnd.controller.bindings.v1") || "")) fail("learn did not persist the cc:22 binding");
  confirmOk = false; const declinedAt = confirmMsgs.length;
  emitCC(22, 100); await sleep(300);
  if (confirmMsgs.length <= declinedAt) fail("ov_loosen did not ask to confirm");
  if (!/loosen/i.test(confirmMsgs[confirmMsgs.length - 1])) fail("the loosen confirm must name the consequence: " + confirmMsgs[confirmMsgs.length - 1]);
  if ((await snapOv()) !== ovAfter) fail("a declined loosen confirm still changed server oversight");
  confirmOk = true;
  emitCC(22, 100);
  let ovLoosened = ovAfter;
  for (let i = 0; i < 40; i++) { await sleep(80); ovLoosened = await snapOv(); if (ovLoosened !== ovAfter) break; }
  if (ORDER.indexOf(ovLoosened) !== ORDER.indexOf(ovAfter) - 1) fail("loosen must step down exactly one level (" + ovAfter + " -> " + ovLoosened + ")");
  lampTxt = ""; for (let i = 0; i < 30; i++) { await sleep(60); lampTxt = D.getElementById("ctl-state").textContent; if (lampTxt.includes(ovLoosened)) break; }
  if (!lampTxt.includes(ovLoosened)) fail("lamp does not show the loosened oversight " + ovLoosened);

  // floor_down: confirm-gated like every loosen; one discrete 0.1 step.
  const snapFloor = async () => { const t = await window.tool("workspace_lock", { op: "threshold_get", params: { folder_context: F } }); return Number(t && t.threshold); };
  const floorBefore = await snapFloor();
  if (Math.abs(floorBefore - 0.5) > 1e-9) fail("seed floor expected 0.5, got " + floorBefore);
  const learnFloor = cp.querySelector('.ctl-learn[data-act="floor_down"]'); if (!learnFloor) fail("no Learn for floor_down");
  learnFloor.click(); await sleep(20); emitCC(23, 64); await sleep(40);
  confirmOk = false;
  emitCC(23, 100); await sleep(300);
  if (Math.abs((await snapFloor()) - 0.5) > 1e-9) fail("a declined floor_down confirm still changed the floor");
  confirmOk = true;
  emitCC(23, 100);
  let floorAfter = floorBefore;
  for (let i = 0; i < 40; i++) { await sleep(80); floorAfter = await snapFloor(); if (Math.abs(floorAfter - floorBefore) > 1e-9) break; }
  if (Math.abs(floorAfter - 0.4) > 1e-9) fail("floor_down must step down exactly 0.1 (0.5 -> " + floorAfter + ")");

  // All-Stop without MIDI: suspends every active party (reversible, not killed)
  const parties = async () => { const pl = await window.tool("workspace_policy", { op: "party_list", params: { folder_context: F } }); return Array.isArray(pl) ? pl : ((pl && (pl.parties || pl.rows)) || []); };
  if ((await parties()).filter(p => (p.status || "active") === "active").length < 1) fail("seed has no active party for All-Stop");
  D.getElementById("ctl-allstop").click();
  let arrA = [];
  for (let i = 0; i < 40; i++) { await sleep(100); arrA = await parties(); if (arrA.length && arrA.every(p => (p.status || "active") !== "active")) break; }
  if (arrA.filter(p => (p.status || "active") === "active").length !== 0) fail("All-Stop left an active party");
  if (arrA.some(p => p.status === "killed")) fail("All-Stop used irreversible 'killed' — must be 'suspended'");
  if (!arrA.some(p => p.status === "suspended")) fail("All-Stop did not suspend any party");
  console.log("PASS: knob bindings — CCs drive discrete steps both ways: tighten instant, ov_loosen/floor_down confirm-gated (declined = no change, accepted = one step, lamp shows granted); reserved has no dial; All-Stop suspends (not kills) without MIDI");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
