// Real DOM test for the decision workbench (Pending section). Drives the real
// ops end-to-end: builds a two-option surface via decision_build, queues it,
// and asserts — options render in server order with band words and nothing
// pre-selected; empty rationale and missing choice are refused; considered is
// earned only from opened grounds sections; the chat exchange joins the
// review trail; the recorded write returns a receipt with the trail counts;
// a single-option surface shows its warning; empty queue shows the empty
// state. Usage: node decision_render.mjs <PORT> <FOLDER>
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
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get(){ return 900; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get(){ return 600; } });
  },
});
const { window } = dom;
const D = window.document;
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
const CANDS = [
  { id: "erase", label: "Erase everything now", conclusion: "erase (Art. 17(1)(a) GDPR)",
    supporting: [{ pinpoint: "GDPR Art. 17(1)(a)", text: "erased where no longer necessary" }],
    consequences: ["the accounting records go too"] },
  { id: "split", label: "Split the records", conclusion: "erase profile; retain invoices restricted",
    supporting: [{ pinpoint: "GDPR Art. 17(3)(b)", text: "retention required by law" },
                 { pinpoint: "§ 147(3) AO", text: "keep accounting records ten years" }],
    consequences: ["profile gone; invoices frozen"] },
];
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  // empty state first
  await window.openDecisionPanel();
  let out = "";
  for (let i = 0; i < 40; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (/no escalation waits|Waiting for a person/.test(out)) break; }
  if (!/no escalation waits/.test(out)) fail("empty queue shows no empty state — got: " + out.slice(0, 160));

  // build via the real op, queue, re-render
  const surface = await window.tool("workspace_dispatch", { op: "decision_build",
    params: { query: "Erase K.'s record while invoices sit in the retention window?",
              candidates: CANDS, esc_reason: "GDPR Art. 17(1) erase vs § 147(3) AO keep-ten-years" } });
  if (!surface.ok) fail("decision_build failed: " + surface.error);
  window.queueDecisionSurface(surface);
  await sleep(60);
  const panel = D.getElementById("decisionpanel");
  out = D.getElementById("decout").textContent;
  if (!/here because: GDPR Art\. 17\(1\)/.test(out)) fail("escalation reason line missing");
  const opts = [...panel.querySelectorAll(".decopt")];
  if (opts.map(o => o.dataset.opt).join(",") !== "erase,split") fail("options not in server order");
  if (!/grounds (thin|moderate|firm)/.test(out)) fail("grounding band words missing");
  if (opts.some(o => /var\(--human\)/.test(o.style.borderColor))) fail("an option is pre-selected");

  // refusals: no choice, then no rationale
  click(panel.querySelector("#decrec")); await sleep(40);
  if (!/pick an option first/.test(panel.querySelector("#decmsg").textContent)) fail("missing-choice refusal absent");
  click(opts[1]); await sleep(20);
  click(panel.querySelector("#decrec")); await sleep(40);
  if (!/rationale/.test(panel.querySelector("#decmsg").textContent)) fail("empty-rationale refusal absent");

  // considered is earned: open ONE grounds section only
  click(panel.querySelector(".decgro")); await sleep(30);
  const sec = panel.querySelector('.decgsec[data-gid="split"] summary');
  click(sec); await sleep(30);
  if (!/§ 147\(3\) AO/.test(panel.querySelector("#decgrounds").textContent)) fail("grounds fold shows no cited law");

  // one chat exchange joins the trail (any answer — degraded is honest)
  panel.querySelector("#decq").value = "are the invoices actually open?";
  click(panel.querySelector("#decask"));
  for (let i = 0; i < 60; i++) { await sleep(120); if ((window._decAsked || []).length) break; }
  if (!(window._decAsked || []).length) fail("the asked exchange did not join the review trail");
  if (!/this exchange is on the record/.test(panel.querySelector("#declog").textContent)) fail("chat honesty line missing");

  // record
  panel.querySelector("#decrat").value = "Art. 17(3)(b) carves out what § 147(3) AO demands; K. confirmed by phone.";
  click(panel.querySelector("#decrec"));
  for (let i = 0; i < 50; i++) { await sleep(80); out = D.getElementById("decout").textContent; if (/Recorded, signed/.test(out)) break; }
  if (!/Recorded, signed/.test(out)) fail("no receipt — got: " + out.slice(0, 200));
  if (!/considered 1/.test(out)) fail("considered must count only the opened section — got: " + out.slice(0, 200));
  if (!/asked 1/.test(out)) fail("asked count missing from the receipt");

  // single-option surface warns
  const one = await window.tool("workspace_dispatch", { op: "decision_build",
    params: { query: "only one reading", candidates: CANDS.slice(0, 1) } });
  window.queueDecisionSurface(one);   // queueing re-renders the panel to the new surface
  await sleep(60);
  out = D.getElementById("decout").textContent;
  if (!/Only one defensible reading/.test(out)) fail("single-reading warning missing");

  console.log("PASS: decision workbench — server order, no pre-selection, band words; refusals for missing choice/rationale; considered earned; chat on the record; signed receipt with trail counts; single-reading warning; empty state");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
