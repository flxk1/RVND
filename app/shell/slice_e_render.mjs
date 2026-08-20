// Real DOM test: drawers that only READ the
// signed record carry a visible "read-only" badge; a drawer that WRITES must NOT
// Opens the FOUR read-only drawers (Conformity,
// Sources & gaps, Standing facts, About → each must show the badge), the partial
// writers Audit (attestation battery → "reads · attests") and AI & Capture (pins
// skills → "reads · pins", never "read-only"), and Data (write → no badge).
// Standing facts is a pack bundle now (app/src/panels/legal.js, mounted
// through the panel-mount contract), so this gate loads the composed page
// (GET /classic) — a raw readFileSync of the shell source has no bundle to
// register it, and the drawer would never open.
// Usage: node slice_e_render.mjs <PORT> <FOLDER_CONTEXT>
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
const badge = (panelId) => { const p = D.getElementById(panelId); return p && p.querySelector(".robadge"); };
async function open(fn, panelId, label) {
  await window[fn](); for (let i = 0; i < 40 && !D.getElementById(panelId); i++) await sleep(50);
  if (!D.getElementById(panelId)) fail(label + " drawer did not open");
}
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  // Audit is NOT read-only any more — its attestation cards trigger the probe
  // battery (governed, recorded writes). Its badge must own that.
  await open("openAuditPanel", "auditpanel", "Audit");
  const ab = badge("auditpanel");
  if (!ab) fail("Audit drawer is missing its capability badge");
  if (/read-only/i.test(ab.textContent)) fail("Audit badge falsely claims read-only — it triggers the attestation battery (recorded writes)");
  if (!/reads.*attests/i.test(ab.textContent)) fail("Audit badge must say it reads · attests, not just read");
  D.querySelector("#auditx").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);

  // read-only drawers carry the badge, labelled read-only
  await open("openConformityPanel", "conformitypanel", "Conformity");
  if (!badge("conformitypanel")) fail("read-only Conformity drawer is missing its read-only badge");
  D.querySelector("#conformityx").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);

  // the remaining read-only drawers must ALSO carry the badge (all four, not a sample)
  for (const [fn, panel, label, closeId] of [
    ["openGrounderPanel", "grounderpanel", "Sources & gaps", "grounderx"],
    ["openLegalPanel", "legalpanel", "Standing facts", "legalx"],
    ["openAboutPanel", "aboutpanel", "About", "abx"],
  ]) {
    await open(fn, panel, label);
    const b = badge(panel);
    if (!b) fail("read-only " + label + " drawer is missing its read-only badge");
    if (!/read-only/i.test(b.textContent)) fail(label + " badge does not say read-only");
    D.querySelector("#" + closeId).dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await sleep(60);
  }

  // AI & Capture is NOT read-only — it pins skills (a governed, recorded write). Its badge
  // must own that: it carries a robadge, but it must NOT claim "read-only"; it must say it
  // reads AND pins. (Honesty: a partial-writer can't hide behind a read-only badge.)
  await open("openAIPanel", "aipanel", "AI & Capture");
  const aib = badge("aipanel");
  if (!aib) fail("AI & Capture drawer is missing its capability badge");
  if (/read-only/i.test(aib.textContent)) fail("AI & Capture badge falsely claims read-only — it pins skills (a recorded write)");
  if (!/reads.*pins/i.test(aib.textContent)) fail("AI & Capture badge must say it reads · pins, not just read");
  if (/read-only/i.test(D.getElementById("aipanel").getAttribute("aria-label") || "")) fail("AI & Capture aria-label still falsely claims read-only");
  D.querySelector("#aix").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);

  // a WRITE drawer must NOT claim to be read-only
  await open("openDataPanel", "datapanel", "Data");
  if (badge("datapanel")) fail("the Data drawer WRITES — it must not wear a read-only badge");
  D.querySelector("#datax").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(60);
  if (D.querySelector(".modal-scrim")) fail("a drawer leaked a scrim on close");

  console.log("PASS: read-only badges — four read-only drawers (Conformity, Sources & gaps, Standing facts, About) carry the badge; Audit says reads · attests and AI & Capture says reads · pins (not read-only); the Data write-drawer wears no badge");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
