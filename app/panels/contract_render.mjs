// Real DOM test for the Contract execution drawer (workspace_contract), both
// in-panel tabs. Terms & obligations (the default tab): reviews + contracts +
// obligations render, zero write-action controls. Decision queue tab: queue +
// workbench render, a link to Approvals, and an ingest round-trip that grows
// the contract stack. The drawer ships as its own pack bundle
// (app/src/panels/contract.js), registered through the panel-mount contract,
// so this gate loads the composed page (GET /classic) rather than a bare
// readFileSync of the shell source — a raw index.html would open a frame with
// nothing inside it.
// Usage: node contract_render.mjs <PORT> <FOLDER_CONTEXT>
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
    window.fetch = (u, o) => fetch(u, o); window.confirm = () => true; window.alert = () => {};
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const txt = () => window.document.getElementById("ctout").textContent;
async function waitFor(re, n = 40) { let t = ""; for (let i = 0; i < n; i++) { await sleep(60); t = txt(); if (re.test(t)) return t; } return t; }
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  // default tab: Terms & obligations (Rules -> Contracts opens here)
  await window.openContractPanel("read"); await sleep(180);
  let cp = window.document.getElementById("contractpanel");
  if (!cp) fail("contract panel did not open");
  if (cp.getAttribute("aria-modal") !== "true") fail("not a modal dialog");
  if (!/Contract execution/i.test(cp.querySelector("b").textContent)) fail("header wrong: " + cp.querySelector("b").textContent);
  const rb = cp.querySelector(".robadge");
  if (!rb || !/reads/i.test(rb.textContent) || !/resolves/i.test(rb.textContent)) fail("missing the reads/resolves badge: " + (rb && rb.textContent));
  let t = await waitFor(/Reviews/);
  if (!/Reviews/.test(t) || !/Contracts/.test(t) || !/Obligations/.test(t)) fail("reviews/contracts/obligations sections missing: " + t.slice(0, 160));
  if (cp.querySelectorAll("button").length) fail("Terms & obligations tab renders act controls — it must render none");

  // Decision queue tab (Pending -> Decision queue jumps straight here, even
  // on a fresh open, via the back-compat wrapper's post-mount tab switch)
  await window.openContractPanel("act"); await sleep(180);
  cp = window.document.getElementById("contractpanel");
  if (!cp) fail("contract panel is not open after switching tabs");
  const tab = cp.querySelector('[data-cttab="act"]');
  if (!tab || tab.getAttribute("aria-selected") !== "true") fail("Decision queue tab did not become selected");
  t = await waitFor(/Decision queue/);
  if (!/Decision queue/.test(t)) fail("decision queue section missing: " + t.slice(0, 160));
  if (/Reviews/.test(t)) fail("Decision queue tab renders the read sections — it must render the queue only");
  if (![...cp.querySelectorAll("button")].some((b) => /approvals/i.test(b.textContent))) fail("no link to Approvals");
  const before = await window.tool("workspace_contract", { op: "state", params: { folder_context: F } });
  const n0 = (before.contracts || []).length;
  cp.querySelector("#cttext").value = "The Publisher shall account to the Writer within 60 days of each half-year.";
  cp.querySelector("#ctingbtn").click(); await sleep(260);
  const after = await window.tool("workspace_contract", { op: "state", params: { folder_context: F } });
  if (!((after.contracts || []).length > n0)) fail("ingest did not grow the contract stack (" + n0 + " -> " + (after.contracts || []).length + ")");

  // clicking the Terms & obligations tab again switches back without closing
  cp.querySelector('[data-cttab="read"]').click(); await sleep(180);
  t = await waitFor(/Reviews/);
  if (!/Reviews/.test(t)) fail("switching back to Terms & obligations did not render its sections: " + t.slice(0, 160));

  console.log("PASS: contract execution drawer — Terms & obligations tab renders reviews/contracts/obligations with zero act controls; Decision queue tab renders the queue + workbench with the reads/resolves badge; links to Approvals; ingest round-trip grows the stack; modal");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
