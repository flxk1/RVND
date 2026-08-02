// Real DOM test for the Inspector sign-off CTA (verdict → action). A task that
// needs a person shows a "Human oversight" traffic light (grey = not requested)
// and a "Request sign-off" button; clicking it routes to an active person and
// creates an approval (server-side, in the sign-off inbox), and the inspector's
// traffic light then turns amber (awaiting). Honest: no fake grant, server decides.
// Usage: node signoff_cta_render.mjs <PORT> <FOLDER_CONTEXT> <UC_ID>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3], UCID = process.argv[4];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const dom = new JSDOM(html, { runScripts: "dangerously", beforeParse(window) {
  bridgeGlobals(window, PORT);
  window.fetch = (u, o) => fetch(u, o); window.confirm = () => true;
  Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
  Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
} });
const { window } = dom; const D = window.document;
const ucops = () => D.getElementById("ucops");
const approvalsFor = async () => { const r = await window.tool("workspace_contract", { op: "list_approvals", params: { folder_context: F } }); return ((r && r.approvals) || []).filter((a) => a.contract_id === UCID); };
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(60);

  // select the task node → its inspector loads the oversight section
  const node = (window.S.g.nodes || []).find((n) => n.kind === "use_case" && n.id.replace(/^uc:/, "") === UCID);
  if (!node) fail("seeded task node not found in the graph");
  window.S.sel = node.id; window.render();
  for (let i = 0; i < 50 && !(ucops() && /Human oversight/.test(ucops().textContent)); i++) await sleep(60);
  if (!ucops() || !/Human oversight/.test(ucops().textContent)) fail("no 'Human oversight' section for a task that needs a person");

  // traffic light present (non-visual label) + grey/'not requested' + a Request sign-off CTA
  const tl = ucops().querySelector('[aria-label^="oversight traffic light"]');
  if (!tl) fail("no oversight traffic-light indicator");
  if (!/not requested/i.test(tl.getAttribute("aria-label") || "")) fail("traffic light should read 'not requested' before a request");
  const btn = D.getElementById("reqsign");
  if (!btn) fail("no 'Request sign-off' CTA");
  if ((await approvalsFor()).length) fail("an approval existed before the CTA was clicked");

  // click → an approval is created server-side, routed to a real person
  btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  let appr = [];
  for (let i = 0; i < 50; i++) { await sleep(80); appr = await approvalsFor(); if (appr.length) break; }
  if (!appr.length) fail("Request sign-off did not create an approval in the record");
  if (!((appr[0].signers || []).length)) fail("the created sign-off has no signer — routing found nobody");

  // the inspector now reads amber (awaiting), and the one-click request is gone (it's in the inbox)
  window.S.sel = node.id; window.render();
  for (let i = 0; i < 50 && !(ucops() && /awaiting sign-off/i.test(ucops().textContent)); i++) await sleep(60);
  if (!/awaiting sign-off/i.test(ucops().textContent)) fail("traffic light did not turn amber after the request");
  if (D.getElementById("reqsign")) fail("Request sign-off button should be gone once a request is pending");

  console.log("PASS: inspector sign-off CTA — 'needs a person' shows an oversight traffic light (grey→amber) + Request sign-off; clicking routes to a real person and records the request; no fake grant");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
