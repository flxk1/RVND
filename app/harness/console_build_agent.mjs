// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Step 2 release walk: build an agent, approve its governance lane, and prove
// that both records exist through the real HTTP bridge.
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { bridgeGlobals } from "./render_harness.mjs";
import { createStore } from "../src/units/state.mjs";
import { createPatchbay } from "../src/units/patchbay.mjs";
import { createRun } from "../src/units/run.mjs";
import { createMatrix } from "../src/units/matrix.mjs";

const PORT = process.argv[2];
const WORKSPACE = process.argv[3];
const html = readFileSync(new URL("../src/console.html", import.meta.url), "utf8");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const fail = (message) => { console.log("FAIL: " + message); process.exit(1); };

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    bridgeGlobals(window, PORT);
    window.fetch = (url, options) => fetch(url, options);
    window.confirm = () => true;
    window.alert = (message) => { window.__lastAlert = String(message); };
    window.__RVND_createStore = createStore;
    window.__RVND_createPatchbay = createPatchbay;
    window.__RVND_createRun = createRun;
    window.__RVND_createMatrix = createMatrix;
  },
});
const { window } = dom;
const D = window.document;

async function until(predicate, message) {
  for (let i = 0; i < 160; i++) {
    if (predicate()) return;
    await sleep(25);
  }
  fail(message);
}

async function main() {
  await until(() => window._ready && window.__RVND.patchbay, "console did not boot");
  if (window.__RVND.store.getState().outside.count !== 0) fail("release walk did not start from a fresh zero-workspace install");
  D.querySelector("#ws-add").click();
  if (D.querySelector("#ws-dialog").hidden) fail("Create or open workspace did not open its dialog");
  D.querySelector("#ws-path").value = WORKSPACE;
  D.querySelector("#ws-label").value = "Release Workspace";
  D.querySelector("#ws-create").click();
  await until(
    () => window.__RVND.store.getState().activeWorkspace,
    "Create or open workspace did not establish the first workspace" + (window.__lastAlert ? ": " + window.__lastAlert : ""),
  );
  const fc = window.__RVND.store.getState().activeWorkspace;
  const beforeTail = await window.__RVND.call("workspace_audit", {
    op: "tail", params: { folder_context: fc, limit: 200 },
  });
  const beforeCount = (beforeTail.events || []).length;
  D.querySelector("#record-settings").click();
  if (D.querySelector("#record-dialog").hidden) fail("Record settings did not open");
  D.querySelector("#record-show-activity").click();
  if (!/activity hidden · recording continues/.test(D.querySelector("#rd-happened").textContent)) {
    fail("activity visibility toggle did not hide Read activity");
  }
  D.querySelector("#record-close").click();
  // Build renders inspect-only now — it mints nothing (agents arrive at the
  // handshake; tasks come from the activated policy). Wait for the patch to
  // paint, then register the agent through the backend, exactly as the
  // handshake's ensure_party would, and set its governance lane on Run (the
  // mixdesk), where the channel strips now live.
  await until(() => D.querySelector(".pb-toolbar"), "Build did not render the patch");

  const reg = await window.__RVND.call("workspace_policy", {
    op: "party_register",
    params: { folder_context: fc, party_id: "release-bot", kind: "agent", name: "Release Bot", actor: "release-owner" },
  });
  if (!reg || reg.ok === false) fail("backend party_register did not accept the agent" + (reg && reg.error ? ": " + reg.error : ""));

  // Switch to Run; it projects the agent as a lane strip with a register action.
  D.querySelector("[data-centre=run]").click();
  await until(() => D.querySelector(".rn-lane-open"), "Run did not render a governance-lane strip for the agent");
  D.querySelector(".rn-lane-open").click();

  D.querySelector("#rn-lane-grade").value = "L2";
  D.querySelector("#rn-lane-actions").value = "summarise, classify";
  D.querySelector("#rn-lane-fpr").value = "sha256:release-walk";
  D.querySelector("#rn-lane-approver").value = "release-owner";
  D.querySelector("#rn-lane-rationale").value = "Bounded release verification";
  D.querySelector(".rn-lane-save").click();

  await until(
    () => /max L2/.test((D.querySelector(".rn-strip") || {}).textContent || ""),
    "saved governance lane never appeared on the Run strip" + (window.__lastAlert ? ": " + window.__lastAlert : ""),
  );

  const parties = await window.__RVND.call("workspace_policy", {
    op: "party_list", params: { folder_context: fc },
  });
  const agent = (parties.parties || parties.rows || []).find((party) => party.name === "Release Bot");
  if (!agent) fail("backend party_list does not contain the created agent");
  const lanes = await window.__RVND.call("workspace_workflow", {
    op: "governance_lane_list", params: { folder_context: fc },
  });
  const lane = (lanes.lanes || []).find((item) => item.agent === (agent.party_id || agent.id));
  if (!lane) fail("backend governance_lane_list does not contain the agent lane");
  if (lane.max_grade !== "L2") fail("lane grade was not persisted");
  if (JSON.stringify(lane.action_classes) !== JSON.stringify(["summarise", "classify"])) {
    fail("lane action classes were not persisted");
  }
  if (lane.policy_fingerprint !== "sha256:release-walk") fail("lane fingerprint was not persisted");
  const afterTail = await window.__RVND.call("workspace_audit", {
    op: "tail", params: { folder_context: fc, limit: 200 },
  });
  if ((afterTail.events || []).length <= beforeCount) fail("hiding activity disabled integrity recording");

  console.log("PASS: install → first workspace → Step 2 — workspace, agent and L2 lane persisted; Record settings hid activity without disabling the signed chain");
  process.exit(0);
}

main().catch((error) => fail(String((error && error.stack) || error)));
