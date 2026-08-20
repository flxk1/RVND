// Real DOM test for the first-run onboarding wizard. Boots against an empty
// registry: the wizard must appear over the demo patch, walk create-workspace →
// register agent + task → cautious autonomy posture → the four-section tour,
// set the localStorage seen flag on finish, and never reappear once a
// workspace exists. Also covers the empty-workspace get-started strip.
// Usage: node wizard_render.mjs <PORT> <NEW_WORKSPACE_PATH>
import { JSDOM } from "jsdom";
import { bridgeGlobals, fetchComposedPage } from "../harness/render_harness.mjs";
const PORT = process.argv[2], NEW = process.argv[3];
const html = await fetchComposedPage(PORT);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  url: "http://localhost/",
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
const wiz = () => D.getElementById("onboardwiz");
const until = async (pred, what, tries = 80) => {
  for (let i = 0; i < tries; i++) { if (pred()) return; await sleep(60); }
  fail("timed out waiting for " + what + (wiz() ? " — wizard says: " + wiz().textContent.slice(0, 220) : ""));
};
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");

  // (1) empty registry → the wizard mounts over the demo patch
  if (!wiz()) fail("no workspaces + flag unset, but the wizard did not appear");
  if (wiz().getAttribute("aria-modal") !== "true") fail("wizard is not a modal dialog");
  if (!(window.S.g && window.S.g._sample)) fail("wizard should sit over the demo patch");

  // (2) welcome → create
  click(wiz().querySelector("#wznext"));
  await until(() => wiz().querySelector("#wzpath"), "the create-workspace step");

  // (3) create the first workspace through the wizard
  wiz().querySelector("#wzpath").value = NEW;
  click(wiz().querySelector("#wznext"));
  await until(() => wiz().querySelector("#wzagent"), "the agent+task step");
  if (window.S.path !== NEW && !(window.S.path || "").endsWith(NEW.split("/").pop())) fail("app did not switch to the new workspace — S.path=" + window.S.path);
  if (wiz()._active !== true) fail("mid-flow wizard lost its active guard");

  // (3a) the empty workspace behind the wizard shows the get-started strip
  const hint = D.getElementById("stagehint");
  if (!hint) fail("empty new workspace shows no get-started strip");
  if (!/get started/i.test(hint.textContent)) fail("strip is not the contextual get-started copy: " + hint.textContent.slice(0, 120));

  // (4) register agent + task
  click(wiz().querySelector("#wznext"));
  await until(() => wiz().querySelector("#wzcautious"), "the autonomy step");
  const kinds = (window.S.g.nodes || []).map((n) => n.kind);
  if (!kinds.includes("agent")) fail("agent did not land on the canvas — kinds: " + kinds.join(","));
  if (!kinds.includes("use_case")) fail("task did not land on the canvas — kinds: " + kinds.join(","));

  // (5) cautious posture — a real tighten-only set_all
  click(wiz().querySelector("#wzcautious"));
  await until(() => /four sections/i.test(wiz().textContent), "the tour step");
  for (const s of ["Set up", "Rules", "Pending", "Record"])
    if (!wiz().textContent.includes(s)) fail("tour is missing section: " + s);

  // (6) finish → seen flag set, wizard gone, no scrim
  click(wiz().querySelector("#wznext"));
  await sleep(80);
  if (wiz()) fail("wizard still open after Finish");
  if (D.querySelector(".modal-scrim")) fail("wizard leaked a scrim");
  if (window.localStorage.getItem("rvnd_onboarded") !== "1") fail("seen flag not persisted");

  // (7) never again: with a workspace present the wizard must not reappear —
  // even with the seen flag cleared (first-run means NO workspaces).
  await window.boot(); await sleep(100);
  if (wiz()) fail("wizard reappeared after onboarding");
  window.localStorage.removeItem("rvnd_onboarded");
  await window.boot(); await sleep(100);
  if (wiz()) fail("wizard appeared although a workspace exists");

  console.log("PASS: onboarding wizard — appears only on true first-run; creates workspace, registers agent+task, tightens the matrix, tours the four sections; seen flag persisted; never returns once a workspace exists; get-started strip on the empty workspace");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
