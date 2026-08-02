// Real DOM test for the Approvals inbox (workspace_contract list_approvals +
// record_approval). Opens the drawer and exercises the WRITE round-trip: a
// 2-signer pending request shows approve/reject controls for each signer;
// approving one signer flips that row but leaves the request pending
// (approval needs every signer); approving the second flips the whole
// request to approved. The drawer ships as its own pack bundle
// (app/src/panels/approvals.js), registered through the panel-mount
// contract, so this gate loads the composed page (GET /classic) rather than
// a bare readFileSync of the shell source — a raw index.html would open a
// frame with nothing inside it.
// Usage: node approvals_render.mjs <PORT> <FOLDER_CONTEXT>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
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
const txt = () => (D.getElementById("apout") || {}).textContent || "";
async function waitFor(re, n=40) { let t=""; for (let i=0;i<n;i++){ await sleep(60); t=txt(); if (re.test(t)) return t; } return t; }
// locates a contract's finding block by its data-contract-id (not DOM/array
// index — the server's list order is not guaranteed stable across seeds)
const blockFor = (cid) => [...D.querySelectorAll("[data-contract-id]")].find((el) => el.dataset.contractId === cid);
const clickDecision = (root, signer, decision) => {
  const btn = [...root.querySelectorAll("[data-signer][data-decision]")].find((b) => b.dataset.signer === signer && b.dataset.decision === decision);
  if (!btn) fail("no " + decision + " control for signer " + signer);
  btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
};
const reSafe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  await window.openApprovalsPanel();
  await sleep(160);
  const pp = D.getElementById("approvalspanel");
  if (!pp) fail("approvals panel did not open");
  if (pp.getAttribute("aria-modal") !== "true") fail("approvals panel is not a modal dialog");
  if (!/Any one rejection blocks/i.test(pp.textContent)) fail("missing the fail-safe sign-off copy");
  // backdrop scrim must cover the canvas while the dialog is open
  if (!D.querySelector(".modal-scrim")) fail("no backdrop scrim while modal open");

  let t = await waitFor(/deploy model X/);
  if (!/deploy model X/.test(t)) fail("pending request not rendered: " + t.slice(0, 160));
  if (!/dpo/.test(t) || !/ciso/.test(t)) fail("both signers should be listed: " + t.slice(0, 160));
  // controls present for an undecided request
  if (![...pp.querySelectorAll("button")].some((b) => /approve/i.test(b.textContent))) fail("no approve control for pending signer");

  let b1 = blockFor("c-1");
  if (!b1) fail("two-signer request c-1 not in the list");

  // approve dpo → dpo's row must show the ACTUAL decision (regression guard for
  // the signer_decisions dict being rendered as "[object Object]")
  clickDecision(b1, "dpo", "approved");
  t = await waitFor(/dpo[\s\S]*approved/i);
  if (/\[object Object\]/.test(t)) fail("signer decision rendered as [object Object] — dict not unwrapped to .decision");
  if (!/dpo[\s\S]*approved/i.test(t)) fail("dpo's 'approved' badge not rendered: " + t.slice(0, 260));
  // overall STILL pending — approval needs every signer (deliberate-grant doctrine)
  b1 = blockFor("c-1");
  if (!b1) fail("c-1 disappeared after only one signer decided");
  const ttl1 = (b1.querySelector(".ttl") || {}).textContent || "";
  if (!/pending/i.test(ttl1)) fail("request flipped before all signers approved — ttl: " + ttl1);

  // approve ciso → whole request approved, drops out of the pending inbox
  // (poll for absence like every other transition — a fixed sleep flakes
  // under parallel gate load when the write round-trip exceeds it)
  clickDecision(b1, "ciso", "approved");
  let gone = false;
  for (let i = 0; i < 40 && !gone; i++) { await sleep(60); gone = !/deploy model X/.test(txt()); }
  if (!gone) fail("request stayed in the pending inbox after all signers approved");

  // single-quote signer name must round-trip (regression guard for the old
  // encodeURIComponent-in-onclick escaping; the bundle now binds writes
  // through data attributes and addEventListener instead)
  let t2 = await waitFor(/o'brien|tricky/i);
  if (!/o'brien|tricky/i.test(t2)) fail("quote-named request not shown: " + t2.slice(0, 200));
  const b2 = blockFor("c-2");
  if (!b2) fail("could not find the quote-named approval (c-2)");
  const qbtn = b2.querySelector('[data-signer][data-decision="approved"]');
  if (!qbtn) fail("no approve control for the quote-named signer");
  const qsigner = qbtn.dataset.signer;
  qbtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(180);
  // switch to "all" so a since-approved (and therefore filtered-out) request
  // still proves the decision recorded server-side
  const filterSel = D.getElementById("apfilter");
  filterSel.value = "";
  filterSel.dispatchEvent(new window.Event("change", { bubbles: true }));
  const recordedRe = new RegExp(reSafe(qsigner) + "[\\s\\S]*approved", "i");
  const t3 = await waitFor(recordedRe);
  if (!recordedRe.test(t3)) fail("decision on a single-quote signer did not record — got: " + t3.slice(0, 200));

  console.log("PASS: approvals inbox — 2-signer round-trip (decided rows show real decision, no [object Object]); unanimity to grant; scrim; quote-safe signer; modal");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
