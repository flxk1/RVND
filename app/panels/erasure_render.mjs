// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the Erasure drawer (Rules > Erasure, workspace_erase).
// Asserts: the Rules menu entry; header + acts-on-the-record badge; the scoping
// copy (this machine's record, no reach into external systems); the Status and
// Request cards; a sweep preview round-trip that finds the seeded subject; the
// request is confirm()-gated (declined confirm queues nothing); an accepted
// request returns an id whose status reads back "requested, not yet executed".
// The drawer ships as its own pack bundle (app/src/panels/erasure.js),
// registered through the panel-mount contract, so this gate loads the
// composed page (GET /classic) rather than a bare readFileSync of the shell
// source — a raw index.html would open a frame with nothing inside it.
// Usage: node erasure_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
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
async function waitEo(re, label) { let t = ""; for (let i = 0; i < 60; i++) { await sleep(60); t = (D.getElementById("eraseout") || {}).textContent || ""; if (re.test(t)) return t; } fail(label + " — got: " + t.slice(0, 200)); }
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  const rulesMi = [...D.querySelectorAll('[aria-label="Rules"] .mi .mil')];
  if (!rulesMi.some((s) => /^Erasure$/.test(s.textContent))) fail("Rules menu has no Erasure entry");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openErasurePanel(); await sleep(160);
  const ep = D.getElementById("erasurepanel");
  if (!ep) fail("erasure drawer did not open");
  if (ep.getAttribute("aria-modal") !== "true") fail("not a modal dialog");
  if (!/Erasure — the right to be removed/.test(ep.querySelector("b").textContent)) fail("header wrong: " + ep.querySelector("b").textContent);
  const b = ep.querySelector(".robadge");
  if (!b || !/acts on the record/i.test(b.textContent)) fail("missing the acts-on-the-record badge");
  // scoping: this machine's record only — never external reach
  if (!/this machine[’']s record/i.test(ep.textContent)) fail("missing the this-machine scoping copy");
  if (!/does not reach into any external system/i.test(ep.textContent)) fail("missing the no-external-reach scoping copy");
  const t = D.getElementById("erout").textContent;
  if (!/Status/.test(t) || !/Request/.test(t)) fail("Status/Request cards missing: " + t.slice(0, 160));

  // sweep preview round-trip — the seeded subject is found, no writes
  set("erSubject", "victim\u0040example.com");
  click("ersweepbtn");
  const sw = await waitEo(/hit\(s\) — no writes made/, "sweep preview did not render");
  if (!/[1-9]\d* hit\(s\)/.test(sw)) fail("sweep found no hits for the seeded subject — " + sw.slice(0, 120));

  // request is confirm-gated: declined confirm queues nothing
  let confirms = 0, accept = false; window.confirm = () => { confirms += 1; return accept; };
  set("erRequester", "alex"); set("erReason", "rtbf");
  click("erreqbtn"); await sleep(150);
  if (confirms === 0) fail("erase request (destructive) is not confirm-gated — no confirm fired");
  if (/request queued/.test((D.getElementById("eraseout") || {}).textContent || "")) fail("declined confirm still queued a request");

  // accepted request queues and its status reads back
  accept = true; click("erreqbtn");
  const rq = await waitEo(/request queued — id/, "accepted request did not queue");
  const m = rq.match(/request queued — id\s+(\S+)/);
  if (!m) fail("no request id rendered — " + rq.slice(0, 120));
  set("erReqId", m[1]);
  click("erstatbtn");
  await waitEo(/requested, not yet executed/, "status did not read back the queued request");

  console.log("PASS: erasure drawer — Rules entry; acts-on-the-record badge; this-machine scoping copy; sweep preview finds the subject with no writes; request confirm-gated; queued request id reads back as requested-not-executed");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
