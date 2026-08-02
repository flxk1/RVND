// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for unit chrome gating. The server's /whoami answer names the
// units the resolved party's role warrants; the page draws only that chrome.
// Presentation over a server answer — read scoping is asserted server-side
// (server/tests/test_proxy_identity.py), never here.
// Usage: node unit_gating_render.mjs <PORT> <FOLDER> <MODE> [<PRINCIPAL>]
//   MODE=principal — every fetch carries the identity header, as the fronting
//                    proxy would; the party's role decides the chrome
//   MODE=local     — no identity header (single-operator): all chrome renders
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
const MODE = process.argv[4], WHO = process.argv[5] || "";
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    bridgeGlobals(window, PORT);
    if (MODE === "principal") window.fetch = (u, o) => fetch(u, { ...(o || {}),
      headers: { ...((o || {}).headers || {}), "X-Auth-Request-Email": WHO } });
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const D = window.document;
const sect = (label) => {
  for (const m of D.querySelectorAll(".sections .sectmenu"))
    if (m.getAttribute("aria-label") === label) return m.closest(".sect");
  return null;
};
const hiddenSect = (label) => { const s = sect(label); return s && s.style.display === "none"; };
const viewBtn = (v) => D.querySelector('.viewtog button[data-view="' + v + '"]');
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("page did not boot");
  for (const label of ["Set up", "Rules", "Pending", "Record"])
    if (!sect(label)) fail("section menu missing from the markup: " + label);

  if (MODE === "principal") {
    // approver role warrants only the sign-off widget: no build/govern chrome
    if (!hiddenSect("Set up")) fail("approver still sees the Set up menu");
    if (!hiddenSect("Rules")) fail("approver still sees the Rules menu");
    if (!hiddenSect("Pending")) fail("approver still sees the Pending menu");
    if (!hiddenSect("Record")) fail("approver still sees the Record menu");
    for (const v of ["patch", "arrange", "desk", "matrix"])
      if (viewBtn(v).style.display !== "none")
        fail("approver still sees the " + v.toUpperCase() + " view button");
    const chip = D.getElementById("principal");
    if (!chip || !chip.textContent.includes(WHO))
      fail("signed-in chip missing or does not name the principal");
    console.log("PASS: unit gating (principal, approver) — no Set up/Rules/"
      + "Pending/Record menus, no view-toggle chrome; the chip names the principal");
  } else {
    // local single-operator mode: everything renders, byte-for-byte as before
    for (const label of ["Set up", "Rules", "Pending", "Record"])
      if (hiddenSect(label)) fail("local mode hid the " + label + " menu");
    for (const v of ["patch", "arrange", "desk", "matrix"])
      if (viewBtn(v).style.display === "none")
        fail("local mode hid the " + v.toUpperCase() + " view button");
    console.log("PASS: unit gating (local) — every menu and view button renders");
  }
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
