// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// RVND-owned gate guards — deliberately NOT in render_harness.mjs, which is
// vendored Patchbay surface (release/patchbay-consumption.json): vendored
// files change only by re-vendoring upstream, so RVND-side test armor lives
// here, beside it, on the right side of the consumption boundary.

// Authenticated-bridge probe for gates that read the server directly (a
// cross-check calling window.tool). Call once after the page boots: it proves
// the page's bridge actually authenticates against the running server, so a
// broken bridge fails the GATE with a named reason instead of letting a
// downstream cross-check compare against garbage. (The asleep-check lesson:
// a check that cannot run must fail, never pass.)
export async function assertBridgeAlive(window, fail) {
  try {
    const r = await window.tool("server_info", {});
    if (!r || typeof r !== "object") throw new Error("bridge returned " + String(r));
  } catch (e) {
    fail("bridge probe failed — the page cannot authenticate to the server, no cross-check can run: " + ((e && e.message) || e));
  }
}
