// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Approvals inbox — a pack entry behind
// docs/loomground-proposals/panel-mount-contract.md, and a write panel: this
// drawer asks a person to sign off, and records the decision, but never
// decides on its own. It joins two engines beside each other: named-signer
// contract reviews (workspace_contract, unanimity — any one rejection blocks
// the action; approval needs every required signer) and role-quorum
// reservation approvals (workspace_workflow's §1.5 engine — any m of a
// role set, no identities). access:"write" with no custom badge — the
// shell's default write chrome (no badge) applies, since every control this
// panel draws is a recorded decision, not a mostly-read view with one write
// bolted on.
Patchbay.register("approvals", {
  async open(ctx) {
    const { host, tool } = ctx;
    const { esc, escA } = ctx.ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Actions the matrix reserved for a person. <b>Any one rejection blocks the action</b> " +
      "(fail-safe, instant); approval needs <b>every</b> required signer. The server records " +
      "each decision — this asks, it does not decide.";
    host.appendChild(intro);

    const controls = document.createElement("div");
    controls.style.cssText = "display:flex;gap:8px;align-items:center;margin-bottom:8px";
    controls.innerHTML =
      '<label style="font-size:11px;color:var(--txt-dim)">acting as <input id="apactor" value="operator" style="background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:3px 6px;font-size:11px;width:120px"></label>' +
      '<label style="font-size:11px;color:var(--txt-dim)">show <select id="apfilter" style="background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:3px 6px;font-size:11px"><option value="pending">pending</option><option value="">all</option></select></label>';
    host.appendChild(controls);

    const out = document.createElement("div");
    out.id = "apout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    let items = [];
    let wsItems = [];
    let loadGeneration = 0;

    const fmtDur = (sec) => {
      sec = Math.max(0, Math.round(sec));
      const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
      return d > 0 ? (d + "d " + h + "h") : (h > 0 ? (h + "h " + m + "m") : (m + "m"));
    };

    // §1.5 reservation approvals (role quorum + temporal) — a SEPARATE engine
    // (role-based, no identities), shown beside the named-signer contract
    // reviews.
    const renderWsApprovals = (list) => {
      const now = Date.now() / 1000;
      const sb = (s) => { const c = s === "granted" ? "#92c4ac" : (s === "denied" ? "#d98b8b" : "#e6b483"); return '<span style="font-size:10px;border:1px solid ' + c + ";color:" + c + ';border-radius:8px;padding:1px 7px">' + esc(s || "pending") + "</span>"; };
      let h = '<div style="font-size:10px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px;margin:12px 0 6px">reservation approvals · role quorum</div>';
      list.forEach((a) => {
        const needed = a.needed || a.quorum || 0, got = ((a.approvers) || []).length;
        let slots = "";
        for (let k = 0; k < needed; k++) { const on = k < got; slots += '<span aria-hidden="true" style="display:inline-block;width:34px;height:12px;border-radius:3px;margin-right:5px;' + (on ? "background:#92c4ac" : "background:var(--bg);border:1px solid var(--line)") + '"></span>'; }
        const roles = ((a.competences) || []).join(", ");
        let temporal = "";
        if (a.deadline) {
          const rem = a.deadline - now, proceed = a.on_elapse === "proceed", col = proceed ? "#e6b483" : "#d98b8b";
          temporal = rem > 0
            ? ('<div style="font-size:11px;color:' + col + ';margin-top:4px">⧗ ' + (proceed ? "auto-proceeds in " + esc(fmtDur(rem)) + " — fail-open" : "halt in " + esc(fmtDur(rem))) + "</div>")
            : ('<div style="font-size:11px;color:' + col + ';margin-top:4px">' + (proceed ? "proceeded (no sign-off)" : "denied (timed out)") + "</div>");
        }
        const rid = String(a.request_id || "");
        h += '<div class="finding info" style="margin-bottom:10px" data-request-id="' + escA(esc(rid)) + '"><span class="ttl">' + esc(rid) + " " + sb(a.state) + "</span>"
          + '<div style="font-size:11px;color:var(--txt-dim);margin:2px 0">' + got + " of " + needed + " signed" + (roles ? (" · any of {" + esc(roles) + "}") : "") + "</div>"
          + '<div style="margin:4px 0" aria-label="' + got + " of " + needed + ' signed">' + slots + "</div>"
          + temporal
          + (a.state === "pending" ? '<div style="margin-top:6px"><button class="tool" style="padding:2px 8px" data-rid="' + escA(esc(rid)) + '" data-ws-decision="approve">approve</button><button class="tool" style="padding:2px 8px;border-color:#d98b8b;color:#d98b8b" data-rid="' + escA(esc(rid)) + '" data-ws-decision="deny">reject</button></div>' : "")
          + "</div>";
      });
      return h;
    };

    const decideWsApproval = async (rid, decision) => {
      const actor = ((host.querySelector("#apactor")) || {}).value || "operator";
      if (decision === "deny" && !confirm("Reject blocks this action immediately (fail-safe). Record " + actor + "’s rejection?")) return;
      try {
        await tool("workspace_workflow", { op: "approval_decide", params: { folder_context: ctx.workspace.path, request_id: rid, decision: decision, actor: actor, now: Date.now() / 1000 } });
      } catch (e) {
        alert("Could not record decision: " + ((e && e.message) || "failed"));
        return;
      }
      load();
    };

    const decideApproval = async (idx, signer, decision) => {
      const a = items[idx];
      if (!a) return;
      const actor = ((host.querySelector("#apactor")) || {}).value || "operator";
      const comment = ((out.querySelector("#apc" + idx)) || {}).value || "";
      if (decision === "rejected" && !confirm("Reject blocks this action immediately (fail-safe). Record " + signer + "’s rejection?")) return;
      try {
        await tool("workspace_contract", { op: "record_approval", params: { folder_context: ctx.workspace.path, approval_id: a.approval_id, signer: signer, decision: decision, comment: comment, actor: actor } });
      } catch (e) {
        alert("Could not record decision: " + ((e && e.message) || "failed"));
        return;
      }
      load();
    };

    const load = async () => {
      const generation = ++loadGeneration;
      if (!ctx.workspace.path) { out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its approvals</div>'; return; }
      const fsel = host.querySelector("#apfilter");
      const state = fsel ? fsel.value : "pending";
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
      let r;
      try {
        const p = { folder_context: ctx.workspace.path };
        if (state) p.state = state;
        r = await tool("workspace_contract", { op: "list_approvals", params: p });
      } catch (e) {
        if (generation !== loadGeneration) return;
        out.innerHTML = '<div class="finding warn"><span class="ttl">Could not load approvals</span>' + esc((e && e.message) || "failed") + "</div>";
        return;
      }
      items = (r && r.approvals) || [];
      try {
        const wp = { folder_context: ctx.workspace.path, now: Date.now() / 1000 };
        if (state) wp.state = state;
        const wr = await tool("workspace_workflow", { op: "approval_list", params: wp });
        wsItems = (wr && wr.approvals) || [];
      } catch (e) {
        wsItems = [];
      }
      if (generation !== loadGeneration) return;
      if (!items.length && !wsItems.length) { out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">no ' + esc(state || "") + " approval requests — nothing waiting on a person</div>"; return; }
      const badge = (s) => { const c = s === "approved" ? "#92c4ac" : (s === "rejected" ? "#d98b8b" : "#e6b483"); return '<span style="font-size:10px;border:1px solid ' + c + ";color:" + c + ';border-radius:8px;padding:1px 7px">' + esc(s || "pending") + "</span>"; };
      let h = "";
      if (items.length) h += '<div style="font-size:10px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px;margin:2px 0 6px">contract sign-offs · named signers</div>';
      items.forEach((a, i) => {
        const dec = a.signer_decisions || {};
        const overall = a.overall_state || "pending";
        let signers = "";
        (a.signers || []).forEach((sg) => {
          const d = dec[sg]; const dv = (d && typeof d === "object") ? d.decision : d; const dc = (d && typeof d === "object") ? d.comment : "";
          if (dv) signers += '<div style="font-size:11px;margin:3px 0"' + (dc ? ' title="' + escA(esc(dc)) + '"' : "") + ">" + esc(sg) + " — " + badge(dv) + (dc ? ' <span style="color:var(--txt-dim)">💬</span>' : "") + "</div>";
          else signers += '<div style="display:flex;gap:6px;align-items:center;margin:3px 0;font-size:11px">' + esc(sg) + " — "
            + '<button class="tool" style="padding:2px 8px" data-idx="' + i + '" data-signer="' + escA(esc(sg)) + '" data-decision="approved">approve</button>'
            + '<button class="tool" style="padding:2px 8px;border-color:#d98b8b;color:#d98b8b" data-idx="' + i + '" data-signer="' + escA(esc(sg)) + '" data-decision="rejected">reject</button></div>';
        });
        h += '<div class="finding info" style="margin-bottom:10px" data-contract-id="' + escA(esc(String(a.contract_id || a.approval_id || ""))) + '"><span class="ttl">' + esc(a.action_summary || a.contract_id || a.approval_id) + " " + badge(overall) + "</span>"
          + '<div style="font-size:11px;color:var(--txt-dim)">' + esc(a.reason || "") + "</div>"
          + '<div style="font-size:10px;color:var(--txt-dim);margin:2px 0">contract ' + esc(a.contract_id || "—") + " · requested by " + esc(a.requested_by || "—") + (a.requested_at ? " · " + esc(a.requested_at) : "") + (a.deadline ? " · due " + esc(a.deadline) : "") + "</div>"
          + signers
          + (overall === "pending" ? '<input id="apc' + i + '" placeholder="optional comment for your decision" style="width:100%;margin-top:4px;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:3px 6px;font-size:11px">' : "")
          + "</div>";
      });
      if (wsItems.length) h += renderWsApprovals(wsItems);
      out.innerHTML = h;
      out.querySelectorAll("[data-idx][data-signer]").forEach((b) => {
        b.addEventListener("click", () => decideApproval(Number(b.dataset.idx), b.dataset.signer, b.dataset.decision));
      });
      out.querySelectorAll("[data-rid][data-ws-decision]").forEach((b) => {
        b.addEventListener("click", () => decideWsApproval(b.dataset.rid, b.dataset.wsDecision));
      });
    };

    controls.querySelector("#apfilter").addEventListener("change", load);
    await load();
  },
});
