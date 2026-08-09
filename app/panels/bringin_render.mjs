// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the Bring-in drawer (Set up > Bring-in, workspace_ingest).
// Asserts: the Set up menu entry; header + acts-on-the-record badge; the
// boundary copy; an ingest-file round-trip that lands pairs on the server; the
// URL fetch is confirm()-gated (declined fetches nothing, the confirm names
// the boundary crossing) and an accepted fetch lands a signed ledger row; a
// skill ingest round-trip (signed, id announced). The drawer ships as its own
// pack bundle (app/src/panels/bringin.js), registered through the panel-mount
// contract, so this gate loads the composed page (GET /classic) rather than a
// bare readFileSync of the shell source — a raw index.html would open a frame
// with nothing inside it.
// Usage: node bringin_render.mjs <PORT> <FOLDER> <INGEST_FILE> <URL>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3], INGEST = process.argv[4], URL_INGEST = process.argv[5];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const dom = new JSDOM(html, { runScripts: "dangerously", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o);
  Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
  Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
} });
const { window } = dom; const D = window.document;
const set = (id, val) => { const el = D.getElementById(id); if (!el) fail("missing input #" + id); el.value = val; };
const click = (id) => { const el = D.getElementById(id); if (!el) fail("missing button #" + id); el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); };
const call = (tn, op, params) => window.tool(tn, { op, params: Object.assign({ folder_context: F }, params || {}) });
const srlive = () => (D.getElementById("srlive") || {}).textContent || "";
// The bring-in submits announce their own text and never swap the panel's
// nodes, so the announce alone proves a write completed before the next one.
async function waitAnnounce(re, label) { let t = ""; for (let i = 0; i < 100; i++) { await sleep(80); t = srlive(); if (re.test(t)) return t; } fail(label + " — announce: " + t.slice(0, 200)); }

// Completion of a URL ingest is asserted on the SERVER LEDGER, never the
// announce region: the announce is a shared single-slot live region, so any
// later announce overwrites it between polls — exactly how this gate flaked
// on CI ("ingested — 2 pair(s), signed" displaced "URL fetched, signed"). The
// ledger row is durable single-writer truth; the announce text rides along
// only as evidence in the failure message. A terminal non-success state stops
// the poll early and fails loud — a genuinely failed ingest can NEVER pass
// this helper (the negative probe below proves it). Siblings that poll
// waitAnnounce for completion should adopt this shape if they ever flake.
async function waitLedgerRow(url, wantState, budgetMs, label) {
  const t0 = Date.now();
  let last = null;
  while (Date.now() - t0 < budgetMs) {
    const led = await call("workspace_ingest", "list_urls", {});
    last = ((led && led.urls) || []).find((u) => u.url === url) || null;
    if (last && last.state === wantState) return last;
    if (last && /error|failed|refused|denied/.test(String(last.state || ""))) break;
    await sleep(200);
  }
  fail(label + " — ledger row: " + JSON.stringify(last).slice(0, 160) + " · announce: " + srlive().slice(0, 120));
}
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  const setupMi = [...D.querySelectorAll('[aria-label="Set up"] .mi .mil')];
  if (!setupMi.some((s) => /^Bring-in$/.test(s.textContent))) fail("Set up menu has no Bring-in entry");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openBringInPanel(); await sleep(160);
  const bp = D.getElementById("bringinpanel");
  if (!bp) fail("bring-in drawer did not open");
  if (bp.getAttribute("aria-modal") !== "true") fail("not a modal dialog");
  if (!/Bring-in — source material/.test(bp.querySelector("b").textContent)) fail("header wrong: " + bp.querySelector("b").textContent);
  const b = bp.querySelector(".robadge");
  if (!b || !/acts on the record/i.test(b.textContent)) fail("missing the acts-on-the-record badge");
  if (!/crosses the boundary/i.test(bp.textContent)) fail("missing the boundary copy");

  // ingest-file round-trip: pairs land on the server
  set("ingPath", INGEST);
  click("ingpathbtn"); await sleep(400);
  let mem = null;
  for (let i = 0; i < 30; i++) { mem = await window.tool("workspace_memory", { op: "recent", params: { folder_context: F } }); if (Number(mem && mem.count) > 0) break; await sleep(100); }
  if (!(Number(mem && mem.count) > 0)) fail("ingest did not land any pair on the server — " + JSON.stringify(mem).slice(0, 160));

  // URL fetch crosses the boundary — confirm-gated; declined must not fetch,
  // and the confirm must name the crossing.
  let urlConfirms = [];
  window.confirm = (m) => { urlConfirms.push(String(m || "")); return false; };
  set("ingUrl", URL_INGEST);
  click("ingurlbtn"); await sleep(120);
  if (urlConfirms.length !== 1) fail("URL fetch (boundary-crossing) must confirm exactly once — got " + urlConfirms.length);
  if (!/CROSSES THE BOUNDARY/.test(urlConfirms[0])) fail("the URL confirm must name the boundary crossing: " + urlConfirms[0]);
  let led = await call("workspace_ingest", "list_urls", {});
  if (((led && led.urls) || []).some((u) => u.url === URL_INGEST)) fail("a declined confirm still landed a URL ledger row");

  // accepted fetch: completion bound to the ledger row (structured signal),
  // signed-ness bound to the row's recorded pair ids.
  urlConfirms = [];
  window.confirm = (m) => { urlConfirms.push(String(m || "")); return true; };
  click("ingurlbtn");
  const row = await waitLedgerRow(URL_INGEST, "fetched", 12000, "URL ingest did not complete");
  if (urlConfirms.length !== 1) fail("accepted URL fetch must confirm exactly once — got " + urlConfirms.length);
  if (!((row.pair_ids || []).length)) fail("fetched URL row carries no recorded pair ids — fetched but unsigned? " + JSON.stringify(row).slice(0, 160));

  // NEGATIVE PROBE — a genuinely failed ingest must still FAIL: submit a URL
  // whose fetch cannot succeed (connection refused instantly) and assert the
  // ledger NEVER reports it fetched. A completion check that cannot fail is
  // not a check; this proves the helper's failure direction stays live.
  const URL_DEAD = "http://public.test:1/unreachable.html";
  window.confirm = () => true;
  set("ingUrl", URL_DEAD);
  click("ingurlbtn"); await sleep(1500);
  led = await call("workspace_ingest", "list_urls", {});
  const dead = ((led && led.urls) || []).find((u) => u.url === URL_DEAD);
  if (dead && dead.state === "fetched") fail("negative probe: an unreachable URL reported state=fetched — the completion signal can false-green");

  // skill ingest round-trip from the textarea — signed, id announced
  set("ingSkill", [
    "---",
    "name: provenance-check",
    "description: 'Check a note for missing provenance fields.'",
    "---",
    "",
    "# Provenance check",
    "",
    "1. Read the note.",
    "2. Flag missing provenance fields.",
  ].join("\n"));
  click("ingskillbtn");
  await waitAnnounce(/skill ingested — user:app-user\/provenance-check, signed/, "skill ingest did not complete");

  console.log("PASS: bring-in drawer — Set up entry; acts-on-the-record badge; boundary copy; ingest-file round-trip lands pairs; URL fetch confirm names the crossing, declined fetches nothing, accepted lands a fetched ledger row; skill ingest signed with its id announced");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
