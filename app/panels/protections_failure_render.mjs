// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// RV-05: failure-injection gate for the Protections drawer — the verdict
// surface a human reads. "Server decides, client renders" is fail-open at the
// operator unless the CLIENT degrades explicitly when the server does not
// answer. The paired _test.py boots the real serve.py and injects one failure
// mode per run into the workspace_policy seam; this scenario asserts the
// panel's visible state is an EXPLICIT degradation — never a calm green lamp,
// never a silently missing answer:
//
//   error    bridge dies mid-call (connection dropped / 500-shaped)
//            -> "Could not load policy" banner; zero dial rows; zero on-lamps
//   hang     bridge never answers inside the observation window
//            -> the explicit "loading…" status stays visible; zero on-lamps
//   revoked  the page's session token is no longer accepted (403)
//            -> same explicit failure banner; zero dial rows
//   stale    first load succeeds, every later call fails: a write attempt
//            must surface the failure banner ABOVE the now-stale rows
//            (stale content may remain, but never framed as current/calm)
//
// Usage: node protections_failure_render.mjs <PORT> <FOLDER> <MODE>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3], MODE = process.argv[4];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log(`FAIL[${MODE}]: ` + m); process.exit(1); };
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    bridgeGlobals(window, PORT);
    window.fetch = (u, o) => fetch(u, o);
    window.confirm = () => true;
    window.alert = () => {};
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const D = window.document;
const out = () => D.getElementById("psout");
const txt = () => (out() || {}).textContent || "";
const onLamps = () => (out() ? [...out().querySelectorAll(".pson")] : []);
const rows = () => (out() ? [...out().querySelectorAll(".psrow")] : []);
const banner = () => (out() ? out().querySelector(".finding.bad") : null);
// generous budget: this gate also runs inside the parallel UI walk, where a
// healthy round-trip can take multiples of its quiet-machine time
async function waitFor(pred, n = 100) { for (let i = 0; i < n; i++) { await sleep(60); if (pred()) return true; } return pred(); }

async function main() {
  for (let i = 0; i < 400 && !window._ready; i++) await sleep(25);   // 10s: CI runners under load blow a 2s boot budget
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  // Revocation is a mid-session event: the page booted with a valid token
  // (serve.py inlines it into the served header script, so a pre-parse
  // override is clobbered at parse time — asserted the hard way). Invalidate
  // it now; the page attaches window.__WORKSPACES_TOKEN__ per call, so every
  // request from here on hits the server's real hmac check and 403s.
  if (MODE === "revoked") window.__WORKSPACES_TOKEN__ = "revoked-after-issue";

  window.openPolicySettingsPanel();
  if (!(await waitFor(() => out()))) fail("protections drawer did not open (#psout missing)");

  if (MODE === "error" || MODE === "revoked") {
    if (!(await waitFor(() => banner()))) fail("no explicit failure banner after a dead bridge — the operator sees nothing; visible: \"" + txt().slice(0, 160) + "\"");
    if (!/could not load policy/i.test(banner().textContent)) fail("banner does not say what failed: " + banner().textContent.slice(0, 120));
    if (rows().length) fail("dial rows rendered from a failed load — fabricated state");
    if (onLamps().length) fail("an 'on' lamp is lit with no server verdict — silent green");
    if (MODE === "revoked" && txt().includes("revoked-after-issue")) fail("the revoked token itself leaked into the visible error");
  } else if (MODE === "hang") {
    await sleep(2000);   // the injected hang outlives the whole observation window
    if (banner() && rows().length === 0 && onLamps().length === 0) {
      // an explicit failure banner is an acceptable degradation for a hang too
    } else {
      if (!/loading…/.test(txt())) fail("no explicit in-flight status while the bridge hangs — reads as silently fine: " + txt().slice(0, 120));
      if (onLamps().length) fail("an 'on' lamp is lit while the bridge hangs — silent green");
      if (rows().length) fail("dial rows rendered while the bridge hangs — fabricated state");
    }
  } else if (MODE === "stale") {
    if (!(await waitFor(() => rows().length > 0))) fail("healthy first load did not render dial rows: " + txt().slice(0, 160));
    if (banner()) fail("failure banner shown on a healthy load");
    // pick any oversight level that is not the current one and click it — the
    // write and the reload behind it both fail from here on
    const lvlBtn = [...out().querySelectorAll("[data-ovl]")].find((b) => !b.classList.contains("on"));
    if (!lvlBtn) fail("no non-current oversight level button to drive the failing write");
    lvlBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    if (!(await waitFor(() => banner()))) fail("bridge died after a healthy load and the panel shows no failure state — stale content framed as current");
    const first = out().firstElementChild;
    if (!first || !first.classList.contains("bad")) fail("failure banner is not the first thing the operator sees above the stale rows");
  } else {
    fail("unknown mode: " + MODE);
  }

  console.log(`PASS[${MODE}]: protections drawer degrades explicitly (no silent green, no fabricated rows, failure named)`);
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
