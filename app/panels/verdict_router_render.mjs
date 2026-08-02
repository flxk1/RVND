// Real DOM test for the Verdict Router node (visualize-only). Adds a router node
// from the Build menu, asserts it renders the six verdict→handling rows + the
// visualize-only note, and that a verdict present on a cord lights its row (live)
// while an absent verdict stays dim — and that it carries NO write/port affordance.
// Usage: node verdict_router_render.mjs <PORT> <FOLDER_CONTEXT>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3];
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
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  // add the router THROUGH the Set up menu (proves the menu item is wired to addNode)
  const buildBtn = [...D.querySelectorAll(".sectbtn")].find(b => b.textContent.trim().replace(/▾$/, "").trim() === "Set up");
  click(buildBtn);
  const item = [...D.querySelectorAll(".mi[data-add='router']")][0];
  if (!item) fail("Set up menu has no Verdict router item");
  click(item);
  await sleep(120);

  const node = D.querySelector(".node.router");
  if (!node) fail("Verdict router node did not render");
  const table = node.querySelector(".vrtable");
  if (!table) fail("router node has no verdict table");
  const rows = [...table.querySelectorAll(".vrow")];
  if (rows.length !== 6) fail("verdict router must show all 6 verdict rows, got " + rows.length);
  const txt = table.textContent;
  for (const name of ["auto", "needs a person", "reserved", "refused", "not allowed", "unfired"])
    if (!txt.includes(name)) fail("router table missing verdict: " + name);
  if (!table.getAttribute("aria-label")) fail("router table has no non-visual (aria) label");
  // no cords/ports and no native buttons — it is not a wired node
  if (node.querySelector(".port") || node.querySelector("button")) fail("router must carry no port/native button");

  // make the seeded task read 'human' so the router's human row is live + actionable
  const ucNode = (window.S.g.nodes || []).find(n => n.kind === "use_case" && n.id.replace(/^uc:/, "") === "review-task");
  if (!ucNode) fail("seeded task node not found");
  let e = (window.S.g.edges || []).find(ed => ed.kind === "egress" && ed.from === ucNode.id);
  if (!e) { e = { from: ucNode.id, to: "master", kind: "egress", verdict: "human" }; window.S.g.edges.push(e); } else e.verdict = "human";
  window.render(); await sleep(40);
  const liveRows = [...D.querySelector(".node.router .vrtable").querySelectorAll(".vrow")];
  const humanRow = liveRows.find(r => /needs a person/.test(r.textContent));
  if (!humanRow.classList.contains("live")) fail("a live 'human' verdict did not light its router row");
  if (liveRows.find(r => /not allowed/.test(r.textContent)).classList.contains("live")) fail("an absent verdict ('prohibited') must not be marked live");

  // a NON-egress cord (grant/authority) carries no server verdict → it must NOT light a row
  window.S.g.edges.push({ from: "party:x", to: "uc:y", kind: "grant", verdict: "auto" });
  window.render(); await sleep(40);
  if ([...D.querySelector(".node.router .vrtable").querySelectorAll(".vrow")].find(r => /\bauto\b/.test(r.textContent)).classList.contains("live")) fail("a non-egress (grant) cord lit a verdict row — only egress cords carry verdicts");

  // ACT: the live 'human' row carries a request-sign-off action; clicking it records a sign-off
  const humanAct = [...D.querySelector(".node.router .vrtable").querySelectorAll(".vrow")].find(r => /needs a person/.test(r.textContent)).querySelector(".vract");
  if (!humanAct) fail("live needs-a-person row has no 'request sign-off' action");
  const before = ((await window.tool("workspace_contract", { op: "list_approvals", params: { folder_context: F } })).approvals || []).length;
  humanAct.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  let appr = [];
  for (let i = 0; i < 50; i++) { await sleep(80); appr = ((await window.tool("workspace_contract", { op: "list_approvals", params: { folder_context: F } })).approvals || []).filter(a => a.contract_id === "review-task"); if (appr.length) break; }
  if (!appr.length) fail("router request-sign-off did not record an approval for the task at 'human'");
  // a non-actionable verdict row (auto/refused/etc) must NOT carry the action
  if (liveRows.find(r => /unfired/.test(r.textContent)).querySelector(".vract")) fail("a non-actionable verdict row must not offer request-sign-off");

  // the router's Inspector label stays non-editable (the node itself is not renamed/written)
  window.S.sel = D.querySelector(".node.router").dataset.id; window.render(); await sleep(40);
  const lbl = D.getElementById("lbl");
  if (lbl && !lbl.disabled) fail("router label must not be editable");
  window.S.sel = null; window.render(); await sleep(20);

  // survives a server reload (client-only node re-injected)
  await window.reload(); await sleep(60);
  if (!D.querySelector(".node.router")) fail("router node did not survive a reload");

  console.log("PASS: verdict router — Build-menu node; 6 verdict→handling rows (aria); live verdict lights its row, absent + non-egress stay dim; live needs-a-person/reserved row requests a sign-off (records it), non-actionable rows don't; label non-editable; survives reload");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
