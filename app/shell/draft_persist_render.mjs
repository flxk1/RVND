// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Real DOM test for draft persistence on the authoring panels — persist-on-edit
// (debounced draft_save), silent prefill on open from the boot-tail rehydrate,
// the quiet per-panel chip incl. the amber older-than-the-patch state, the chat
// restore divider, the always-visible discard control, and the pagehide flush.
// The workspace arrives pre-seeded: a map draft saved BEFORE the last applied
// change (stale) and policy/cards/chat drafts saved after it (fresh).
// The map surface ships as its own pack bundle (app/src/panels/map.js),
// registered through the panel-mount contract, so this gate loads the composed
// page (GET /classic) rather than a bare readFileSync of the shell source — a
// raw index.html would open a map panel with no bundle registered against it.
// Usage: node draft_persist_render.mjs <PORT> <FOLDER>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], F = process.argv[3], F2 = process.argv[4];
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

const loadDraftIn = async (folder, surface) =>
  window.tool("workspace_session", { op: "draft_load",
    params: { folder_context: folder, surface } });
const loadDraft = (surface) => loadDraftIn(F, surface);
const until = async (fn, ms = 3000) => {
  for (let t = 0; t < ms; t += 50) { if (await fn()) return true; await sleep(50); }
  return false;
};

