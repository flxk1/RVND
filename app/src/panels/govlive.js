// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Live Governance drawer — a read-only projection of the folder's live
// sessions, run-lease serialization, per-agent verdicts and the one signed
// chain (workspace_workflow op "governance_live", honest contract v2: every
// field maps to a real read-only source — sessions derived by replay of
// GovernanceSessionOpened, verdicts from lane_capabilities, leases from the
// run queue, the chain from mutation_log.replay). No write controls, and no
// invented state: kind / autonomy decay / per-agent breaker have no honest
// source yet (spec §1 deferred-(B)), so this board does not draw them; any
// field the op omits stays unrendered rather than faked.
Patchbay.register("govlive", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc, escA } = ui;
    // Semantic verdict colors for the lane_capabilities vocabulary —
    // deliberately not the cyan system accent, so a verdict can never be
    // mistaken for chrome. System/boundary/chain = cyan.
    const VC = { auto: "#4fbe8b", human: "#e0a852", reserved: "#e2554a", refused: "#c8446e", prohibited: "#c8446e", unfired: "var(--txt-dim)" };
    const SYS = "#3ec8d8";
    const MONO = "font-family:IBM Plex Mono,monospace;font-size:10px";

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML = "Every live session against the same protections — admission, per-agent lane verdict, run-lease serialization, one signed chain. This board only reads them.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "govlive";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const pill = (v) => '<span class="gl-verdict" data-verdict="' + escA(v) + '" style="border:1px solid ' + (VC[v] || "var(--line)") + ';color:' + (VC[v] || "var(--txt-dim)") + ';border-radius:6px;padding:1px 7px;font-size:10px;text-transform:uppercase;letter-spacing:.4px">' + esc(v) + "</span>";

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its live governance board</div>';
        return;
      }
      let b;
      try {
        b = await tool("workspace_workflow", { op: "governance_live", params: { folder_context: ctx.workspace.path } });
      } catch (e) {
        out.innerHTML = '<div class="finding warn"><span class="ttl">Board unavailable</span>' + esc((e && e.message) || "governance_live failed") + "</div>";
        return;
      }
      if (!b || b.ok === false) {
        out.innerHTML = '<div class="finding warn"><span class="ttl">Board unavailable</span>' + esc((b && b.error) || "governance_live returned no board") + "</div>";
        return;
      }
      // Connected-agents presence (server-level, read-only), via the SAME governed
      // tool() path. The board's sessions[] are admission-minted (governance_open)
      // only; PreToolUse-monitored agents never mint one, so they surface here as
      // presence — "connected · monitored", never a verdict. Optional/independent.
      let ags = [];
      try {
        const ar = await tool("workspace_workflow", { op: "connected_agents", params: {} });
        ags = (ar && ar.agents) || [];
      } catch (e) { /* presence optional */ }
      const sum = b.summary || {}, bound = b.boundary || {};
      const esca = sum.escalations != null ? sum.escalations : null;
      let h = "";

      // ── summary tiles: sessions_open / admitted / run_leases_held / escalations ──
      const tile = (label, val, warn) => '<div class="gl-tile"' + (warn ? ' data-warn="true"' : "") +
        ' style="flex:1;min-width:96px;background:var(--panel-2);border:1px solid ' + (warn ? VC.reserved : "var(--line)") + ';border-radius:8px;padding:7px 9px">' +
        '<div style="font-size:9.5px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px">' + label + "</div>" +
        '<div style="font-size:13px;margin-top:2px">' + val + "</div></div>";
      h += '<div class="gl-summary" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:9px">';
      h += tile("connected", esc(ags.length));
      h += tile("sessions open", esc(sum.sessions_open != null ? sum.sessions_open : "—"));
      h += tile("admitted", esc(sum.admitted != null ? sum.admitted : "—"));
      h += tile("run leases held", esc(sum.run_leases_held != null ? sum.run_leases_held : "—"));
      h += tile("escalations", esca != null ? (esca > 0 ? '<span style="color:' + VC.reserved + '">' + esc(esca) + "</span>" : esc(esca)) : "—", esca != null && esca > 0);
      const unauth = sum.unauthorised_effects != null ? sum.unauthorised_effects : null;
      h += tile("unauthorised", unauth != null ? (unauth > 0 ? '<span style="color:' + VC.reserved + '">' + esc(unauth) + "</span>" : esc(unauth)) : "—", unauth != null && unauth > 0);
      h += "</div>";

      // ── connected agents — SERVER-LEVEL presence (MCP handshake), NOT authority.
      // Answers "who is connected" (vs the sessions block's "who is admitted to
      // act"). Monitored agents live here; rendered "connected · monitored", never
      // a verdict pill — presence is not a grant. ──────────────────────────────
      h += '<div style="font-size:9.5px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">connected agents — live presence (' + ags.length + ') · monitored, not admitted</div>';
      h += '<div class="gl-presence" style="border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:9px">';
      ags.forEach((a) => {
        h += '<div class="gl-agent" data-connid="' + escA(a.connid || "") + '" data-pid="' + escA(a.pid != null ? a.pid : "") + '"' +
          (a.session_id ? ' data-session-id="' + escA(a.session_id) + '"' : "") +
          ' style="display:flex;gap:8px;align-items:center;padding:4px 9px;border-top:1px solid var(--line);font-size:10px">' +
          '<span style="color:' + SYS + '">● connected</span>' +
          '<span style="' + MONO + '">' + esc(String(a.connid || "").slice(0, 8)) + '</span>' +
          '<span style="flex:1;color:var(--txt-dim)">' + esc(a.agent || "agent") + ' · ' + esc(a.transport || "stdio") +
          (a.session_id ? ' · sid ' + esc(String(a.session_id).slice(0, 8)) : "") + "</span>" +
          (a.pid != null ? '<span style="' + MONO + ';color:var(--txt-dim)">pid ' + esc(a.pid) + "</span>" : "") + "</div>";
      });
      if (!ags.length) h += '<div style="padding:6px 9px;font-size:10px;color:var(--txt-dim)">no agents connected</div>';
      h += "</div>";

      // ── acting sessions — SIGNED CHAIN (workspace_workflow op "session_governance").
      // Unlike the presence block above (who is CONNECTED), these are the real acting
      // identities sourced from the signed chain: one row per actor, each with its own
      // REAL lane verdict, grade, event_count and last activity. A session whose verdict
      // is null/absent renders honest-neutral (no verdict pill), never a fabricated one.
      // Optional/independent — if the op is absent this section simply does not draw. ──
      let sgSessions = [];
      try {
        const sg = await tool("workspace_workflow", { op: "session_governance", params: { folder_context: ctx.workspace.path, chain_limit: 10 } });
        if (sg && sg.ok !== false) sgSessions = (sg.sessions) || [];
      } catch (e) { /* signed-chain sessions optional */ }
      if (sgSessions.length) {
        h += '<div style="font-size:9.5px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">acting sessions — signed chain (' + sgSessions.length + ') · real per-actor verdict</div>';
        h += '<div class="gl-sessions-chain" style="margin-bottom:9px">';
        sgSessions.forEach((s) => {
          // A real, non-null verdict is the honesty gate. verdict null/absent → honest-neutral.
          const hasV = s.verdict != null && String(s.verdict) !== "";
          const v = hasV ? String(s.verdict) : null;
          const r0 = (Array.isArray(s.recent) && s.recent.length) ? s.recent[0] : null;
          const lastAct = r0 ? (r0.action || r0.event || r0.kind || "activity") : null;
          h += '<div class="gl-chain-session" data-actor="' + escA(s.actor || "") + '" data-verdict="' + escA(v || "") + '"' +
            ' style="border:1px solid var(--line);border-left:3px solid ' + (v && VC[v] ? VC[v] : "var(--line)") +
            ';border-radius:8px;padding:8px 10px;margin-bottom:7px;background:var(--panel-2)">';
          h += '<div style="display:flex;align-items:center;gap:7px"><b style="' + MONO + ';font-size:11px">' + esc(s.actor || "?") + "</b>" +
            // ● LIVE = a live connection joined this actor by session_id (real presence); else chain-only.
            (s.connected
              ? '<span style="font-size:9.5px;color:' + SYS + '">● live</span>'
              : '<span style="font-size:9.5px;color:var(--txt-dim)">on chain</span>') +
            '<span style="flex:1"></span>' +
            (v ? pill(v) : '<span style="font-size:9.5px;color:var(--txt-dim)">monitored · no verdict</span>') + "</div>";
          const bits = [];
          if (s.connected && s.connid) bits.push('<span style="' + MONO + '">conn ' + esc(String(s.connid).slice(0, 8)) + "</span>");
          if (s.connected && s.pid != null) bits.push("pid " + esc(s.pid));
          if (s.grade != null && String(s.grade) !== "") bits.push("grade " + esc(String(s.grade)));
          if (s.event_count != null) bits.push(esc(s.event_count) + " event" + (s.event_count === 1 ? "" : "s"));
          if (lastAct) bits.push("last " + esc(lastAct));
          if (s.last_event_ts) bits.push(esc(String(s.last_event_ts).slice(0, 19)));
          if (bits.length) h += '<div style="margin-top:4px;font-size:10px;color:var(--txt-dim)">' + bits.join(" · ") + "</div>";
          if (s.escalation) h += '<div class="gl-chain-escalation" data-escalation="true" style="margin-top:4px;font-size:10px;color:' + VC.reserved + '">▲ escalation — a human is in this loop</div>';
          h += "</div>";
        });
        h += "</div>";
      }

      // ── complete-mediation reconciliation: the authorisation ledger (gate
      // verdicts) vs the effect ledger (observed step outcomes), see
      // reconciliation_binding. An effect with no authorisation behind it is the
      // loud number; this surfaces it read-only, warning red when non-zero. ──
      const rec = b.reconciliation || {};
      const unauthN = rec.observed_not_authorised != null ? rec.observed_not_authorised : 0;
      h += '<div class="gl-reconciliation" data-status="' + escA(rec.status || "") +
        '" data-unauthorised="' + escA(unauthN) +
        '" data-rate="' + escA(rec.unauthorised_rate != null ? rec.unauthorised_rate : 0) +
        '" style="border:1px solid ' + (unauthN > 0 ? VC.reserved : "var(--line)") +
        ';border-radius:8px;padding:6px 10px;margin-bottom:9px;font-size:10.5px;color:var(--txt-dim)">' +
        "complete-mediation — " + esc(rec.matched != null ? rec.matched : 0) + " matched · " +
        '<span style="color:' + (unauthN > 0 ? VC.reserved : "var(--txt-dim)") + '">' + esc(unauthN) +
        " unauthorised</span> · " + esc(rec.status || "—") + "</div>";

      // ── sessions (derived by replay; admission honesty enforced here too) ──
      h += '<div class="gl-sessions">';
      (b.sessions || []).forEach((s) => {
        // An un-admitted (expired/revoked) session must never be drawn acting:
        // whatever upstream said, it renders refused and loses the GO family.
        const admitted = s.admitted !== false;
        const v = admitted ? (s.verdict || "unfired") : "refused";
        h += '<div class="gl-session" data-sid="' + escA(s.sid || "") + '" data-admitted="' + (admitted ? "true" : "false") + '" data-verdict="' + escA(v) + '"' +
          ' style="border:1px solid var(--line);border-left:3px solid ' + (VC[v] && v !== "unfired" ? VC[v] : "var(--line)") + ';border-radius:8px;padding:8px 10px;margin-bottom:7px;background:var(--panel-2)">';
        h += '<div style="display:flex;align-items:center;gap:7px"><b style="' + MONO + ';font-size:11px">' + esc(s.sid || "?") + "</b>" +
          '<span style="font-size:9.5px;color:var(--txt-dim)">' + (admitted ? "admitted" : "not admitted") + "</span>" +
          '<span style="flex:1"></span>' + pill(v) + "</div>";
        if (admitted && s.capability && s.capability.folder_context) {
          h += '<div class="gl-cap" data-folder="' + escA(s.capability.folder_context) + '" style="margin-top:5px;font-size:10px;color:#92c4ac">✓ capability' +
            '<span style="color:var(--txt-dim)"> · ' + esc(s.capability.folder_context) +
            (s.capability.expires ? " · expires " + esc(String(s.capability.expires).slice(0, 19)) : "") + "</span></div>";
        } else if (!admitted) {
          h += '<div style="margin-top:5px;font-size:10px;color:' + VC.refused + '">expired or revoked — no live capability, nothing to act with</div>';
        }
        if (s.grade) h += '<div style="margin-top:4px;font-size:10px;color:var(--txt-dim)">grade ' + esc(s.grade) + "</div>";
        if (s.escalation != null) {
          h += '<div class="gl-escalation" data-escalation="' + (s.escalation ? "true" : "false") + '" style="margin-top:4px;font-size:10px;color:' + (s.escalation ? VC.reserved : "var(--txt-dim)") + '">' +
            (s.escalation ? "▲ escalation — a human is in this loop" : "no escalation") + "</div>";
        }
        h += "</div>";
      });
      h += "</div>";

      // ── run leases — serialization BY REFUSAL: at most one run in flight
      // per folder·workflow (a second enqueue is refused by the module), so
      // there is never a queue position to draw. One row = the holder. ──────
      h += '<div style="font-size:9.5px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">run leases — one in flight per folder · workflow (a second is refused)</div>';
      h += '<div class="gl-leases" style="border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:9px">';
      (b.leases || []).forEach((l) => {
        const fw = (l.folder || "") + "·" + (l.workflow || "");
        h += '<div class="gl-lease" data-folder-workflow="' + escA(fw) + '" data-holder="' + escA(l.holder || "") + '"' +
          ' style="display:flex;gap:8px;align-items:center;padding:4px 9px;border-top:1px solid var(--line);font-size:10px">' +
          '<span style="color:#92c4ac">holding</span>' +
          '<span style="' + MONO + '">' + esc(l.workflow || "") + '</span>' +
          '<span style="flex:1;color:var(--txt-dim)">' + esc(l.folder || "") + "</span>" +
          (l.holder ? '<span style="' + MONO + ';color:var(--txt-dim)">' + esc(l.holder) + "</span>" : "") +
          (l.ttl_s != null ? '<span style="color:var(--txt-dim)">ttl ' + esc(l.ttl_s) + "s</span>" : "") + "</div>";
      });
      if (!(b.leases || []).length) h += '<div style="padding:6px 9px;font-size:10px;color:var(--txt-dim)">no runs in flight</div>';
      h += "</div>";

      // ── boundary — static doctrine label, panel-rendered, NOT op data ──
      h += '<div class="gl-boundary" style="border:1px solid ' + SYS + '33;border-radius:8px;padding:6px 10px;margin:2px 0 9px;font-size:10.5px;color:' + SYS + '">boundary — releases GO only</div>';

      // ── the one chain (replay order; hash is a digest of already-public
      // audit data and appears as the next entry's prev_hash — exposed so the
      // render gate can verify the linkage in the DOM). Each node drills into
      // the step inspector (I4) — activation is a read, never a write. ──────
      h += '<div style="font-size:9.5px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">one signed chain — newest first · activate a step to inspect it</div>';
      h += '<div class="gl-chain" style="border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:9px">';
      (b.chain || []).forEach((n) => {
        h += '<div class="gl-node" role="button" tabindex="0" data-seq="' + escA(n.seq) + '" data-actor="' + escA(n.actor || "") + '" data-event="' + escA(n.event || "") + '" data-extra="' + escA(n.extra || "") + '"' +
          (n.hash ? ' data-hash="' + escA(n.hash) + '"' : "") + (n.prev_hash ? ' data-prev="' + escA(n.prev_hash) + '"' : "") +
          ' aria-label="inspect step ' + escA(n.seq) + ' — ' + escA((n.actor || "") + " " + (n.event || "")) + '"' +
          ' style="display:flex;gap:8px;align-items:center;padding:4px 9px;border-top:1px solid var(--line);font-size:10px;cursor:pointer">' +
          '<span style="' + MONO + ';color:' + SYS + '">#' + esc(n.seq) + "</span>" +
          '<span style="' + MONO + '">' + esc(n.actor || "") + "</span>" +
          '<span style="color:var(--txt)">' + esc(n.event || "") + "</span>" +
          '<span style="flex:1;color:var(--txt-dim)">' + esc(n.extra || "") + "</span>" +
          (n.hash ? '<span style="' + MONO + ';color:var(--txt-dim)">' + esc(String(n.hash).slice(0, 8)) + " ← " + esc(String(n.prev_hash || "").slice(0, 8)) + "</span>" : "") + "</div>";
      });
      if (!(b.chain || []).length) h += '<div style="padding:6px 9px;font-size:10px;color:var(--txt-dim)">no entries</div>';
      h += "</div>";

      h += '<div class="gl-inspector-slot"></div>';
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:8px">Read-only. Admission, lanes and leases are the server’s protections — this board can only show them. Fields with no honest source (kind, decay, per-agent breaker) are not drawn.</div>';
      out.innerHTML = h;
      const chainEl = out.querySelector(".gl-chain");
      if (chainEl) {
        const drill = (nd) => inspect(nd, out.querySelector(".gl-inspector-slot"));
        chainEl.addEventListener("click", (ev) => { const nd = ev.target.closest(".gl-node"); if (nd) drill(nd.dataset); });
        chainEl.addEventListener("keydown", (ev) => {
          if (ev.key !== "Enter" && ev.key !== " ") return;
          const nd = ev.target.closest(".gl-node"); if (!nd) return;
          ev.preventDefault(); drill(nd.dataset);
        });
      }
    };

    // ── Step inspector (I4) — read-only drill-down over data that already
    // exists: the node's own signed-chain fields, the live verify_chain
    // status, the actor's live lane verdict (strictest-wins over the raw
    // lane_capabilities cells — the op's own derivation), and the approval /
    // decision context. Disclosure principle: every section renders exactly
    // what the governed read discloses — a refused read renders the server's
    // words, and the inspector never adds a label (e.g. "sealed") the server
    // did not state. Inspection, not action: no write controls. ─────────────
    const RANK = { prohibited: 5, refused: 4, reserved: 3, human: 2, auto: 1, unfired: 0 };
    const collectVerdicts = (x, out2) => {
      if (Array.isArray(x)) x.forEach((v) => collectVerdicts(v, out2));
      else if (x && typeof x === "object") {
        for (const k of Object.keys(x)) {
          if (k === "verdict" && typeof x[k] === "string" && x[k] in RANK) out2.push(x[k]);
          else collectVerdicts(x[k], out2);
        }
      }
      return out2;
    };
    const sect = (title, body) => '<div style="border-top:1px solid var(--line);padding:6px 0"><div style="font-size:9px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px">' + title + "</div>" + body + "</div>";
    const said = (e) => esc((e && e.message) || String(e));

    async function inspect(d, slot) {
      if (!slot) return;
      let h = '<div class="gl-inspector" data-seq="' + escA(d.seq) + '" style="border:1px solid ' + SYS + '55;border-radius:8px;padding:8px 10px;margin-bottom:9px;background:var(--panel-2)">';
      h += '<div style="display:flex;align-items:center;gap:7px"><b style="' + MONO + ';font-size:11px;color:' + SYS + '">step #' + esc(d.seq) + "</b>" +
        '<span style="' + MONO + '">' + esc(d.actor || "") + '</span><span>' + esc(d.event || "") + '</span><span style="flex:1"></span>' +
        '<span class="gl-inspector-x" role="button" tabindex="0" aria-label="Close inspector" style="cursor:pointer;color:var(--txt-dim)">✕</span></div>';
      if (d.extra) h += '<div style="font-size:10px;color:var(--txt-dim);margin-top:3px">' + esc(d.extra) + "</div>";
      h += sect("hash linkage", '<span style="' + MONO + ';word-break:break-all">' + esc(d.hash || "(none)") + '</span><br><span style="' + MONO + ';color:var(--txt-dim);word-break:break-all">← prev ' + esc(d.prev || "(none)") + "</span>");
      slot.innerHTML = h + '<div class="gl-inspector-live" style="font-size:10px;color:var(--txt-dim)">reading the record…</div></div>';
      const x = slot.querySelector(".gl-inspector-x");
      const closeIt = () => { slot.innerHTML = ""; };
      x.addEventListener("click", closeIt);
      x.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); closeIt(); } });

      let live = "";
      let rec = null;
      // The step's FULL signed record, from the audit surface. Ordering is
      // load-bearing: the AUDIT SURFACE RECORDS READS (verify_chain appends a
      // verify_chain_read event — the audit auditing its own reading), so the
      // log-size anchor and the tail must be fetched BEFORE any appending
      // read. The board is the anchor (board reads append nothing): its head
      // seq gives the log size the tail window is measured against. seq is
      // the absolute replay index; row = seq − (total − N). The record row
      // must agree with the board's node — on any misalignment the inspector
      // says WHAT misaligned and binds nothing (never a guessed binding).
      try {
        const bh = await tool("workspace_workflow", { op: "governance_live", params: { folder_context: ctx.workspace.path, chain_limit: 1 } });
        const total = bh && bh.chain && bh.chain.length ? Number(bh.chain[0].seq) + 1 : null;
        const t = await tool("workspace_audit", { op: "tail", params: { folder_context: ctx.workspace.path, limit: 40 } });
        const evs = (t && t.events) || [];
        if (total != null && evs.length) {
          const idx = Number(d.seq) - (total - evs.length);
          const cand = idx >= 0 && idx < evs.length ? evs[idx] : null;
          if (cand && cand.actor === (d.actor || "")) {
            rec = cand;
            live += sect("the step's signed record",
              '<div class="gl-inspector-record" data-pair="' + escA(rec.pair_id || "") + '" style="font-size:10px;' + (rec.signed ? "" : "color:" + VC.refused) + '">' +
              (rec.signed ? "✓ signed" : "⚠ UNSIGNED") + " · " + esc(rec.kind || rec.event || "") +
              (rec.pair_id ? " · pair " + esc(rec.pair_id) : "") +
              (rec.verdict ? " · verdict " + esc(rec.verdict) : "") +
              (rec.channel ? ' · <span style="color:var(--txt-dim)">' + esc(rec.channel) + "</span>" : "") +
              (rec.ts ? ' · <span style="color:var(--txt-dim)">' + esc(String(rec.ts).slice(0, 19)) + "</span>" : "") +
              (rec.audit_id ? '<br><span style="' + MONO + ';color:var(--txt-dim)">audit ' + esc(rec.audit_id) + "</span>" : "") + "</div>");
          } else if (cand) {
            live += sect("the step's signed record", '<span style="color:var(--txt-dim)">record row does not align with the board’s replay index (row ' + esc(idx) + " of " + esc(evs.length) + " is " + esc(cand.actor || "?") + "/" + esc(cand.kind || cand.event || "?") + ", the step is " + esc(d.actor || "?") + ") — showing nothing rather than a guess</span>");
          } else {
            live += sect("the step's signed record", '<span style="color:var(--txt-dim)">outside the readable tail window (' + esc(evs.length) + " of " + esc(total) + " events)</span>");
          }
        }
      } catch (e) { live += sect("the step's signed record", '<span style="color:var(--txt-dim)">not readable here — ' + said(e) + "</span>"); }
      // Live chain verification — the record's own tamper check, run now.
      // (An appending read: it lands AFTER the tail window above, by design.)
      try {
        const vc = await tool("workspace_audit", { op: "verify_chain", params: { folder_context: ctx.workspace.path } });
        live += sect("record verification (live)", vc && vc.ok
          ? '<span class="gl-verify" data-ok="true" style="color:#4fbe8b">✓ intact — ' + esc(vc.total_events || 0) + " signed events, no broken links</span>"
          : '<span class="gl-verify" data-ok="false" style="color:' + VC.refused + '">✗ verification failed — broken links ' + esc(((vc || {}).broken_links || []).length) + ", signature failures " + esc(((vc || {}).signature_failures || []).length) + "</span>");
      } catch (e) { live += sect("record verification (live)", '<span class="gl-verify" data-ok="unavailable" style="color:var(--txt-dim)">not readable here — ' + said(e) + "</span>"); }
      // The actor's live lane verdict + why (strictest-wins, the op's rule).
      try {
        const lc = await tool("workspace_workflow", { op: "lane_capabilities", params: { folder_context: ctx.workspace.path, actor: d.actor || "" } });
        const vs = collectVerdicts(lc, []);
        if (vs.length) {
          const strict = vs.reduce((a2, b2) => (RANK[a2] >= RANK[b2] ? a2 : b2));
          live += sect("actor's live lane verdict", '<span class="gl-inspector-verdict" data-verdict="' + escA(strict) + '" style="color:' + (VC[strict] || "var(--txt-dim)") + '">' + esc(strict) + "</span>" +
            '<span style="color:var(--txt-dim)"> — strictest of ' + esc(vs.length) + " boundary cell(s); the tightest constraint the agent faces</span>");
        } else live += sect("actor's live lane verdict", '<span style="color:var(--txt-dim)">no lane cells surfaced for this actor</span>');
      } catch (e) { live += sect("actor's live lane verdict", '<span style="color:var(--txt-dim)">not readable here — ' + said(e) + "</span>"); }
      // Reservation / approval context. Bound = the item's request id equals
      // the signed record's OWN pair reference (pair_id "approval:<id>") —
      // binding comes from the record, never from text-matching a summary;
      // anything else renders honestly as folder-scope.
      try {
        const al = await tool("workspace_workflow", { op: "approval_list", params: { folder_context: ctx.workspace.path, now: Math.floor(Date.now() / 1000) } });
        if (al && al.error) throw new Error(al.error);
        const items = (al && (al.approvals || al.items || al.rows)) || [];
        const pairRef = rec && typeof rec.pair_id === "string" && rec.pair_id.indexOf("approval:") === 0
          ? rec.pair_id.slice("approval:".length) : null;
        const row = (a) => {
          const needed = a.needed || a.quorum || 0, got = ((a.approvers) || []).length;
          return '<div class="gl-inspector-approval" data-request="' + escA(a.request_id || "") + '" data-quorum="' + escA(needed) + '" style="font-size:10px;margin-top:2px">' +
            '<b>' + esc(a.request_id || "approval") + "</b> · signed " + esc(got) + " of " + esc(needed) +
            (((a.competences) || []).length ? ' · any of {' + esc(a.competences.join(", ")) + "}" : "") +
            (a.requester ? ' <span style="color:var(--txt-dim)">· requested by ' + esc(a.requester) + "</span>" : "") + "</div>";
        };
        const bound = items.filter((a) => pairRef && a.request_id === pairRef);
        const rest = items.filter((a) => !bound.includes(a));
        if (bound.length) live += sect("this step's reservation — routed approval", bound.map(row).join(""));
        if (rest.length) live += sect(bound.length ? "other open approvals in this folder" : "open approvals in this folder (this step names none)", rest.slice(0, 3).map(row).join(""));
        if (!items.length) live += sect("reservation / approvals", '<span style="color:var(--txt-dim)">none open in this folder</span>');
      } catch (e) { live += sect("reservation / approvals", '<span style="color:var(--txt-dim)">not readable here — ' + said(e) + "</span>"); }
      live += '<div style="font-size:9.5px;color:var(--txt-dim);border-top:1px solid var(--line);padding-top:5px">Inspection, not action — acting on a step goes through the governed surfaces, never this monitor.</div>';
      const liveEl = slot.querySelector(".gl-inspector-live");
      if (liveEl) liveEl.innerHTML = live;
    }

    await load();
  },
});
