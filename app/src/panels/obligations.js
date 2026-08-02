// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Obligations panel — the fifth pack entry behind
// docs/loomground-proposals/panel-mount-contract.md. Read-only projection of
// the contract runtime's obligation registry, in severity order. States
// advance only through the tick (which stays in the Contracts panel);
// breach is never auto-declared: the machine stops at breach candidate and
// a person decides. This bundle calls only workspace_contract's obligations
// read op (registry read, then a per-obligation history read on drill-in).
Patchbay.register("obligations", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc, escA } = ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "What the record shows as of the last tick. States advance only when " +
      "the tick runs (Rules → Contracts); breach is never auto-declared.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "oblout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">reading the registry…</div>';
    host.appendChild(out);

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to read its obligations</div>';
        return;
      }
      let r;
      try {
        r = await tool("workspace_contract", { op: "obligations", params: { folder_context: ctx.workspace.path } });
      } catch (e) {
        r = { error: (e && e.message) || "failed" };
      }
      if (r.error || r.ok === false) {
        out.innerHTML = '<div class="finding warn"><span class="ttl">Obligations — could not read</span>' + esc(r.error || "unavailable") + "</div>";
        return;
      }
      const mono = "font-family:IBM Plex Mono,monospace";
      const BINS = [
        ["breached_candidate", "breach candidate", "deadline passed, per the record — a person decides", "#e2554a"],
        ["escalated", "escalated", "handed to the decision queue", "#df8b46"],
        ["due", "due", "deadline is the as-of day", "#df8b46"],
        ["due_soon", "due soon", "inside the 14-day warning window", "#c8a23f"],
        ["pending", "pending", "deadline known, not yet near", "#8fb9d6"],
      ];
      const row = (o) =>
        '<div class="oblrow" role="button" tabindex="0" data-oid="' + escA(o.obligation_id) + '" style="display:flex;gap:8px;align-items:baseline;border-top:1px solid #2a2f39;padding:5px 0;cursor:pointer">' +
        '<span style="' + mono + ';font-size:9.5px;color:var(--txt-dim)">' + esc(o.obligation_id) + "</span>" +
        '<span style="font-size:11.5px;flex:1">' + esc(o.summary || "") + "</span>" +
        '<span style="' + mono + ';font-size:10px;color:var(--txt-dim)">' + (o.deadline ? esc(o.deadline) : (o.deadline_rel ? "unresolved — " + esc(String(o.deadline_rel.event || "")) + " date unknown" : "no deadline")) + "</span></div>" +
        '<div class="oblhist" data-hist="' + escA(o.obligation_id) + '" style="display:none;padding:2px 0 6px 12px"></div>';
      let h = "";
      for (const [key, label, gloss, color] of BINS) {
        const rows = (r.buckets && r.buckets[key]) || [];
        if (key === "escalated" && !rows.length) continue; // extra bin — shown only when the queue has taken something
        const dim = rows.length ? "" : "opacity:.45;";
        h += '<div style="' + dim + 'margin-bottom:10px"><div style="display:flex;align-items:baseline;gap:8px"><b style="font-family:Space Grotesk,sans-serif;font-size:12px;color:' + color + '">' + esc(label) + '</b><span style="' + mono + ';font-size:10px;color:var(--txt)">' + rows.length + '</span><span style="font-size:10px;color:var(--txt-dim)">' + esc(gloss) + "</span></div>" +
          (rows.length ? rows.map(row).join("") : '<div class="ro" style="font-size:10px;color:var(--txt-dim);border-top:1px solid #2a2f39;padding:4px 0">none</div>') + "</div>";
      }
      const un = r.unresolved_deadlines || [];
      if (un.length) h += '<div class="finding warn"><span class="ttl">Unresolved deadlines</span>' + esc(un.length) + " obligation(s) name an event date the record does not carry — the tick cannot place them on the calendar: <span style=\"" + mono + '">' + esc(un.join(", ")) + "</span></div>";
      const cc = r.closed_counts || {};
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:4px">closed: satisfied ' + esc(cc.satisfied || 0) + " · waived " + esc(cc.waived || 0) + " · superseded " + esc(cc.superseded || 0) + "</div>";
      out.innerHTML = h;
      out.querySelectorAll(".oblrow").forEach((el) => {
        const openRow = async () => {
          const oid = el.dataset.oid;
          const hist = out.querySelector('[data-hist="' + oid + '"]');
          if (!hist) return;
          if (hist.style.display !== "none") { hist.style.display = "none"; return; }
          hist.style.display = "block";
          if (!hist.innerHTML) {
            hist.innerHTML = '<div class="ro" style="font-size:10px;color:var(--txt-dim)">reading history…</div>';
            let d;
            try {
              d = await tool("workspace_contract", { op: "obligations", params: { folder_context: ctx.workspace.path, obligation_id: oid } });
            } catch (e) {
              d = { error: (e && e.message) || "failed" };
            }
            if (d.error || d.ok === false) {
              hist.innerHTML = '<div class="ro" style="font-size:10px;color:var(--txt-dim)">could not read: ' + esc(d.error || "") + "</div>";
              return;
            }
            const items = (d.history || []).map((en) =>
              '<div style="font-size:10px;color:var(--txt-dim)"><span style="' + mono + '">' + esc(String(en.at || "").slice(0, 19)) + "</span> → <b style=\"color:var(--txt)\">" + esc(en.state || "") + "</b>" + (en.actor ? " · " + esc(en.actor) : "") + (en.reason ? " · " + esc(en.reason) : "") + "</div>"
            ).join("");
            hist.innerHTML = items || '<div class="ro" style="font-size:10px;color:var(--txt-dim)">no transitions recorded — instantiated and untouched</div>';
          }
        };
        el.addEventListener("click", openRow);
        el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openRow(); } });
      });
    };

    await load();
  },
});
