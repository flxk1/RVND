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
      const sum = b.summary || {}, bound = b.boundary || {};
      const esca = sum.escalations != null ? sum.escalations : null;
      let h = "";

      // ── summary tiles: sessions_open / admitted / run_leases_held / escalations ──
      const tile = (label, val, warn) => '<div class="gl-tile"' + (warn ? ' data-warn="true"' : "") +
        ' style="flex:1;min-width:96px;background:var(--panel-2);border:1px solid ' + (warn ? VC.reserved : "var(--line)") + ';border-radius:8px;padding:7px 9px">' +
        '<div style="font-size:9.5px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px">' + label + "</div>" +
        '<div style="font-size:13px;margin-top:2px">' + val + "</div></div>";
      h += '<div class="gl-summary" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:9px">';
      h += tile("sessions open", esc(sum.sessions_open != null ? sum.sessions_open : "—"));
      h += tile("admitted", esc(sum.admitted != null ? sum.admitted : "—"));
      h += tile("run leases held", esc(sum.run_leases_held != null ? sum.run_leases_held : "—"));
      h += tile("escalations", esca != null ? (esca > 0 ? '<span style="color:' + VC.reserved + '">' + esc(esca) + "</span>" : esc(esca)) : "—", esca != null && esca > 0);
      h += "</div>";

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
      // render gate can verify the linkage in the DOM) ──────────────────────
      h += '<div style="font-size:9.5px;color:var(--txt-dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">one signed chain — newest first</div>';
      h += '<div class="gl-chain" style="border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:9px">';
      (b.chain || []).forEach((n) => {
        h += '<div class="gl-node" data-seq="' + escA(n.seq) + '" data-actor="' + escA(n.actor || "") + '" data-event="' + escA(n.event || "") + '"' +
          (n.hash ? ' data-hash="' + escA(n.hash) + '"' : "") + (n.prev_hash ? ' data-prev="' + escA(n.prev_hash) + '"' : "") +
          ' style="display:flex;gap:8px;align-items:center;padding:4px 9px;border-top:1px solid var(--line);font-size:10px">' +
          '<span style="' + MONO + ';color:' + SYS + '">#' + esc(n.seq) + "</span>" +
          '<span style="' + MONO + '">' + esc(n.actor || "") + "</span>" +
          '<span style="color:var(--txt)">' + esc(n.event || "") + "</span>" +
          '<span style="flex:1;color:var(--txt-dim)">' + esc(n.extra || "") + "</span>" +
          (n.hash ? '<span style="' + MONO + ';color:var(--txt-dim)">' + esc(String(n.hash).slice(0, 8)) + " ← " + esc(String(n.prev_hash || "").slice(0, 8)) + "</span>" : "") + "</div>";
      });
      if (!(b.chain || []).length) h += '<div style="padding:6px 9px;font-size:10px;color:var(--txt-dim)">no entries</div>';
      h += "</div>";

      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:8px">Read-only. Admission, lanes and leases are the server’s protections — this board can only show them. Fields with no honest source (kind, decay, per-agent breaker) are not drawn.</div>';
      out.innerHTML = h;
    };

    await load();
  },
});