async function main() {
  for (let i = 0; i < 120 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  if (window.S.path !== F) fail("workspace not focused: " + window.S.path);

  // boot-tail rehydrate populated the draft state (reload survives)
  const loaded = window._drafts.loaded || {};
  for (const s of ["policy_paste", "map", "cards", "chat"])
    if (!loaded[s]) fail("rehydrate missed surface " + s + ": " + JSON.stringify(Object.keys(loaded)));
  if (window._chatPolicy !== "no pii egress") fail("chat policy accumulation not rehydrated: " + window._chatPolicy);

  // --- policy_paste: silent prefill + fresh chip + discard control ---
  await window.openPolicyPanel();
  const ta = D.getElementById("pptext");
  if (!ta) fail("policy panel did not open");
  if (ta.value !== "Automated decisions must be reviewed.") fail("policy paste not prefilled: " + JSON.stringify(ta.value));
  const pchip = D.getElementById("draftchip-policy_paste");
  if (!pchip || pchip.hidden) fail("policy draft chip missing or hidden");
  if (!/^draft · saved \d+s ago$/.test(pchip.textContent)) fail("policy chip text: " + pchip.textContent);
  if (pchip.classList.contains("stale")) fail("fresh policy draft must not read stale");
  if (!D.getElementById("ppdiscard")) fail("policy discard control missing beside the commit action");

  // persist-on-edit: an input schedules a debounced save; flush lands it
  ta.value = "No PII leaves the boundary.";
  ta.dispatchEvent(new window.Event("input", { bubbles: true }));
  if (!window._drafts.pending.policy_paste) fail("input did not queue a debounced draft save");
  await window.draftFlush("policy_paste");
  let r = await loadDraft("policy_paste");
  if (!r.ok || r.payload.text !== "No PII leaves the boundary.") fail("draft_save did not persist the edit: " + JSON.stringify(r));
  if (!/^draft · saved \d+s ago$/.test(pchip.textContent)) fail("chip did not refresh after save: " + pchip.textContent);

  // pending edits flush on panel close
  ta.value = "closed with pending edits";
  ta.dispatchEvent(new window.Event("input", { bubbles: true }));
  await window.openPolicyPanel();   // toggles closed
  if (D.getElementById("policypanel")) fail("policy panel did not toggle closed");
  if (!await until(async () => (await loadDraft("policy_paste")).payload.text === "closed with pending edits"))
    fail("close did not flush the pending draft save");

  // --- a debounced edit spanning a workspace switch lands in the workspace it
  // --- was typed in — and never in the switched-to one (the misfile guard)
  await window.openPolicyPanel();
  const ta2 = D.getElementById("pptext");
  ta2.value = "typed in the first workspace";
  ta2.dispatchEvent(new window.Event("input", { bubbles: true }));
  window.S.path = F2;                     // the rail switch: path first, then boot's draft tail
  await window.rehydrateDrafts();
  if (!await until(async () => ((await loadDraft("policy_paste")).payload || {}).text === "typed in the first workspace"))
    fail("switch dropped the pending edit for its origin workspace");
  const leak = await loadDraftIn(F2, "policy_paste");
  if (leak.ok && leak.payload && leak.payload.text) fail("draft misfiled into the switched-to workspace: " + JSON.stringify(leak.payload));
  if (window._drafts.savedAt.policy_paste) fail("switched-to workspace inherited the origin's savedAt chip state");
  window.S.path = F; await window.rehydrateDrafts();   // switch back for the rest
  await window.openPolicyPanel();        // toggle the stale panel closed

  // --- map: prefill of view state + the amber older-than-the-patch chip ---
  await window.openMapPanel();
  const mta = D.getElementById("mptext"), mg = D.getElementById("mpgroup");
  if (!mta || mta.value !== "Article 9 risk management") fail("map text not prefilled: " + (mta && mta.value));
  if (mg.value !== "role") fail("map group_by not prefilled: " + mg.value);
  const mchip = D.getElementById("draftchip-map");
  if (!mchip || mchip.hidden) fail("map draft chip missing or hidden");
  if (mchip.textContent !== "draft · older than the patch") fail("stale map draft chip: " + mchip.textContent);
  if (!mchip.classList.contains("stale")) fail("stale map chip missing its amber class");
  if (!D.getElementById("mpdiscard")) fail("map discard control missing beside the commit action");

  // pagehide flushes every pending debounced save
  mta.value = "flushed on pagehide";
  mta.dispatchEvent(new window.Event("input", { bubbles: true }));
  window.dispatchEvent(new window.Event("pagehide"));
  if (!await until(async () => (await loadDraft("map")).payload.text === "flushed on pagehide"))
    fail("pagehide did not flush the pending map save");
  await window.openMapPanel();   // toggle closed

  // --- chat: transcript restored under the divider; discard clears it ---
  await window.openChatPanel();
  const clog = D.getElementById("chatlog");
  if (!clog) fail("chat panel did not open");
  if (!/— restored .+ —/.test(clog.textContent)) fail("chat restore divider missing: " + clog.textContent.slice(0, 120));
  if (!clog.textContent.includes("hello governance")) fail("restored chat entry missing");
  const cchip = D.getElementById("draftchip-chat");
  if (!cchip || cchip.hidden) fail("chat draft chip missing or hidden");
  const cdisc = D.getElementById("chatdiscard");
  if (!cdisc) fail("chat discard control missing beside Send");
  cdisc.click();
  if (!await until(async () => { const d = await loadDraft("chat"); return d.ok && Object.keys(d.payload).length === 0; }))
    fail("chat discard did not delete the server draft");
  if (clog.textContent.trim() !== "") fail("chat discard left the transcript rendered");
  if (!await until(async () => cchip.hidden, 500)) fail("chat chip still shown after discard");
  await window.openChatPanel();   // toggle closed

  // --- cards: the in-progress intake card prefills the create panel ---
  window.addNode("use_case");   // resolves only on user action — do not await
  if (!await until(async () => D.getElementById("createpanel"), 2000)) fail("create panel did not open");
  const nm = D.getElementById("cpname"), rk = D.getElementById("cprisk");
  if (nm.value !== "Loan scoring") fail("intake card name not prefilled: " + JSON.stringify(nm.value));
  if (rk.value !== "high") fail("intake card risk not prefilled: " + rk.value);
  if (!D.getElementById("cpdiscard")) fail("cards discard control missing beside Create");
  const kchip = D.getElementById("draftchip-cards");
  if (!kchip || kchip.hidden) fail("cards draft chip missing or hidden");
  nm.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  if (D.getElementById("createpanel")) fail("create panel did not close on Escape");

  // --- applyTwin: the commit act discards the applied policy draft (as the
  // --- create act does for the intake card) instead of leaving it to go amber
  await window.openPolicyPanel();
  const ta3 = D.getElementById("pptext");
  ta3.value = "Automated decisions must be reviewed by a compliance officer.";
  ta3.dispatchEvent(new window.Event("input", { bubbles: true }));
  await window.draftFlush("policy_paste");
  const tw = await window.ingestPolicy(ta3.value);
  if (!tw || !tw.ok) fail("policy_ingest did not build a twin: " + JSON.stringify(tw).slice(0, 200));
  await window.applyTwin();
  if (D.getElementById("policypanel")) fail("apply did not close the policy panel");
  if (!await until(async () => { const d = await loadDraft("policy_paste"); return d.ok && !(d.payload && d.payload.text); }))
    fail("apply left the policy_paste draft behind");

  console.log("PASS: draft persistence — rehydrate at boot tail, silent prefill on open (policy/map/cards), debounced persist-on-edit incl. close + pagehide flush, quiet chip with amber older-than-the-patch state, chat restore divider, discard controls present and effective, switch-safe flush (origin-folder capture, no misfile, no inherited chip state), apply discards the committed policy draft");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
