// Real DOM test for the Connected tools drawer. Opens it, asserts the channels render
// under their client group-bus with floor chips, the joined strictest-wins verdict
// shows with a disagreement badge, and the mute / mute-client kill switches exist.
// The drawer ships as its own pack bundle (app/src/panels/federation.js),
// registered through the panel-mount contract
// (docs/loomground-proposals/panel-mount-contract.md), so this gate loads the
// composed page (GET /classic) rather than a bare readFileSync of the shell
// source — a raw index.html would open a frame with nothing inside it.
// Usage: node federation_render.mjs <PORT> <FOLDER>
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
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(60);

  // open the Connected tools drawer (the Rules menu item calls this)
  if (typeof window.openFederationPanel !== "function") fail("openFederationPanel is not wired");
  window.openFederationPanel();
  const panel = D.getElementById("federationpanel");
  if (!panel) fail("Connected tools drawer did not open");
  // wait for the async load (connector_list + federated_decision)
  let out = D.getElementById("fdout");
  for (let i = 0; i < 60 && /loading…/.test(out.textContent); i++) await sleep(80);
  const t = out.textContent;

  // channels render under their client group-bus
  if (!/n8n/.test(t)) fail("the client group 'n8n' is not shown");
  if (!/group bus/.test(t)) fail("the group-bus label is missing");
  if (!/n8n-jira/.test(t) || !/n8n-gh/.test(t)) fail("a channel is missing from the group");
  if (!/floor/.test(t) || !/hold/.test(t)) fail("the per-channel floor (hold) is not shown");

  // the joined strictest-wins verdict + disagreement badge
  if (!/Joined verdicts/.test(t)) fail("no joined-verdicts section");
  if (!/score/.test(t)) fail("the use case 'score' has no joined verdict");
  if (!/deny/.test(t)) fail("a lone tool deny did not drive the joined decision to deny");
  if (!/disagreement/.test(t)) fail("local-permit vs tool-deny disagreement not surfaced");

  // the kill switches exist (channel mute + client mute)
  if (!panel.querySelector("[data-fedrev]")) fail("no per-channel mute (kill switch)");
  if (!panel.querySelector("[data-fedrg]")) fail("no per-client (group) mute");

  // the per-GROUP floor is shown on the group bus, and is settable
  const gf = panel.querySelector("[data-grpfloor]");
  if (!gf) fail("no per-group floor indicator on the group bus");
  if (!/group floor/.test(gf.textContent) || !/hold/.test(gf.textContent)) fail("group floor (hold) not shown on the n8n bus");
  if (!panel.querySelector("[data-fedgf]")) fail("no set-group-floor control on the desk");

  console.log("PASS: connected tools drawer — channels under their client group-bus with floors; per-GROUP floor shown + settable (); joined strictest-wins verdict with disagreement badge; channel + client mute kill switches");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
