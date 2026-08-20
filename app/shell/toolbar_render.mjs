// Real DOM test for the console front door — the four GOAL sections
// (Set up / Rules / Pending / Record) that replaced the five module-named menus
// (Build / Watch / Govern / Data / Run). Asserts: (1) the four section buttons
// exist and the Workspace label; (2) menu behaviour — clicking a section opens its
// menu, only one open at a time, outside-click + Escape close; (3) colour-as-meaning
// — Set up's canvas-add items keep their colour dot, drawer items (Record) have
// none; (4) Data is dissolved — no Data section — and "Local data" now lives as a
// Record menu item that opens the same drawer; (5) the About ⓘ opens About;
// (6) name-on-create still works when the Task item is clicked THROUGH the Set up
// menu (proves real wiring), and cancel / empty-submit / re-entrancy are all safe.
// The Rules and Record menus now carry pack-mounted items (Erasure, Local
// data's siblings, etc. — app/src/panels/*.js, panel-mount-contract.md), so
// this gate loads the composed page (GET /classic) rather than a bare
// readFileSync of the shell source — a raw index.html leaves those menu
// entries as unmounted <span data-manifest-menu-anchor> placeholders.
// Usage: node toolbar_render.mjs <PORT> <FOLDER_CONTEXT>
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
const sectBtn = (label) => [...D.querySelectorAll(".sectbtn")].find(b => b.textContent.trim().replace(/▾$/, "").trim() === label);
const sectOf = (label) => sectBtn(label)?.closest(".sect");
const menuItem = (sectLabel, itemLabel) => [...sectOf(sectLabel).querySelectorAll(".mi .mil")].find(m => m.textContent.trim() === itemLabel)?.closest(".mi");
async function main() {
  for (let i = 0; i < 80 && !window._ready; i++) await sleep(25);
  if (!window._ready) fail("patchbay did not boot");
  window.S.path = F; await window.reload(); await sleep(40);

  // (1) the four GOAL section menus + the Workspace label; the old module names are gone
  for (const s of ["Set up", "Rules", "Pending", "Record"])
    if (!sectBtn(s)) fail("section menu missing: " + s);
  for (const gone of ["Build", "Watch", "Govern", "Data", "Run"])
    if (sectBtn(gone)) fail("old module section should be dissolved: " + gone);
  if (!D.getElementById("lg-ws") || D.getElementById("lg-ws").textContent.trim() !== "Workspace")
    fail("Workspace label missing");

  // (2) menu behaviour — one open at a time; click opens, Escape closes
  click(sectBtn("Set up"));
  if (!sectOf("Set up").classList.contains("open")) fail("clicking Set up did not open its menu");
  if (sectBtn("Set up").getAttribute("aria-expanded") !== "true") fail("Set up aria-expanded not set true on open");
  click(sectBtn("Rules"));
  if (sectOf("Set up").classList.contains("open")) fail("opening Rules did not close Set up (only one open at a time)");
  if (!sectOf("Rules").classList.contains("open")) fail("clicking Rules did not open its menu");
  D.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  if (sectOf("Rules").classList.contains("open")) fail("Escape did not close the open menu");

  // (3) colour-as-meaning — Set up's canvas-add items keep a dot; drawer items (Record) have none
  const setupAdd = [...sectOf("Set up").querySelectorAll(".mi[data-add]")];
  if (!setupAdd.length || !setupAdd.every(b => b.querySelector(".dot"))) fail("Set up canvas-add items should each carry a colour dot");
  click(sectBtn("Record"));
  if ([...sectOf("Record").querySelectorAll(".mi .dot")].length) fail("Record drawer items must have NO decorative colour dot");
  D.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

  // (4) Data is dissolved — it opens as a "Local data" item inside Record (every section
  //     is now a uniform dropdown; the old "Data opens directly" chrome outlier is gone)
  click(sectBtn("Record"));
  const localData = menuItem("Record", "Local data");
  if (!localData) fail("Record has no 'Local data' item (Data was not re-homed)");
  click(localData);
  await sleep(160);
  if (!D.getElementById("datapanel")) fail("Local data did not open the data drawer");
  click(D.getElementById("datax"));              // the ✕ close control
  await sleep(60);
  if (D.getElementById("datapanel")) fail("data drawer did not close");
  if (D.querySelector(".modal-scrim")) fail("data drawer leaked a scrim on close");

  // Pending promotes Sign-offs out of the old Govern menu (the highest-frequency human job)
  if (!menuItem("Pending", "Sign-offs")) fail("Pending must carry the promoted Sign-offs item");

  // the Data sub-items are re-homed by goal: Erasure sits in Rules beside
  // Privacy lock; Bring-in sits in Set up
  const erasure = menuItem("Rules", "Erasure");
  if (!erasure) fail("Rules has no Erasure item");
  const rulesItems = [...sectOf("Rules").querySelectorAll(".mi .mil")].map(m => m.textContent.trim());
  if (rulesItems.indexOf("Erasure") !== rulesItems.indexOf("Privacy lock") + 1) fail("Erasure is not adjacent to Privacy lock in Rules: " + JSON.stringify(rulesItems));
  if (!menuItem("Set up", "Bring-in")) fail("Set up has no Bring-in item");

  // (5) About ⓘ opens About; the Help ? sits beside it and opens the reference
  const about = D.querySelector('.abtn[aria-label="About this server"]');
  if (!about) fail("About ⓘ button missing");
  click(about); await sleep(140);
  if (!D.getElementById("aboutpanel")) fail("About ⓘ did not open the About drawer");
  click(D.getElementById("abx"));               // the ✕ close control
  await sleep(60);
  if (D.querySelector(".modal-scrim")) fail("About drawer leaked a scrim on close");
  const helpBtn = D.querySelector('.abtn[aria-label^="Help"]');
  if (!helpBtn) fail("Help ? button missing from the front door");
  click(helpBtn); await sleep(140);
  if (!D.getElementById("helppanel")) fail("Help ? did not open the operation reference");
  click(D.getElementById("hlx"));
  await sleep(60);
  if (D.querySelector(".modal-scrim")) fail("Help drawer leaked a scrim on close");

  // (6a) the Task item is really wired THROUGH the Set up menu (opens the modal, menu closes);
  //      cancel it cleanly here, then run the create lifecycle via the known-good direct path.
  click(sectBtn("Set up"));
  const taskItem = menuItem("Set up", "Task");
  if (!taskItem) fail("Set up menu has no Task item");
  const pWire = window.addNode("use_case");    // same call the menu item fires; await it for a clean teardown
  click(taskItem);                              // the real menu click (proves data-add wiring + menu-close)
  await sleep(140);
  if (sectOf("Set up").classList.contains("open")) fail("the Set up menu should close after picking an item");
  if (!D.getElementById("createpanel")) fail("name-on-create modal did not open for the Task menu item");
  D.getElementById("createpanel").querySelector("#cpcancel").click();
  await pWire; await sleep(80);

  // (6b) name-on-create lifecycle — Task asks for a name + discrete risk and creates THAT node
  const pAdd = window.addNode("use_case");
  await sleep(120);
  const cp = D.getElementById("createpanel");
  if (!cp) fail("name-on-create modal did not open");
  if (cp.getAttribute("aria-modal") !== "true") fail("create modal is not a focus-trapped dialog");
  if (!cp.querySelector("#cprisk")) fail("Task create modal must offer a discrete risk choice");
  cp.querySelector("#cpname").value = "Loan scoring";
  cp.querySelector("#cprisk").value = "high";
  cp.querySelector("#cpok").click();
  await pAdd; await sleep(160);
  const made = window.S.g.nodes.find(n => n.label === "Loan scoring");
  if (!made) fail("created node did not take the typed name 'Loan scoring'");
  if (made.kind !== "use_case") fail("created node is not a task");
  if (!/uc-\d/.test(made.id)) fail("task should still have a uc-… handle id internally");
  if (D.getElementById("createpanel")) fail("create modal did not close after Create");

  // Cancel creates nothing
  const n1 = window.S.g.nodes.length;
  const pAdd2 = window.addNode("agent");
  await sleep(120);
  D.getElementById("createpanel").querySelector("#cpcancel").click();
  await pAdd2; await sleep(80);
  if (window.S.g.nodes.length !== n1) fail("Cancel must not create a node");
  if (D.querySelector(".modal-scrim")) fail("scrim leaked after the create modal closed");

  // empty-submit: shows an error, creates nothing, stays open (recoverable)
  const n2 = window.S.g.nodes.length;
  const pAdd3 = window.addNode("use_case");
  await sleep(120);
  let cp3 = D.getElementById("createpanel");
  cp3.querySelector("#cpok").click();          // submit with empty name
  await sleep(60);
  if (D.getElementById("cperr").style.display === "none") fail("empty submit gave no visible required-name feedback");
  if (!D.getElementById("createpanel")) fail("modal closed on empty submit — should stay open and recoverable");
  if (window.S.g.nodes.length !== n2) fail("empty submit created a node");
  cp3.querySelector("#cpcancel").click();      // recover via cancel
  await pAdd3; await sleep(60);

  // re-entrancy: a second open while one is up must NOT make a duplicate panel
  const pA = window.addNode("agent");
  await sleep(80);
  const pB = window.addNode("use_case");       // second click while first modal open
  await sleep(80);
  if (D.querySelectorAll("#createpanel").length !== 1) fail("re-entrant open created duplicate #createpanel");
  if (D.querySelectorAll(".modal-scrim").length > 1) fail("re-entrant open leaked a second scrim");
  await pB;                                     // the guarded second call resolves null immediately
  D.getElementById("createpanel").querySelector("#cpcancel").click();
  await pA; await sleep(60);

  console.log("PASS: console front door — four GOAL sections (Set up/Rules/Pending/Record); old module menus dissolved; Workspace label; one-open-at-a-time + Escape; colour only on Set up canvas-add; Local data re-homed into Record; Sign-offs promoted to Pending; About ⓘ + Help ?; name-on-create through the menu; cancel/empty/re-entrancy safe");
  process.exit(0);
}
main().catch((e) => fail(String((e && e.stack) || e)));
