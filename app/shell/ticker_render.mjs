// Real DOM test for the Live Audit Ticker (workspace_audit op=tail, read-only).
// Loads index.html in jsdom against serve.py, toggles the ticker, and asserts:
// the bottom strip appears with signed-event chips each carrying a verdict
// lamp; an event whose signature was stripped is flagged unsigned (never
// rendered like a signed chip); it is NON-modal (a live region you watch while
// working, not a focus trap) and READ-ONLY (no <button> writes); and toggling
// again removes it.
// Usage: node ticker_render.mjs <PORT> <FOLDER_CONTEXT>
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
let _step = "start";
const watchdog = setTimeout(() => fail("watchdog: hung at step '" + _step + "' (jsdom boot/fetch stall)"), 18000);
async function main() {
  _step = "boot";
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  _step = "reload";
  window.S.path = F; await window.reload(); await sleep(40);
  _step = "open-ticker";

  window.toggleTicker();
  await sleep(120);
  const strip = D.getElementById("tickerstrip");
  if (!strip) fail("ticker strip did not open");
  // non-modal live region — NOT a focus trap, NO scrim
  if (strip.getAttribute("role") !== "region") fail("ticker should be a region, not a dialog");
  if (strip.getAttribute("aria-live") !== "polite") fail("ticker should be an aria-live=polite region");
  if (strip.getAttribute("aria-modal")) fail("ticker must not be modal — you watch it while working");
  if (D.querySelector(".modal-scrim")) fail("ticker must not add a backdrop scrim");

  let chips = [];
  for (let i = 0; i < 40; i++) { await sleep(60); chips = [...strip.querySelectorAll(".tchip")]; if (chips.length) break; }
  if (!chips.length) fail("no signed-event chips rendered: " + strip.textContent.slice(0, 140));
  if (!chips.every(c => c.querySelector("span[style*='border-radius:50%']"))) fail("each chip must show a discrete verdict lamp");
  // honesty: a verdict-less event must SAY so in the tooltip, not stay silent
  if (!chips.some(c => /no verdict on this event/i.test(c.getAttribute("title") || ""))) fail("verdict-less chip tooltip is silent — must signal 'no verdict on this event'");

  // tamper honesty: the seed strips one event's signature — that chip must be
  // flagged (⚠ unsigned marker + UNSIGNED in the tooltip), and signed chips
  // must not carry the flag.
  const unsigned = chips.filter(c => /⚠ unsigned/.test(c.textContent));
  if (unsigned.length !== 1) fail("expected exactly 1 unsigned-flagged chip, got " + unsigned.length);
  if (!/UNSIGNED/.test(unsigned[0].getAttribute("title") || "")) fail("unsigned chip tooltip does not say UNSIGNED");
  if (!chips.some(c => /· signed/.test(c.getAttribute("title") || ""))) fail("no chip reports signed in its tooltip");

  // read-only: chips are role=button spans; no <button> writes inside the strip
  if (strip.querySelectorAll("button").length) fail("ticker must be read-only — found a <button>");
  if (!chips[0].getAttribute("onclick") || !/openAuditPanel/.test(chips[0].getAttribute("onclick"))) fail("a chip should jump to the signed record");

  // meter bridge — a discrete verdict-tally meter cluster (the DAW bridge feel),
  // read-only, aria-labelled, with at least one verdict meter showing a count
  const meters = strip.querySelector(".mbmeters");
  if (!meters) fail("meter bridge missing — no .mbmeters verdict-tally cluster");
  if (!/verdict mix/i.test(meters.getAttribute("aria-label") || "")) fail("meter cluster has no non-visual (aria) verdict-mix label");
  if (![...meters.querySelectorAll(".mtr")].length) fail("meter bridge shows no verdict meters");
  if (![...meters.querySelectorAll(".mtr b")].some(b => /^\d+$/.test(b.textContent.trim()))) fail("verdict meters show no discrete count");

  // toggle off removes it (and stops polling)
  window.toggleTicker();
  await sleep(60);
  if (D.getElementById("tickerstrip")) fail("toggling the ticker off did not remove the strip");

  console.log("PASS: meter bridge — verdict-tally meters (read-only, aria-labelled) + signed-event chips with discrete verdict lamps; a stripped signature is flagged unsigned; non-modal live region; toggles off clean");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
