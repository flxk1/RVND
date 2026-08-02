// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Policy map panel — the thirteenth pack entry behind
// docs/loomground-proposals/panel-mount-contract.md. Paste Article-shaped
// policy text and rvnd projects each rule onto role · step · risk over the
// live governance_map op (governance_map/v1) — grouped, collapsible, gaps
// first. It maps; it never certifies compliance.
//
// governance_map is a pure projection (server/src/workspaces/op_mutation.py's
// _READ_OPS: no chain event, no state change), so this drawer stays
// access:"read" even though it authors a draft of the pasted text through
// ctx.drafts — draft_save/draft_load write unsigned scratch state beside the
// chain, not a governed act.
//
// renderMapContract stays a shared top-level function in app/src/index.html:
// the governance chat panel (openChatPanel/renderChatResult, shell chrome in
// app/src/shell/chat.js) renders the same governance_map/v1 contract for a
// routed policy paste, so this bundle calls that shared helper rather than
// forking a second copy of the same renderer. The same applies to the
// draft-chip helpers (draftChipHtml/draftChipMount/draftDiscard/
// draftPanelClosed) — shell-owned machinery the chat, cards and
// policy-paste surfaces still share, called here the same way
// already-migrated panels call the shell's `announce`.
Patchbay.register("map", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc } = ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0;display:flex;align-items:center;gap:8px";
    intro.innerHTML =
      "Paste policy text (Article-shaped). Rvnd maps each rule onto role · step · risk " +
      "— grouped, collapsible, gaps first. It maps; it does not certify compliance." +
      draftChipHtml("map");
    host.appendChild(intro);

    const ta = document.createElement("textarea");
    ta.id = "mptext";
    ta.setAttribute("aria-label", "Policy text");
    ta.placeholder = "Providers of high-risk AI systems shall establish a risk management system.";
    ta.style.cssText = "width:100%;height:110px;background:var(--panel-2);border:1px solid var(--line);color:#fff;font-family:IBM Plex Mono,monospace;font-size:12px;border-radius:6px;padding:8px";
    host.appendChild(ta);

    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:8px;align-items:center;margin-top:8px";
    row.innerHTML =
      '<label for="mpgroup" style="font-size:11px;color:var(--txt-dim)">group by</label>' +
      '<select id="mpgroup" style="background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;font-size:12px;padding:4px">' +
      '<option value="room">room</option><option value="role">role</option><option value="risk">risk</option>' +
      '<option value="demand">demand</option><option value="status">status</option></select>' +
      '<span style="flex:1"></span>' +
      '<button class="del" id="mpbuild" style="border-color:#3a3357;color:#b9acff">Map it</button>' +
      '<button class="del" id="mpdiscard" title="delete this panel\'s saved draft" style="border-color:#5a2f2a;color:#e6b0aa">Discard draft</button>';
    host.appendChild(row);

    const askrow = document.createElement("div");
    askrow.style.cssText = "display:flex;gap:8px;align-items:center;margin-top:8px";
    askrow.innerHTML =
      '<input id="mpask" type="text" aria-label="Ask about the rules" placeholder="ask… e.g. which rules need a human?" ' +
      'style="flex:1;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;font-size:12px;padding:6px 8px">' +
      '<button class="del" id="mpaskbtn" style="border-color:#2f4a3a;color:#8fd1ad">Ask</button>';
    host.appendChild(askrow);

    const out = document.createElement("div");
    out.id = "mpout";
    out.setAttribute("role", "status");
    out.setAttribute("aria-live", "polite");
    out.style.marginTop = "10px";
    host.appendChild(out);

    const mapPolicy = async () => {
      const policy = ta.value || "";
      const g = (host.querySelector("#mpgroup") || {}).value || "room";
      out.innerHTML = '<div class="ro" style="font-size:11px;color:var(--txt-dim)">mapping…</div>';
      let r;
      try {
        r = await tool("workspace_workflow", {
          op: "governance_map",
          params: { folder_context: ctx.workspace.path || "", policy_text: policy, instrument: "policy", view: { group_by: g, sort: "gaps" } },
        });
      } catch (e) {
        out.innerHTML = '<div class="ro">err: ' + esc((e && e.message) || "failed") + "</div>";
        return;
      }
      out.innerHTML = renderMapContract(r);
    };

    const askPolicy = async () => {
      const policy = ta.value || "";
      const q = (host.querySelector("#mpask") || {}).value || "";
      out.innerHTML = '<div class="ro" style="font-size:11px;color:var(--txt-dim)">asking…</div>';
      let r;
      try {
        r = await tool("workspace_workflow", {
          op: "governance_map",
          params: { folder_context: ctx.workspace.path || "", policy_text: policy, instrument: "policy", question: q },
        });
      } catch (e) {
        out.innerHTML = '<div class="ro">err: ' + esc((e && e.message) || "failed") + "</div>";
        return;
      }
      out.innerHTML = renderMapContract(r);
    };

    host.querySelector("#mpbuild").addEventListener("click", () => mapPolicy());
    host.querySelector("#mpgroup").addEventListener("change", () => mapPolicy());
    host.querySelector("#mpaskbtn").addEventListener("click", () => askPolicy());
    host.querySelector("#mpask").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); askPolicy(); } });

    // silent prefill of the map view state (text + grouping); edits re-save, debounced
    const mpg = host.querySelector("#mpgroup");
    const mpd = ctx.drafts.loaded("map");
    if (mpd) {
      if (typeof mpd.text === "string" && !ta.value) ta.value = mpd.text;
      if (mpd.group_by && [...mpg.options].some((o) => o.value === mpd.group_by)) mpg.value = mpd.group_by;
    }
    const mpq = () => ctx.drafts.queue("map", () => ({ text: ta.value, group_by: mpg.value }));
    ta.addEventListener("input", mpq);
    mpg.addEventListener("change", mpq);
    host.querySelector("#mpdiscard").addEventListener("click", () => { ta.value = ""; draftDiscard("map"); });
    draftChipMount("map");

    return { close() { draftPanelClosed("map"); } };
  },
});
