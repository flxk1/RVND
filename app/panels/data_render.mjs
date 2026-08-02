// Real DOM test for the Local data drawer (workspace_memory/mirror) — read + WRITE.
// Asserts the Memory + Mirror sections render, the erase and bring-in sections
// are gone (they live in Rules > Erasure and Set up > Bring-in), no %, the
// server-decides honesty line, a memory.remember round-trip, a mirror
// generate + approve round-trip, and that the loosening (un_redact) control
// is confirm()-gated (a declined confirm performs nothing).
// The drawer ships as its own pack bundle (app/src/panels/data.js), registered
// through the panel-mount contract, so this gate loads the composed page
// (GET /classic) rather than a bare readFileSync of the shell source — a raw
// index.html would open a frame with nothing inside it.
// Usage: node data_render.mjs <PORT> <FOLDER_CONTEXT> <MIRROR_SRC>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3], MIRROR_SRC = process.argv[4];
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
const doc = window.document;
const set = (id, val) => { const el = doc.getElementById(id); if (!el) fail("missing input #" + id); el.value = val; };
const click = (id) => { const el = doc.getElementById(id); if (!el) fail("missing button #" + id); el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); };
async function waitOut(re, label) { let t = ""; for (let i = 0; i < 60; i++) { await sleep(60); t = doc.getElementById("dtout").textContent; if (re.test(t)) return t; } fail(label + " — got: " + t.slice(0, 300)); }
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openDataPanel();
  await sleep(160);
  const dp = doc.getElementById("datapanel");
  if (!dp) fail("data panel did not open");
  if (dp.getAttribute("aria-modal") !== "true") fail("data panel is not a modal dialog");
  if (!/server\b[\s\S]*decides/i.test(dp.textContent)) fail("missing the 'server decides' honesty line");

  let txt = await waitOut(/Memory[\s\S]*Mirror/, "Memory + Mirror section headers did not render");
  if (/%/.test(txt)) fail("drawer renders a percentage — doctrine forbids a 0-1 dial/score");
  if (!doc.getElementById("membtn")) fail("missing memory write control");
  // erase + bring-in are re-homed (Rules > Erasure, Set up > Bring-in) — not here
  if (doc.getElementById("erreqbtn") || doc.getElementById("ingpathbtn")) fail("erase/bring-in controls still render in Local data — they were re-homed");
  if (/Erase\b|Ingest\b/.test(txt)) fail("erase/ingest section headers still render in Local data");

  // WRITE round-trip: remember a triple -> appears after reload
  const subj = "rt_subject_" + Date.now();
  set("memS", subj); set("memP", "is_a"); set("memO", "test_object");
  click("membtn");
  txt = await waitOut(new RegExp(subj), "remembered triple did not appear after reload");

  const call = (tn, op, params) => window.tool(tn, { op, params: Object.assign({ folder_context: F }, params || {}) });
  const srlive = () => (doc.getElementById("srlive") || {}).textContent || "";
  async function waitAnnounce(re, label) { let t = ""; for (let i = 0; i < 100; i++) { await sleep(80); t = srlive(); if (re.test(t)) return t; } fail(label + " — announce: " + t.slice(0, 200)); }
  // Every ok write announces its OWN text, then loadData swaps the whole panel.
  // Wait for both: the announce proves the write completed (a later announce
  // would overwrite srlive if two submits were ever in flight), and the node
  // swap proves the re-render landed, so the next set()/click() can't hit an
  // input the swap is about to replace. The swap check must see a NEW node,
  // not merely a change: loadData first paints a "reading the local facades…"
  // placeholder with NO inputs, so getElementById returning null means the
  // re-render is still in flight, not that it landed.
  async function announcedWrite(btnId, re, label) {
    const before = doc.getElementById(btnId);
    click(btnId);
    const t = await waitAnnounce(re, label);
    for (let i = 0; i < 100; i++) { const b = doc.getElementById(btnId); if (b && b !== before) return t; await sleep(80); }
    fail(label + " — panel did not re-render after the write");
  }

  // WRITE round-trip 2: generate a mirror from a source file, then approve it
  set("mirSrc", MIRROR_SRC);
  await announcedWrite("mirgenbtn", /mirror generated — \d+ span\(s\), signed/, "mirror generate did not complete");
  const ml = await call("workspace_mirror", "list", { kind: "lock" });
  if (!ml || ml.ok === false || !(ml.mirrors || []).length) fail("generated mirror not listed: " + JSON.stringify(ml));
  const mpath = ml.mirrors[0].mirror_path;
  await waitOut(/mirror\(s\) under this folder/, "mirror card did not refresh after generate");
  set("mirPath", mpath); set("mirApprover", "alex");
  await announcedWrite("mirapprbtn", /mirror approved, signed/, "mirror approve did not complete");
  const mo = await call("workspace_mirror", "list", { kind: "oversight" });
  if (!mo || mo.ok === false || !(mo.mirrors || []).length) fail("approved mirror not promoted to oversight: " + JSON.stringify(mo));
  if (!doc.getElementById("urMirPath")) fail("panel did not settle after the mirror writes");

  // confirm-gating: the loosening (un_redact) control must route through
  // confirm() — spy on confirm (the actual gate).
  let confirms = 0; window.confirm = () => { confirms += 1; return false; };
  set("urMirPath", "/x/mirror"); set("urSpan", "s1"); set("urKey", "ck1");
  confirms = 0; click("urbtn"); await sleep(120);
  if (confirms === 0) fail("un_redact (loosening) is not confirm-gated — no confirm fired");
  // a forward action (remember) must NOT prompt confirm
  confirms = 0; set("memS", "fwd"); set("memP", "is"); set("memO", "x"); click("membtn"); await sleep(120);
  if (confirms !== 0) fail("a forward action (remember) must not require confirm");

  console.log("PASS: local data drawer — memory + mirror sections; erase/bring-in re-homed away; remember round-trip; mirror generate+approve promotes to oversight; un_redact confirm-gated; modal");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
