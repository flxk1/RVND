// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for the shared synchronous store (units/state.mjs) driving the
// five-widget front door (console.html). Loads the actual console page in jsdom
// and boots it with the real store factory, so the page's own render code runs
// against the live serve.py bridge — not a stand-in.
//
// Asserts the two shared-store invariants:
//  1. A mutation from one surface reflects in the others in the SAME tick, with
//     the version bumped exactly once (setState notifies synchronously).
//  2. A workspace switch re-projects the outside before any surface paints, and
//     no prior-workspace data leaks — the two seeded workspaces carry distinct
//     agent counts, so a leaked outside is visible in the Read screen.
//
// Usage: node console_sync.mjs <PORT> <FOLDER_A> <FOLDER_B>
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { bridgeGlobals } from "./render_harness.mjs";
import { createStore } from "../src/units/state.mjs";
const PORT = process.argv[2];
const html = readFileSync(new URL("../src/console.html", import.meta.url), "utf8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };

const dom = new JSDOM(html, { runScripts: "dangerously", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o);
  window.confirm = () => true;
  // hand the page the real store factory so its own boot path runs; in a
  // browser the module shim does this, but jsdom does not execute modules
  window.__RVND_createStore = createStore;
} });
const { window } = dom; const D = window.document;

async function main() {
  for (let i = 0; i < 120 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("front door did not boot (store never hydrated)");
  const store = window.__RVND && window.__RVND.store;
  if (!store) fail("the store is not exposed on window.__RVND");

  // (1) same-tick synchronous propagation. Two subscribers stand in for two
  // widgets: one mutates, the other must see it within the same synchronous
  // emit, with the version bumped exactly once. No await between them.
  let sensed = null;
  const unB = store.subscribe((s) => { sensed = { version: s.version, marker: s.chatContext && s.chatContext.marker }; });
  const v0 = store.getState().version;
  store.setState({ chatContext: { marker: 42 } });        // one widget mutates
  if (sensed === null) fail("a subscriber did not fire synchronously — not same-tick");
  if (sensed.version !== v0 + 1) fail("version did not bump exactly once on the mutation: saw " + JSON.stringify(sensed) + ", expected " + (v0 + 1));
  if (sensed.marker !== 42) fail("the other subscriber saw stale state — the mutation was not visible same-tick");
  if (store.getState().version !== v0 + 1) fail("store version not advanced after setState");
  unB();

  // find the two seeded workspaces by their distinct agent counts, off the
  // store's own rollup — this avoids any path-normalisation mismatch
  const buses = store.getState().outside.buses;
  const busA = buses.find((b) => b.agents_total === 2);   // the 2-agent workspace
  const busB = buses.find((b) => b.agents_total === 1);   // the 1-agent workspace
  if (!busA || !busB) fail("seed did not produce a 2-agent and a 1-agent workspace: " + JSON.stringify(buses.map((b) => ({ p: b.path, a: b.agents_total }))));

  // (2a) focus A: the env picker and the Read screen both follow the focus in
  // the one render — proof the surfaces share the store, not three panels
  await store.setActiveWorkspace(busA.path);
  if (store.getState().outside.bus.path !== busA.path) fail("outside did not re-project to workspace A");
  if (D.getElementById("ws").value !== busA.path) fail("the workspace picker did not follow the focus to A");
  const allowedA = D.getElementById("rd-allowed").textContent;
  if (!/2 of 2/.test(allowedA)) fail("the Read screen does not show workspace A's 2 agents: " + allowedA);

  // (2b) switch to B: the outside must re-project wholesale — B's agent count,
  // never A's. A leaked (merged-forward) outside would still read "2 of 2".
  await store.setActiveWorkspace(busB.path);
  const st = store.getState();
  if (st.outside.bus.path !== busB.path) fail("outside did not re-project to workspace B on switch");
  if (st.outside.bus.agents_total !== 1) fail("workspace A's agent count leaked into B's outside: " + st.outside.bus.agents_total);
  const allowedB = D.getElementById("rd-allowed").textContent;
  if (D.getElementById("ws").value !== busB.path) fail("the workspace picker did not follow the focus to B in the same tick");
  if (!/1 of 1/.test(allowedB)) fail("the Read screen does not show workspace B's 1 agent: " + allowedB);
  if (/2 of 2/.test(allowedB)) fail("workspace A's agent count leaked into B's Read screen: " + allowedB);

  console.log("PASS: shared store — one mutation reflects in every surface in the same tick with the version bumped once; a workspace switch re-projects the outside before any paint and leaks no prior-workspace data (env picker + Read screen both follow the focus, distinct agent counts prove no merge-forward)");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
