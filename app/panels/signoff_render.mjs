// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the sign-off widget (src/signoff.html): the page the
// action link opens on the delegate's own device. Seeded: two open decisions,
// a link minted for party "dana" on one of them. Asserts: a valid token
// renders exactly the bound decision — acting-as line, quorum progress,
// deadline with its on-elapse direction — and never the other decision;
// approving records through the token and the page states the outcome; an
// invalid token renders a refusal in words and lists nothing.
// Usage: node signoff_render.mjs <PORT> <FOLDER> <TOKEN> <QUERIES_JSON>
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { bridgeGlobals } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3], TOKEN = process.argv[4];
const Q = JSON.parse(process.argv[5]);
const html = readFileSync(new URL("../src/signoff.html", import.meta.url), "utf8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };

function load(url) {
  return new JSDOM(html, {
    url,
    runScripts: "dangerously",
    beforeParse(window) {
      bridgeGlobals(window, PORT);
      window.fetch = (u, o) => fetch(u, o);
    },
  });
}

async function ready(window) {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("the widget did not finish booting");
}

async function bridge(tool, args) {
  const r = await fetch(`http://127.0.0.1:${PORT}/tool`, {
    method: "POST",
    headers: { "Content-Type": "application/json",
               "X-Workspaces-Token": process.env.RVND_BRIDGE_TOKEN || "" },
    body: JSON.stringify({ tool, args }) });
  return r.json();
}

async function main() {
  // 1 — a valid token renders exactly the bound decision
  const dom = load(`http://127.0.0.1:${PORT}/sign?folder=${encodeURIComponent(F)}&token=${encodeURIComponent(TOKEN)}`);
  const { window } = dom; const D = window.document;
  await ready(window);
  let text = D.getElementById("card").textContent;
  if (!text.includes(Q.bound)) fail("the bound decision's question is missing — got: " + text.slice(0, 200));
  if (text.includes(Q.other)) fail("a decision the token is not bound to rendered — the widget must show only bound items");
  if (!/acting as dana/.test(text)) fail("acting-as line does not name the token's party");
  if (!/signed 0 of 1/.test(text)) fail("quorum progress line missing — got: " + text.slice(0, 300));
  if (!/decide by 2030-01-01/.test(text)) fail("deadline missing from the card");
  if (!/widens to legal/.test(text)) fail("the deadline's on-elapse direction is missing — a deadline never renders bare");

  // 2 — approve records through the token and the page states it
  D.querySelector('input[name="opt"][value="erase"]').checked = true;
  D.getElementById("rat").value = "the retention window argument does not hold here";
  D.getElementById("approve").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  for (let i = 0; i < 50; i++) { await sleep(80); text = D.getElementById("card").textContent; if (/Recorded, signed/.test(text)) break; }
  if (!/Recorded, signed/.test(text)) fail("approve did not record — got: " + text.slice(0, 300));
  if (!/Erase everything now/.test(text)) fail("the outcome does not state the recorded choice");
  const pending = await bridge("workspace_dispatch", { op: "decision_pending", params: { folder_context: F } });
  if ((pending.pending || []).some((p) => p.query === Q.bound)) fail("the decision is still pending on the server after the recorded approval");

  // 3 — an invalid token renders a refusal, never a list
  const bad = load(`http://127.0.0.1:${PORT}/sign?folder=${encodeURIComponent(F)}&token=garbage`);
  await ready(bad.window);
  const badText = bad.window.document.getElementById("card").textContent;
  if (bad.window.document.getElementById("approve")) fail("an invalid token must render no sign controls");
  if (badText.includes(Q.bound) || badText.includes(Q.other)) fail("an invalid token leaked decision content");
  if (!/malformed link token|did not verify|no longer open/.test(badText)) fail("the refusal must speak in words — got: " + badText.slice(0, 200));

  console.log("PASS: sign-off widget — bound decision only, with party, quorum and deadline direction; approve recorded and stated; invalid token refused in words");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
