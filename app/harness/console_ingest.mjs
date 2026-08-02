// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for Say (units/say.mjs) — the driver and the grower — driving
// the five-widget front door (console.html) over the shared store. Loads the
// actual console page in jsdom, boots it with the real store + Say factories,
// and exercises the live serve.py bridge (governance_chat / policy_ingest /
// patch_validate / help / party_*), never a stand-in.
//
// Usage: node console_ingest.mjs <PORT> <FOLDER>
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { bridgeGlobals } from "./render_harness.mjs";
import { createStore } from "../src/units/state.mjs";
import { createSay } from "../src/units/say.mjs";

const PORT = process.argv[2];
const F = process.argv[3];
const html = readFileSync(new URL("../src/console.html", import.meta.url), "utf8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };

const dom = new JSDOM(html, { runScripts: "dangerously", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o);
  window.confirm = () => true;
  window.__RVND_createStore = createStore;
  window.__RVND_createSay = createSay;
} });
const { window } = dom; const D = window.document;

// a direct bridge call, same wiring the page uses, for out-of-band assertions
async function bridge(tool, args) {
  const res = await fetch(`http://127.0.0.1:${PORT}/tool`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Workspaces-Token": process.env.RVND_BRIDGE_TOKEN || "" },
    body: JSON.stringify({ tool, args }),
  });
  return res.json();
}
async function auditLen() {
  const t = await bridge("workspace_audit", { op: "tail", params: { folder_context: F, limit: 200 } });
  return (t && Array.isArray(t.events)) ? t.events.length : 0;
}
async function agentActive(pid) {
  const r = await bridge("workspace_policy", { op: "party_list", params: { folder_context: F } });
  const parties = (r && (r.parties || r.rows)) || (Array.isArray(r) ? r : []);
  const p = parties.find((x) => (x.party_id || x.id) === pid);
  return p ? (p.status || "active") === "active" : false;
}

const POLICY = "Automated decisions must be approved by a compliance officer. "
  + "Offer letters may not be sent automatically. Personal data must be encrypted at rest. "
  + "Communications should feel right.";

async function main() {
  for (let i = 0; i < 160 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("front door did not boot (store never hydrated)");
  const rvnd = window.__RVND;
  if (!rvnd || !rvnd.store || !rvnd.say) fail("store or Say not exposed on window.__RVND");
  const { store, say } = rvnd;

  // focus the seeded workspace so folder_context defaults and the confirm to
  // apply is offered against a real chain
  const bus = store.getState().outside.buses.find((b) => b.path === F) || store.getState().outside.buses[0];
  if (!bus) fail("seed produced no visible workspace");
  await store.setActiveWorkspace(bus.path);

  // ---- (c precondition) capture the chain length before any grow ------------
  const before = await auditLen();

  // ================= GROWER: ingest a policy, render the ledger ==============
  await say.grow(POLICY, "policy");            // the explicit-ingest path (intent policy)
  for (let i = 0; i < 80 && !D.querySelector(".say-ledger"); i++) await sleep(25);
  const led = D.getElementById("say-out");
  const ledHtml = led ? led.innerHTML : "";
  if (!D.querySelector(".say-ledger")) fail("the grower did not render a ledger: " + ledHtml.slice(0, 300));

  // (a) the variety ledger shows BOTH express (absorbed) and residual (handed back)
  const twin = store.getState().chatContext.ledger;
  if (!twin || twin.ok !== true) fail("no valid twin on the store's chat context");
  const c = twin.classification || {};
  const nExpress = (c.express || []).length;
  const nResidual = (c.host || []).length + (c.policy || []).length + (c.unmapped || []).length;
  if (nExpress < 1) fail("ledger absorbed no express rules");
  if (nResidual < 1) fail("ledger handed nothing back to the human (no residual): " + JSON.stringify(c));
  if (!/Express — the governor absorbs these/.test(ledHtml)) fail("the express bucket is not labelled as absorbed");
  if (!/Handed back to you/.test(ledHtml)) fail("the residual is not labelled as handed back");
  // stated as a control patch, never a workflow
  if (!/Not a workflow/.test(ledHtml)) fail("the patch is not stated as a control patch (not a workflow)");
  // at least one express rule is a Loomground declaration (reserve/prohibit/…)
  if (!(c.express || []).some((x) => /^(reserve|prohibit|obligation|redress|grant)\b/.test(x)))
    fail("express rules are not Loomground declarations: " + JSON.stringify(c.express));

  // (b) the drafted control patch validates through loomground_lang
  const v = await bridge("workspace_workflow", { op: "patch_validate", params: { folder_context: F, netlist: twin.netlist } });
  if (!v || v.ok !== true) fail("the drafted patch does not validate through loomground_lang: " + JSON.stringify(v));
  if (!/Loomground-valid/.test(ledHtml)) fail("the ledger does not report the patch as Loomground-valid");

  // (c) nothing is applied without a human confirm
  if (twin.applied !== false) fail("the twin reports applied before any confirm");
  const applyBtn = led.querySelector('[data-say="apply"]');
  if (!applyBtn) fail("no confirm control offered to apply the patch");
  const afterGrow = await auditLen();
  if (afterGrow !== before) fail("the chain grew before any confirm — drafting wrote to the chain");

  // ================= DRIVER: the confirm gate ================================

  // (d1) a READ command runs and renders structured, with NO execute control
  say.clear();
  await say.submit("workspace_policy party_list folder_context=" + F);
  for (let i = 0; i < 80 && !D.querySelector(".say-tbl"); i++) await sleep(25);
  const readOut = D.getElementById("say-out");
  if (!readOut.querySelector(".say-tbl")) fail("a read did not render a structured table: " + readOut.innerHTML.slice(0, 200));
  if (readOut.querySelector('[data-say="run"]')) fail("a read raised an execute control — reads must just render");

  // (d2) a WRITE command raises a confirm-card and fires nothing until confirmed
  const beforeWrite = await auditLen();
  say.clear();
  await say.submit("workspace_policy party_status folder_context=" + F + " party_id=a1 status=suspended actor=app-user");
  for (let i = 0; i < 80 && !D.querySelector(".say-confirm"); i++) await sleep(25);
  const confOut = D.getElementById("say-out");
  if (!confOut.querySelector(".say-confirm")) fail("a mutating command did not raise a confirm-card: " + confOut.innerHTML.slice(0, 200));
  if (!confOut.querySelector('[data-say="run"]')) fail("the confirm-card offered no confirm control");
  if (!/recorded/i.test(confOut.textContent)) fail("the confirm-card does not say the write will be recorded");
  // nothing has fired: the agent is still active and the chain has not grown
  if (!(await agentActive("a1"))) fail("the write fired before the confirm was pressed");
  if ((await auditLen()) !== beforeWrite) fail("the chain grew before the confirm was pressed");

  console.log("PASS: Say — the grower renders the requisite-variety ledger (express absorbed as Loomground "
    + "declarations, residual handed back), the drafted control patch validates through loomground_lang, "
    + "nothing applies without the human confirm; the driver runs reads with no execute control and raises "
    + "a confirm-card for a mutating command that fires nothing until confirmed");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
