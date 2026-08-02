// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Shared bridge wiring for the jsdom render gates. Points the loaded page at
// the running serve.py shim and hands it the per-session token the server
// requires on POST /tool. The token is read from RVND_BRIDGE_TOKEN, which the
// paired *_render_test.py pins before starting the server and inherits to this
// node process. Each gate installs its own fetch shim; those shims forward the
// request headers, so the token the page attaches reaches the server.
import { readFileSync, readdirSync } from "node:fs";

export function bridgeGlobals(window, port) {
  window.__WORKSPACES_HTTP__ = `http://127.0.0.1:${port}/tool`;
  window.__WORKSPACES_TOKEN__ = process.env.RVND_BRIDGE_TOKEN || "";
}

// Fetches the composed page from the already-running serve.py — the shell
// document plus every panel-pack bundle compose_classic() inlines before
// </body>, manifest included. A panel moved behind the panel-mount contract
// (docs/loomground-proposals/panel-mount-contract.md) has no bundle in a
// raw readFileSync of app/src/index.html, so its gate needs the served
// artifact instead.
//
// serve.py's own bridge injection points __WORKSPACES_HTTP__ at the
// page-relative '/tool' (correct for a real browser, which resolves it
// against the page origin). A jsdom render gate has no browser origin, so
// this rewrites that one script to an absolute origin before the page is
// ever parsed — bridgeGlobals() would otherwise be clobbered the moment the
// served header script runs.
export async function fetchComposedPage(port) {
  const r = await fetch(`http://127.0.0.1:${port}/classic`);
  if (!r.ok) throw new Error(`GET /classic -> http ${r.status}`);
  const html = await r.text();
  return html.replace(
    "window.__WORKSPACES_HTTP__='/tool'",
    `window.__WORKSPACES_HTTP__='http://127.0.0.1:${port}/tool'`
  );
}

// Static equivalent of compose_classic()'s shell-chrome step, for the rare
// gate that asserts pure client-side logic (no server, no /tool bridge —
// e.g. verdict_resolve.mjs, m8_ceiling.mjs) and so cannot fetch /classic.
// Shell chrome (app/src/shell/*.js) is inlined unconditionally before the
// shell's own main <script> block, in name order — same placement and
// reasoning as host.py's compose_classic(): the shared window.X=X export
// line runs synchronously at parse time and references these functions
// directly, so they must exist before that line executes, not just before
// some later event handler. Panel-pack bundles are deliberately NOT
// included here — these gates never call panel functions (thin
// patchbayOpen() wrappers reference no removed global), only shell/core
// client logic, so no live server is needed to compose it.
export function composeStatic(indexUrl) {
  const html = readFileSync(indexUrl, "utf8");
  const shellDir = new URL("../src/shell/", indexUrl);
  let shellScripts = "";
  try {
    const names = readdirSync(shellDir).filter((n) => n.endsWith(".js")).sort();
    for (const n of names) {
      shellScripts += "<script>\n" + readFileSync(new URL(n, shellDir), "utf8") + "\n</script>\n";
    }
  } catch { /* no shell dir yet — nothing to inline */ }
  return html.replace("<script>", shellScripts + "<script>");
}
