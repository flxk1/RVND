// Real DOM test for trusted-front identity in the console. serve.py runs with
// a declared principal header; every fetch carries it (as the fronting proxy
// would). Asserts: the topbar shows the signed-in chip with the principal;
// claiming from the pending list acts as the verified party, overriding the
// client-side actor — the workbench claim line names the principal.
// Usage: node proxy_identity_render.mjs <PORT> <FOLDER> <PRINCIPAL>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3], WHO = process.argv[4];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    bridgeGlobals(window, PORT);
    window.fetch = (u, o) => fetch(u, { ...(o || {}),
      headers: { ...((o || {}).headers || {}), "X-Auth-Request-Email": WHO } });
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const D = window.document;
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(60);

  const chip = D.getElementById("principal");
  if (!chip) fail("signed-in chip missing from the topbar");
  if (!new RegExp("signed in as " + WHO).test(chip.textContent)) fail("chip does not name the principal — got: " + chip.textContent);

  await window.openDecisionPanel();
  let out = "";
  for (let i = 0; i < 60; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (/Claim & review/.test(out)) break; }
  click([...D.querySelectorAll("[data-decclaim]")][0]);
  for (let i = 0; i < 40; i++) { await sleep(100); if (window._decClaim) break; }
  if (!window._decClaim) fail("claim did not complete");
  if (window._decClaim.claimed_by !== WHO) fail("claim acted as " + window._decClaim.claimed_by + " — the verified principal must override the client actor");
  out = D.getElementById("decout").textContent;
  if (!/claimed by you/.test(out)) fail("workbench claim line missing");

  console.log("PASS: proxy identity — signed-in chip names the principal; the claim acts as the verified party, not the client's claim");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
