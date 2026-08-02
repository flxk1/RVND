// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Decision workbench — the escalation surface built to
// docs/mockups/t4-decision-surface.html, and the pack entry behind
// docs/loomground-proposals/panel-mount-contract.md. One host serves four
// gate-named scenarios that are really one rendering path branching on
// server-declared state, not four UI modes: the workbench itself (options
// left, the recorded ask loop right, judgment + the one governed write
// below), the pending list (routing: assignment basis, claim lease,
// mine-filter, closure), action-link identity (acting-as, record via token,
// spent-link refusal) and co-decision (sealed badge, seat claim, counts, no
// rationale leak — the server never lets a sealed rationale reach the DOM).
//
// Two entry points stay in index.html rather than here, because they must
// work whether or not this panel is currently mounted: a decision surface
// arriving out of band (queueDecisionSurface — the render gates simulate a
// server push this way) and a holder's action-link deep link read from the
// URL at boot, before any panel exists (applyActionLink, called from boot()
// itself). Both write to window._decisionQueue / _decAuth / _decClaim /
// _decId — the pending-decision inbox this bundle reads once mounted — and
// both nudge an already-mounted host through host._decisionRefresh(), the
// same host-hook precedent contract.js uses for _contractSetTab, rather
// than duplicating render logic in the shell. window._decAsked is exposed
// the same way for one more reason: the render gate for the main workbench
// asserts on it directly (app/panels/decision_render.mjs) as the review
// trail's independent proof that an exchange really happened, separate from
// the receipt text. These five names are the one deliberate exception to
// "no globals" (panel-mount-contract.md §3.5) this bundle carries, forced by
// callers on both sides of the file boundary; every other piece of state
// (considered, chosen option, evidence, the mine-filter) is ordinary closure
// state below.
Patchbay.register("decision", {
  async open(ctx) {
    const { host, tool } = ctx;
    const { esc, escA } = ctx.ui;
    const mono = "font-family:IBM Plex Mono,monospace";

    const queueBadge = document.createElement("span");
    queueBadge.id = "decqueue";
    queueBadge.style.cssText = mono + ";font-size:9.5px;color:var(--txt-dim);border:1px solid var(--line);border-radius:12px;padding:2px 9px;display:inline-block;margin-bottom:6px";
    host.appendChild(queueBadge);

    const out = document.createElement("div");
    out.id = "decout";
    host.appendChild(out);

    // Ordinary panel-local state — reset per surface inside load(), below.
    let considered = {};
    let evidence = [];
    let choice = null;
    let mine = false;

    const renderList = async () => {
      if (!ctx.workspace.path) { out.innerHTML = '<div class="ro" style="font-size:11px;color:var(--txt-dim);margin-top:6px">open a folder to see its pending decisions</div>'; return; }
      let r; try { r = await tool("workspace_dispatch", { op: "decision_pending", params: { folder_context: ctx.workspace.path, for_party: (mine ? "app-user" : "") } }); } catch (e) { r = { ok: false, error: (e && e.message) || "failed" }; }
      if (!r || r.ok === false) { out.innerHTML = '<div class="finding warn"><span class="ttl">Pending decisions — could not read</span>' + esc((r && r.error) || "unavailable") + "</div>"; return; }
      const rows = r.pending || [];
      let h = '<div style="display:flex;align-items:center;gap:10px;margin:6px 0 8px"><span style="font-family:Space Grotesk,sans-serif;font-size:12px;color:#fff">Waiting for a person</span>'
        + '<label style="font-size:10.5px;color:var(--txt-dim)"><input type="checkbox" id="decmine"' + (mine ? " checked" : "") + '> only what I may claim</label></div>';
      if (!rows.length) h += '<div class="ro" style="font-size:11px;color:var(--txt-dim)">' + (mine ? "nothing you may claim — clear the filter to see every open decision" : "no escalation waits — a decision surface arrives here when a gate finds a residual only a person may resolve") + "</div>";
      rows.forEach((e) => {
        const isMine = e.claimed_by === "app-user";
        h += '<div style="display:flex;gap:10px;align-items:baseline;border-top:1px solid #2a2f39;padding:7px 0">'
          + '<span style="flex:1;font-size:12px">' + esc(e.query || "") + '<div style="' + mono + ';font-size:9px;color:var(--txt-dim);margin-top:2px">' + esc(e.decision_id) + " · " + esc(e.assignment_basis) + " · raised by " + esc(e.raised_by) + " · " + esc(e.option_count) + " options" + (e.priority ? ' · <span style="color:' + (e.priority === "urgent" ? "#e88" : "#e6b483") + '">' + esc(e.priority) + "</span>" : "") + (e.overdue ? ' · <span style="color:#e88">past its decide-by</span>' : "") + "</div></span>"
          + (e.panel ? '<span style="' + mono + ';font-size:9px;color:#c9a8e8" title="co-decision: choices stay sealed until the panel resolves">panel ' + e.panel.recorded + "/" + e.panel.seats + "</span>" : "")
          + (e.claimed_by ? (isMine ? '<span style="' + mono + ';font-size:9px;color:#8fb9d6">claimed by you</span>' : '<span style="' + mono + ';font-size:9px;color:#e6b483">claimed by ' + esc(e.claimed_by) + "</span>") : "")
          + ((!e.claimed_by || isMine || e.panel) ? '<button class="psbtn" data-decclaim="' + escA(e.decision_id) + '">' + (isMine ? "Resume" : (e.panel ? "Claim a seat" : "Claim & review")) + "</button>" : "")
          + "</div>";
      });
      out.innerHTML = h;
      const mineBox = out.querySelector("#decmine");
      if (mineBox) mineBox.addEventListener("change", () => { mine = mineBox.checked; load(); });
      out.querySelectorAll("[data-decclaim]").forEach((b) => b.addEventListener("click", async () => {
        b.disabled = true;
        let c; try { c = await tool("workspace_dispatch", { op: "decision_claim", params: { folder_context: ctx.workspace.path, decision_id: b.dataset.decclaim, actor: "app-user" } }); } catch (e) { c = { ok: false, error: (e && e.message) || "failed" }; }
        if (!c || c.ok === false) { b.disabled = false; announce("Could not claim: " + ((c && c.error) || "refused")); load(); return; }
        window._decId = b.dataset.decclaim; window._decClaim = c;
        (window._decisionQueue = window._decisionQueue || []).unshift(c.surface);
        load();
      }));
    };

    const load = async () => {
      const q = window._decisionQueue || [];
      queueBadge.textContent = q.length ? ("decision 1 of " + q.length) : "none waiting";
      if (!q.length) { await renderList(); return; }
      const s = q[0];
      considered = {}; window._decAsked = []; evidence = []; choice = null;
      let h = '<div style="font-family:Space Grotesk,sans-serif;font-size:16px;color:#f3f1ea;margin:8px 0 2px;max-width:760px">' + esc(s.query || "") + "</div>";
      h += '<div style="' + mono + ';font-size:9.5px;color:var(--txt-dim);margin-bottom:12px">' + (s.esc_reason ? '<span style="color:#e6b483">here because: ' + esc(s.esc_reason) + " — the engine does not decide that; you do.</span>" : "a residual choice — the engine presents grounds; the decision is reserved to you.") + "</div>";
      if (window._decClaim && window._decClaim.panel) { const pn = window._decClaim.panel; h += '<div style="' + mono + ';font-size:9.5px;color:#c9a8e8;margin:-8px 0 4px">co-decision — ' + pn.recorded + " of " + pn.seats + " seats recorded (" + esc(pn.rule) + (pn.rule !== "unanimous" ? ", m=" + pn.m : "") + "); choices stay sealed until the panel resolves</div>"; }
      if (window._decAuth) h += '<div style="' + mono + ';font-size:9.5px;color:#9fd8c0;margin:-8px 0 4px">acting as <b>' + esc(window._decAuth.party) + "</b> — authenticated by link (single-use; the record will say so)</div>";
      if (window._decClaim) h += '<div style="' + mono + ';font-size:9.5px;color:#8fb9d6;margin:' + (window._decAuth ? "0" : "-8px") + " 0 12px\">claimed by " + (window._decAuth ? esc(window._decAuth.party) : "you") + " · the lease holds until " + esc(String(window._decClaim.claim_expires_at || "").slice(0, 19)) + ' · <span id="decrel" role="button" tabindex="0" style="cursor:pointer;border-bottom:1px dotted #2f4358">release — widen it back to every holder (recorded)</span></div>';
      if (s.single_reading_warning) h += '<div class="finding warn"><span class="ttl">Only one defensible reading was assembled</span>Deciding on a single reading is still your call — but alternatives were not found, and the record will show that.</div>';
      h += '<div style="display:grid;grid-template-columns:1fr 340px;gap:14px;align-items:start">';
      // ---- decide (left): options in server order, nothing pre-selected ----
      h += '<div><div style="font-family:Space Grotesk,sans-serif;font-size:12px;color:#fff;margin-bottom:8px">The defensible options <span style="font-size:10px;color:var(--txt-dim);font-weight:400">as assembled — none recommended, none pre-selected' + (s.options_may_be_incomplete ? "; the list may be incomplete" : "") + "</span></div>";
      (s.options || []).forEach((o) => {
        h += '<div class="decopt" role="button" tabindex="0" data-opt="' + escA(o.id) + '" style="background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px;cursor:pointer">'
          + '<span style="float:right;' + mono + ';font-size:9px;color:var(--txt-dim)">' + esc(o.id) + " · grounds " + esc(o.grounding_band || "") + "</span>"
          + '<b style="font-family:Space Grotesk,sans-serif;font-size:12.5px;color:#fff">' + esc(o.label || "") + "</b>"
          + '<div style="font-size:11px;color:var(--txt-dim);margin-top:2px">' + esc((o.consequences || [])[0] || o.conclusion || "") + "</div></div>";
      });
      h += '<div style="font-size:11px;margin-top:2px"><span class="decgro" role="button" tabindex="0" style="color:#8fb9d6;border-bottom:1px dotted #2f4358;cursor:pointer">Read the grounds — cited law, full consequences ▸</span> <span style="color:var(--txt-dim)">· what you open is recorded as considered</span></div>';
      h += '<div id="decgrounds" style="display:none;margin-top:8px">';
      (s.options || []).forEach((o) => {
        h += '<details class="decgsec" data-gid="' + escA(o.id) + '" style="border:1px solid var(--line);border-radius:8px;background:var(--panel-2);margin-bottom:6px"><summary style="cursor:pointer;padding:7px 10px;font-size:11.5px;font-weight:600;color:#fff">' + esc(o.label) + ' <span style="' + mono + ';font-size:9px;color:var(--txt-dim)">' + esc(o.id) + " · " + esc(o.supporting_count != null ? o.supporting_count : (o.supporting || []).length) + " supporting</span></summary>"
          + '<div style="padding:2px 11px 10px;font-size:11px"><div style="color:var(--txt)">' + esc(o.conclusion || "") + "</div>"
          + ((o.supporting || []).map((sp) => '<div style="border-left:2px solid var(--human);padding:5px 9px;margin:5px 0;background:rgba(79,134,198,.06)">' + esc(sp.text || "") + '<span style="display:block;' + mono + ';font-size:9px;color:var(--txt-dim);margin-top:3px">' + esc(sp.pinpoint || sp.entity || "") + "</span></div>").join(""))
          + ((o.consequences || []).length ? '<div style="color:var(--txt-dim);margin-top:4px">consequences: ' + esc(o.consequences.join(" · ")) + "</div>" : "")
          + (o.reasons ? '<div style="color:var(--txt-dim);margin-top:4px">' + esc(o.reasons) + "</div>" : "") + "</div></details>";
      });
      if (s.residual !== false && s.note) h += '<div class="ro" style="font-size:10px;color:var(--txt-dim)">' + esc(s.note) + "</div>";
      h += "</div></div>";
      // ---- converse (right): the recorded ask loop ----
      h += '<div style="background:var(--panel-2);border:1px solid var(--line);border-radius:8px;display:flex;flex-direction:column;min-height:280px">'
        + '<div style="display:flex;align-items:center;gap:8px;padding:8px 11px;border-bottom:1px solid var(--line)"><b style="font-family:Space Grotesk,sans-serif;font-size:11.5px;color:#fff">Ask before you decide</b><span class="robadge" title="answers assist and are recorded; the agent never recommends an option">assist · recorded</span></div>'
        + '<div id="declog" style="flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:8px;max-height:260px"></div>'
        + '<div style="display:flex;gap:6px;padding:8px 10px;border-top:1px solid var(--line)"><input id="decq" placeholder="ask about this decision, the task, the folder…" style="flex:1;background:var(--panel);border:1px solid var(--line);border-radius:6px;color:#fff;font-size:11px;padding:6px 8px"><button class="psbtn" id="decask">Ask</button></div>'
        + '<div style="padding:0 10px 7px;' + mono + ';font-size:8.5px;color:var(--txt-dim)">asked questions join the review trail</div></div>';
      h += "</div>";
      // ---- your judgment (below): prose + evidence + the one write ----
      h += '<div style="border-top:1px solid var(--line);margin-top:12px;padding-top:10px"><div style="font-family:Space Grotesk,sans-serif;font-size:12px;color:#fff">Your judgment <span style="font-size:10px;color:var(--txt-dim);font-weight:400">this prose is the rationale the record keeps</span></div>'
        + '<textarea id="decrat" placeholder="why this option — in your own words, for the person who reads this in three years" style="width:100%;box-sizing:border-box;min-height:64px;margin-top:6px;background:var(--panel-2);border:1px solid var(--line);border-radius:8px;color:#fff;font-size:12px;line-height:1.6;padding:8px 10px;resize:vertical"></textarea>'
        + '<div style="display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap"><label class="psbtn" style="cursor:pointer">⎘ Attach evidence<input id="decfile" type="file" style="display:none"></label><span id="decevlist" style="' + mono + ';font-size:9.5px;color:var(--txt-dim)"></span><span style="font-size:10px;color:var(--txt-dim)">an attached file goes through the ingest boundary and is referenced by this decision</span></div>'
        + '<div style="display:flex;align-items:center;gap:12px;margin-top:10px"><button class="psbtn" id="decrec" style="border-color:#2f4a3a;color:#9fd8c0">Record the decision — signed</button><span id="decmsg" style="font-size:10.5px;color:#e3a877"></span><span style="font-size:10px;color:var(--txt-dim)">records your choice with rationale, evidence, what you considered and what you asked</span></div></div>';
      out.innerHTML = h;
      // wiring
      out.querySelectorAll(".decopt").forEach((el) => {
        const pick = () => { choice = el.dataset.opt; out.querySelectorAll(".decopt").forEach((e2) => { e2.style.borderColor = "var(--line)"; e2.style.background = "var(--panel-2)"; }); el.style.borderColor = "var(--human)"; el.style.background = "#1d2531"; };
        el.addEventListener("click", pick); el.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
      });
      const rel = out.querySelector("#decrel");
      if (rel) rel.addEventListener("click", async () => {
        let rr; try { rr = await tool("workspace_dispatch", { op: "decision_release", params: { folder_context: ctx.workspace.path || "", decision_id: window._decId, actor: "app-user" } }); } catch (e) { rr = { ok: false, error: (e && e.message) || "failed" }; }
        if (!rr || rr.ok === false) { announce("Could not release: " + ((rr && rr.error) || "refused")); return; }
        window._decId = null; window._decClaim = null; (window._decisionQueue || []).shift(); load();
      });
      const gro = out.querySelector(".decgro");
      if (gro) { const tog = () => { const g = out.querySelector("#decgrounds"); g.style.display = (g.style.display === "none") ? "block" : "none"; };
        gro.addEventListener("click", tog); gro.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); tog(); } }); }
      out.querySelectorAll(".decgsec summary").forEach((sm) => sm.addEventListener("click", () => { const id = sm.parentElement.dataset.gid; considered[id] = true; }));
      const ask = out.querySelector("#decask");
      if (ask) ask.addEventListener("click", async () => {
        const inp = out.querySelector("#decq"); const qq = (inp.value || "").trim(); if (!qq) return;
        const log = out.querySelector("#declog"); inp.value = "";
        log.insertAdjacentHTML("beforeend", '<div style="align-self:flex-end;background:#243040;border:1px solid #33445c;border-radius:8px;padding:6px 9px;font-size:11px;max-width:88%">' + esc(qq) + "</div>");
        log.insertAdjacentHTML("beforeend", '<div class="decthinking ro" style="font-size:10px;color:var(--txt-dim)">asking…</div>');
        let r; try { r = await tool("workspace_ask", { folder_context: ctx.workspace.path || "", query: "Context: a pending decision — " + (q[0].query || "") + " Question: " + qq }); } catch (e) { r = { ok: false, error: (e && e.message) || "failed" }; }
        const th = log.querySelector(".decthinking"); if (th) th.remove();
        const ans = (r && (r.answer || r.error)) || "no answer";
        const deg = !!(r && (r.degraded || (r.governance && r.governance.degraded) || r.ok === false));
        log.insertAdjacentHTML("beforeend", '<div style="align-self:flex-start;background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:6px 9px;font-size:11px;max-width:88%">' + esc(String(ans).slice(0, 600)) + (deg ? '<div style="font-family:IBM Plex Mono,monospace;font-size:8.5px;color:var(--txt-dim);margin-top:3px">degraded — answered from the record only</div>' : "") + '<div style="font-family:IBM Plex Mono,monospace;font-size:8.5px;color:var(--txt-dim);margin-top:3px">this exchange is on the record</div></div>');
        log.scrollTop = log.scrollHeight;
        window._decAsked.push({ query: qq, degraded: deg, audit_id: (r && r.audit_id) || null });
      });
      const file = out.querySelector("#decfile");
      if (file) file.addEventListener("change", async () => {
        const f = file.files && file.files[0]; if (!f) return;
        const list = out.querySelector("#decevlist");
        try {
          const text = await f.text();
          const rel = "evidence/" + f.name;
          const w = await tool("workspace_folder", { op: "write_file", params: { folder_context: ctx.workspace.path || "", relative_path: rel, content: text } });
          if (w && (w.error || w.ok === false)) throw new Error(w.error || "refused");
          evidence.push(rel);
          list.textContent = evidence.join(" · ") + " — brought inside · recorded";
        } catch (e) { list.textContent = "could not attach: " + ((e && e.message) || "failed"); }
      });
      const rec = out.querySelector("#decrec");
      if (rec) rec.addEventListener("click", async () => {
        const msg = out.querySelector("#decmsg");
        const rat = (out.querySelector("#decrat").value || "").trim();
        if (!choice) { msg.textContent = "pick an option first — nothing is pre-selected"; return; }
        if (!rat) { msg.textContent = "the record needs your rationale — origination, not a rubber stamp"; return; }
        rec.disabled = true; msg.textContent = "";
        const recParams = { folder_context: ctx.workspace.path || "", surface: q[0], chosen_option_id: choice, rationale: rat, considered: Object.keys(considered), asked: window._decAsked, evidence_refs: evidence };
        if (window._decAuth && window._decAuth.token) recParams.link_token = window._decAuth.token; else recParams.actor = "app-user";
        if (window._decId) recParams.decision_id = window._decId;
        let r; try { r = await tool("workspace_dispatch", { op: "decision_record", params: recParams }); } catch (e) { r = { ok: false, error: (e && e.message) || "failed" }; }
        rec.disabled = false;
        if (!r || r.ok === false) { msg.textContent = "refused: " + esc((r && r.error) || "unknown"); return; }
        q.shift(); window._decId = null; window._decClaim = null; window._decAuth = null;
        out.innerHTML = '<div class="finding ok"><span class="ttl">Recorded, signed' + (r.audit_id ? " — " + esc(r.audit_id) : "") + "</span>"
          + esc(r.chosen_label || "") + " · considered " + esc((r.considered || []).length) + " · asked " + esc((r.asked || []).length) + " · evidence " + esc((r.evidence_refs || []).length)
          + (r.considered && r.considered.length ? "" : " — decided on the card face alone, and the record says so") + "</div>"
          + '<button class="psbtn" id="decnext" style="margin-top:6px">' + (q.length ? "Next decision" : "Done — the queue is clear") + "</button>";
        const nx = out.querySelector("#decnext");
        nx.addEventListener("click", () => { if (q.length) load(); else ctx.close(); });
        queueBadge.textContent = q.length ? ("decision 1 of " + q.length) : "none waiting";
      });
    };

    // Exposed on the host, not on window (§3.5), so the two shell-side entry
    // points (queueDecisionSurface, applyActionLink) can refresh an
    // already-mounted panel without duplicating this render logic — the same
    // pattern contract.js uses for host._contractSetTab.
    host._decisionRefresh = load;
    await load();
  },
});
