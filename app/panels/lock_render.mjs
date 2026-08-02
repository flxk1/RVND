// Real DOM test for the Privacy Lock drawer (workspace_lock, read + WRITE). Asserts
// the read cards render, the floor is not a 0-1 dial/%, RAISING the floor is
// direct while LOWERING is confirm()+reason (both change server state),
// reclassify is confirm-gated and sweeps every stored pair, and on a folder
// sealed at rest: a wrong passphrase fails closed (no key cached), the real
// passphrase unseals for the session, and seal drops the cached key.
// The drawer ships as its own pack bundle (app/src/panels/lock.js),
// registered through the panel-mount contract, so this gate loads the
// composed page (GET /classic) rather than a bare readFileSync of the shell
// source — a raw index.html would open a frame with nothing inside it. The
// panel's write handlers are closure-local (no window export, per contract
// §3.5), so every write below is driven through a real button click rather
// than a direct function call.
// Usage: node lock_render.mjs <PORT> <FOLDER_CONTEXT> <SEALED_FOLDER> <PASSPHRASE>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3], F2 = process.argv[4], PASS = process.argv[5];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
let confirmCalls = [];
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    bridgeGlobals(window, PORT);
    window.fetch = (u, o) => fetch(u, o);
    window.confirm = (m) => { confirmCalls.push(String(m || "")); return true; };
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const doc = () => window.document;
const call = (op, params) => window.tool("workspace_lock", { op, params: Object.assign({ folder_context: F }, params || {}) });
const clickLk = (id) => { const el = doc().getElementById(id); if (!el) fail("missing control #" + id); el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); };
async function waitText(id, re, n = 40) { let t = ""; for (let i = 0; i < n; i++) { await sleep(60); t = (doc().getElementById(id) || {}).textContent || ""; if (re.test(t)) break; } return t; }
const srlive = () => (doc().getElementById("srlive") || {}).textContent || "";
async function waitAnnounce(re, label) { let t = ""; for (let i = 0; i < 60; i++) { await sleep(80); t = srlive(); if (re.test(t)) return t; } fail(label + " — announce: " + t.slice(0, 200)); }
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openLockPanel();
  await sleep(160);
  const lp = doc().getElementById("lockpanel");
  if (!lp) fail("lock panel did not open");
  if (lp.getAttribute("aria-modal") !== "true") fail("lock panel is not a modal dialog");
  let txt = await waitText("lkout", /Redaction floor/);
  if (!/Redaction floor/.test(txt) || !/Lock decisions/.test(txt)) fail("read cards missing: " + txt.slice(0, 160));
  if (/%/.test(txt)) fail("drawer renders a percentage — doctrine forbids a 0-1 dial/score");

  for (const id of ["lkfloorbtn", "lksealbtn", "lkunsealbtn", "lkreclassbtn"])
    if (!doc().getElementById(id)) fail("write control missing: #" + id);

  // raise (tighten) = direct — the button's click handler captures the
  // drawer's current floor (0, from the initial read) as "prev" via closure,
  // same as every other write control here (audit/lens precedent)
  let before = confirmCalls.length;
  doc().getElementById("lkfloor").value = "0.75";
  clickLk("lkfloorbtn");
  await sleep(180);
  if (confirmCalls.length !== before) fail("raising the floor must be direct — it asked to confirm");
  let g = await call("threshold_get");
  if (Number(g.threshold) !== 0.75) fail("server floor did not change on raise — " + JSON.stringify(g));

  // lower (loosen) = confirm + reason
  before = confirmCalls.length;
  doc().getElementById("lkfloor").value = "0.25";
  doc().getElementById("lkfloorreason").value = "audit: widen visibility for review";
  clickLk("lkfloorbtn");
  await sleep(200);
  if (confirmCalls.length <= before) fail("lowering the floor must be gated by confirm() — none fired");
  if (!/lower/i.test(confirmCalls[confirmCalls.length - 1])) fail("confirm() must name the consequence (lower)");
  g = await call("threshold_get");
  if (Number(g.threshold) !== 0.25) fail("server floor did not change on lower — " + JSON.stringify(g));

  // lower with no reason → refused (no write)
  doc().getElementById("lkfloor").value = "0.1";
  doc().getElementById("lkfloorreason").value = "";
  let c3 = confirmCalls.length;
  clickLk("lkfloorbtn");
  await sleep(120);
  g = await call("threshold_get");
  if (Number(g.threshold) !== 0.25) fail("lowering without a reason must be refused — became " + g.threshold);
  if (confirmCalls.length !== c3) fail("lowering without a reason must not reach confirm()");

  // reclassify: confirm-gated; sweeps every stored pair and reports the counts
  before = confirmCalls.length;
  clickLk("lkreclassbtn");
  await waitAnnounce(/Reclassified — /, "reclassify did not complete");
  if (confirmCalls.length <= before) fail("reclassify must be confirm-gated — none fired");
  if (!/reclassify/i.test(confirmCalls[confirmCalls.length - 1])) fail("confirm() must name the reclassify");
  if (!/of 3\./.test(srlive())) fail("reclassify did not sweep the 3 stored pairs: " + srlive());

  // switch to the at-rest-sealed folder and drive seal/unseal
  await window.openLockPanel();                       // toggle the drawer closed
  window.S.path = F2;
  try { await window.reload(); } catch (_) { /* sealed reads may refuse — the drawer must still work */ }
  await sleep(80);
  await window.openLockPanel(); await sleep(200);
  if (!doc().getElementById("lkunsealbtn")) fail("lock drawer did not render write controls on the sealed folder");

  // wrong passphrase → fail closed (confirm names the loosening; no key cached)
  doc().getElementById("lkpass").value = "not-the-passphrase";
  before = confirmCalls.length;
  clickLk("lkunsealbtn");
  await waitAnnounce(/Could not unseal/, "a wrong passphrase must be refused");
  if (confirmCalls.length <= before) fail("unseal must be confirm-gated — none fired");
  if (!/unseal/i.test(confirmCalls[confirmCalls.length - 1])) fail("confirm() must name the unseal");

  // seal is direct (no confirm); after the refused unseal there is no cached key
  before = confirmCalls.length;
  clickLk("lksealbtn");
  await waitAnnounce(/already absent/, "seal after a refused unseal must find no cached key");
  if (confirmCalls.length !== before) fail("seal must be direct — it asked to confirm");

  // the real passphrase unseals for this session (files served read-through)
  doc().getElementById("lkpass").value = PASS;
  clickLk("lkunsealbtn");
  await waitAnnounce(/Unsealed — \d+ file/, "the real passphrase did not unseal");

  // seal again drops the cached session key — the unseal really cached it
  clickLk("lksealbtn");
  await waitAnnounce(/key was dropped/, "seal did not drop the cached session key");

  console.log("PASS: privacy lock — read cards + write controls; raise=direct, lower=confirm+reason; reclassify confirm-gated sweeps all pairs; wrong passphrase fails closed; unseal+seal round-trip caches then drops the key; no % dial");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
