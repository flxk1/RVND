// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Lens panel — the ninth pack entry behind
// docs/loomground-proposals/panel-mount-contract.md, and (with audit and
// erasure) one of the write panels: reading the spend log and the precedent
// shelf (workspace_lens "log" / "precedent_list") takes no write, but setting
// the learning-cost cap and declaring or revoking a precedent are governed,
// recorded writes. Tightening (lowering the cap) is direct; loosening
// (raising the cap, declaring or revoking a precedent) confirms first and,
// for the cap, requires a typed reason. The manifest declares a custom
// "reads · sets cap/precedent" badge (panel-mount-contract's badge extension)
// since this panel is not purely read.
Patchbay.register("lens", {
  async open(ctx) {
    const { host, tool } = ctx;
    const { esc, escA } = ctx.ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Spend against this workspace’s cost cap, and the precedents that pre-decide repeat cases. " +
      "The server decides and records; this drawer submits a request. Raising the cap or declaring " +
      "a precedent loosens — it asks first.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "lout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    let curPre = [];

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its spend &amp; limits</div>';
        return;
      }
      const card = (t, b, k) => '<div class="finding ' + (k || "info") + '" style="margin-bottom:8px"><span class="ttl">' + t + "</span>" + b + "</div>";
      const get = async (op) => {
        try {
          return await tool("workspace_lens", { op, params: { folder_context: ctx.workspace.path } });
        } catch (e) {
          return { error: (e && e.message) || "failed" };
        }
      };
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
      const [log, pre] = await Promise.all([get("log"), get("precedent_list")]);
      let h = "";
      if (log.error) h += card("Spend — could not read", esc(log.error), "warn");
      else {
        const cap = log.cap, spent = log.spent || 0, held = log.held || 0;
        if (cap == null) h += card("Spend", "no cost cap set for this workspace · " + esc(spent) + " spent · " + esc(held) + " held", "info");
        else h += card(log.over_budget ? "⛔ Over the cost cap" : "Spend within cap", esc(spent) + " of " + esc(cap) + " spent · " + esc(held) + " held", log.over_budget ? "bad" : "ok");
        h += card("Spend log", esc(log.count || 0) + " spend event(s) recorded", "info");
      }
      const curCap = (log && log.cap != null) ? log.cap : null;
      if (pre.error) h += card("Precedents — could not read", esc(pre.error), "warn");
      else {
        const ps = pre.precedents || []; curPre = ps;
        if (!ps.length) h += card("Precedents", "none declared — every case is decided fresh", "info");
        else h += card("Precedents", esc(pre.count || ps.length) + " declared" + ps.slice(0, 8).map((p, i) => {
          const id = (typeof p === "string") ? p : (p && (p.id || p.name) || "");
          return '<div style="display:flex;gap:6px;align-items:center;font-size:11px;margin-top:4px"><span style="flex:1">' + esc(id || "(precedent)") + '</span><button class="tool" data-lensrevoke="' + i + '" style="padding:1px 7px;border-color:#d98b8b;color:#d98b8b">revoke</button></div>';
        }).join(""), "info");
      }
      // ---- writes: cap (raise=confirm+reason, lower=direct) + declare precedent (confirm+rationale) ----
      const inp = 'style="width:100%;margin-top:4px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px"';
      h += '<details style="margin-top:8px"><summary style="cursor:pointer;font-size:11px;color:var(--txt-dim)">+ set the learning-cost cap</summary>'
        + '<div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:4px">A typed value the server stores (must be &gt; 0). Raising it lets the agent absorb more change before re-gating — that loosens, so it asks to confirm and records a reason.</div>'
        + '<input type="number" id="lenscap" min="0" step="0.5" placeholder="cap (e.g. 5)" value="' + (curCap != null ? escA(String(curCap)) : "") + '" ' + inp + '>'
        + '<input type="text" id="lenscapreason" placeholder="reason (required when raising)" ' + inp + '>'
        + '<button class="tool" id="lenscapbtn" style="margin-top:5px;width:100%">Set cost cap</button></details>';
      h += '<details style="margin-top:6px"><summary style="cursor:pointer;font-size:11px;color:var(--txt-dim)">+ declare a precedent</summary>'
        + '<div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:4px">A human decision the agent may then follow in matching cases — revocable, signed. It widens what the agent may do without asking, so it confirms.</div>'
        + '<input type="text" id="lenspid" placeholder="precedent id (one word)" ' + inp + '>'
        + '<input type="text" id="lenspchosen" placeholder="chosen option" ' + inp + '>'
        + '<input type="text" id="lensprationale" placeholder="rationale (required)" ' + inp + '>'
        + '<button class="tool" id="lenspbtn" style="margin-top:5px;width:100%">Declare precedent</button></details>';
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:4px">Over-budget is a fail-safe stop, not a dial. The server enforces; this drawer requests and shows the verdict.</div>';
      out.innerHTML = h;
      const cb = out.querySelector("#lenscapbtn"); if (cb) cb.addEventListener("click", () => lensSetCap(curCap));
      const pb = out.querySelector("#lenspbtn"); if (pb) pb.addEventListener("click", lensDeclarePrecedent);
      out.querySelectorAll("[data-lensrevoke]").forEach((b) => b.addEventListener("click", () => lensRevokePrecedent(Number(b.dataset.lensrevoke))));
    };

    const lensSetCap = async (prev) => {
      if (!ctx.workspace.path) return;
      const cap = Number((out.querySelector("#lenscap") || {}).value);
      if (!isFinite(cap) || cap <= 0) { announce("the cost cap must be a number greater than 0"); return; }
      const reason = ((out.querySelector("#lenscapreason") || {}).value || "").trim();
      if (prev != null && cap > Number(prev)) {
        if (!reason) { announce("raising the cap lets the agent absorb more change — a reason is required"); return; }
        if (!confirm("Raise the learning-cost cap from " + prev + " to " + cap + "? The agent may absorb more before re-gating. Recorded.")) return;
      }
      let msg;
      try {
        const r = await tool("workspace_lens", { op: "budget_cap_set", params: { folder_context: ctx.workspace.path, cap, actor: "app-user", reason } });
        msg = (r && (r.ok === false || r.error)) ? ("Could not set cap: " + esc(r.error || "refused")) : ("Cost cap set to " + esc(r.cap) + (prev != null ? " (was " + esc(prev) + ")" : "") + ".");
      } catch (e) {
        msg = "Could not set cap: " + ((e && e.message) || "failed");
      }
      try { await load(); } catch (_) { }
      announce(msg);
    };

    const lensDeclarePrecedent = async () => {
      if (!ctx.workspace.path) return;
      const id = ((out.querySelector("#lenspid") || {}).value || "").trim();
      if (!id) { announce("a precedent needs an id"); return; }
      if (!/^[\w.-]+$/.test(id)) { announce("the precedent id must be one word (letters, digits, - _ .)"); return; }
      const chosen = ((out.querySelector("#lenspchosen") || {}).value || "").trim();
      const rationale = ((out.querySelector("#lensprationale") || {}).value || "").trim();
      if (!rationale) { announce("a precedent the agent may follow needs a rationale"); return; }
      if (!confirm("Declare precedent “" + id + "”? The agent may follow it in matching cases without asking. Revocable, recorded.")) return;
      let msg;
      try {
        const r = await tool("workspace_lens", { op: "precedent_declare", params: { folder_context: ctx.workspace.path, id, chosen_option: chosen, rationale, actor: "app-user", learnable: true } });
        msg = (r && (r.ok === false || r.error)) ? ("Could not declare: " + esc(r.error || "refused")) : ("Precedent “" + esc(id) + "” declared, signed.");
      } catch (e) {
        msg = "Could not declare: " + ((e && e.message) || "failed");
      }
      try { await load(); } catch (_) { }
      announce(msg);
    };

    const lensRevokePrecedent = async (idx) => {
      if (!ctx.workspace.path) return;
      const p = (curPre || [])[idx];
      const id = (typeof p === "string") ? p : (p && (p.id || p.name));
      if (!id) return;
      const reason = prompt("Revoke precedent “" + id + "”? The agent may no longer follow it. Reason (recorded):", "");
      if (reason === null) return;
      let msg;
      try {
        const r = await tool("workspace_lens", { op: "precedent_revoke", params: { folder_context: ctx.workspace.path, id, reason: (reason || "").trim(), actor: "app-user" } });
        msg = (r && (r.ok === false || r.error)) ? ("Could not revoke: " + esc(r.error || "refused")) : ("Precedent “" + esc(id) + "” revoked, recorded.");
      } catch (e) {
        msg = "Could not revoke: " + ((e && e.message) || "failed");
      }
      try { await load(); } catch (_) { }
      announce(msg);
    };

    await load();
  },
});
