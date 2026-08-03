// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// RV-05: fail-closed failure-injection gate. Drives the governance console
// against a bridge forced into each failure mode and asserts the connection
// surface reads DEGRADED — never a calm "live" or an empty-looking "no
// workspace" that a down/refusing server is indistinguishable from. "Server
// decides, client renders" is only safe if a fail-closed server is not fronted
// by a fail-open display.
//
// Modes (argv[4]): healthy (control) | error (500-shaped error response) |
// hang (call never returns → client deadline aborts) | revoked (session token
// rejected mid-session). The server-side faults are installed by the paired
// harness on serve._facade_call; revoked drives a bad token from the page.
//
// Usage: node failclosed_render.mjs <PORT> <FOLDER> <mode>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";

const PORT = process.argv[2], F = process.argv[3], MODE = process.argv[4] || "healthy";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: [" + MODE + "] " + m); process.exit(1); };

const html = await fetchComposedPage(PORT);
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    bridgeGlobals(window, PORT);
    window.fetch = (u, o) => fetch(u, o);
    // The client bounds /tool calls with a Promise.race timer (no AbortController,
    // so no jsdom realm issue). Only the hang mode needs a tight deadline so its
    // abort fires fast; the others keep a generous one so a cold real call is not
    // mistaken for a hang.
    window.__WORKSPACES_TIMEOUT_MS__ = (MODE === "hang") ? 600 : 12000;
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const D = window.document;
const connState = () => { const el = D.getElementById("conn"); return el ? (el.getAttribute("data-conn") || "") : "(none)"; };
const connText = () => { const el = D.getElementById("conn"); return el ? el.textContent : ""; };
const waitConn = async (want) => { for (let i = 0; i < 120 && connState() !== want; i++) await sleep(25); };

async function main() {
  for (let i = 0; i < 160 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("console did not boot within the deadline (did a hang block the whole boot?)");

  if (MODE === "healthy") {
    window.S.path = F; await window.reload();
    for (let i = 0; i < 40 && connState() === ""; i++) await sleep(25);
    if (connState() === "degraded") fail("a HEALTHY bridge wrongly reads degraded: " + JSON.stringify(connText()));
    if (!["live", "nows"].includes(connState())) fail("healthy bridge conn is neither live nor nows: " + connState());
    if (window.S.bridgeFault) fail("healthy bridge set S.bridgeFault: " + window.S.bridgeFault);
    console.log("PASS: [healthy] bridge reads '" + connState() + "' (not degraded); the fault state is earned, not always-on");
    process.exit(0);
  }

  if (MODE === "revoked") {
    // Prove the degraded state is EARNED: a healthy boot first reads live/nows,
    // then the token is revoked and the very next read must flip to degraded —
    // no stale "live" left on screen.
    window.S.path = F; await window.reload();
    if (connState() === "degraded") fail("revoked: degraded BEFORE the token was revoked (control failed)");
    if (!["live", "nows"].includes(connState())) fail("revoked: pre-revocation conn not live/nows: " + connState());
    window.__WORKSPACES_TOKEN__ = "revoked-invalid-token";
    await window.reload();
  } else {
    // error / hang: the harness patched serve._facade_call before boot, so the
    // auto-boot already failed; also drive governance_graph through the fault.
    window.S.path = F; await window.reload();
  }

  await waitConn("degraded");
  if (connState() !== "degraded")
    fail("bridge fault did NOT surface as degraded — conn=" + connState() + " text=" + JSON.stringify(connText()));
  if (connState() === "live") fail("a failing bridge still shows a calm 'live'");
  if (!/unverified|unreachable/.test(connText())) fail("degraded text is not explicit about the fault: " + JSON.stringify(connText()));
  if (!window.S.bridgeFault) fail("conn shows degraded but S.bridgeFault is not set (state/display disagree)");
  // no stale/fake verdict: the demo sample must not be presented as a real graph
  if (window.S.g && window.S.g._sample && window.S.path)
    fail("a failing read left the DEMO sample graph on screen for a real folder (stale/fake verdict)");
  // Global stale-session recovery: a 403 (revoked token) — and only a 403 — must
  // raise the reconnect banner (a reload pulls a fresh token). A down/hung server
  // (error/hang) must NOT raise it, since reloading cannot fix it.
  const banner = window.document.getElementById("reconnect-banner");
  if (MODE === "revoked") {
    if (!banner) fail("revoked (403) did not raise the global reconnect banner");
    if (!/Reconnect/.test(banner.textContent)) fail("reconnect banner is missing its Reconnect action");
  } else if (banner) {
    fail(MODE + " (not a 403) wrongly raised the reconnect banner — a reload can't fix a down/hung server");
  }
  console.log("PASS: [" + MODE + "] failing bridge reads DEGRADED (“" + connText() + "”) — no calm-live, no silent green, no stale sample" + (MODE === "revoked" ? "; 403 raised the reconnect banner" : ""));
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
