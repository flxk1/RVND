// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Egress board panel — "which tracks can act outside?" the sixth pack entry
// behind docs/loomground-proposals/panel-mount-contract.md. Read-only: it
// lists every egress track (the connectors that cross the boundary) with its
// floor, declared destination and cable (credential) state, all resolved
// server-side, fail-closed. Honesty first: every state is glyph + word +
// colour, never colour alone. The one doctrine-sensitive distinction this
// board draws is board-level LLM-egress mode — <b>attested</b> (RVND
// witnesses model calls but cannot cut them; no broker holds a track's plug
// today) versus <b>enforced</b> (a bound broker gates every call on a
// declared llm destination) — both worded from the live server-side broker
// probe the same read returns, never a client-side guess. Arming/disarming a
// cable and declaring a track's destination are governed steps on the
// connector itself, not clicks here; this bundle calls only
// workspace_workflow's egress_board read op.
Patchbay.register("egress", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc, escA } = ui;

    // Local copies of the shell's floor/arm-state lookups (index.html keeps
    // its own — fillTrackStrip's inspector rendering uses the same tables and
    // is shell chrome, not this panel's).
    const FLOOR = { permit: { col: "#5aa886", word: "permit" }, hold: { col: "#c8a23f", word: "hold" }, deny: { col: "#cf463c", word: "deny" } };
    const ARM = {
      armed: { col: "#c8a23f", glyph: "●", word: "armed" },
      no_cable: { col: "#5a616f", glyph: "○", word: "no cable — cannot reach outside" },
      unplugged: { col: "#e2554a", glyph: "◍", word: "unplugged — revoked / unresolvable" },
    };

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "One row per <b>egress track</b> — the connectors that cross the boundary. " +
      "The cable is the track’s credential <b>reference</b> (the secret is never " +
      "stored or shown); its arm state is resolved on the server, fail-closed. " +
      "<b>◌ attested</b> means RVND witnesses this track’s calls but cannot cut " +
      "them — no broker holds its plug. <b>⛓ enforced</b> appears where the " +
      "track’s declared destination (llm) has a bound broker gating every call. " +
      "Arming/disarming and the declared destination are governed steps on the " +
      "connector, not clicks here.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "egout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its egress board</div>';
        return true;
      }
      let b;
      try {
        b = await tool("workspace_workflow", { op: "egress_board", params: { folder_context: ctx.workspace.path } });
      } catch (e) {
        const banner = '<div class="finding bad" style="margin-bottom:6px"><span class="ttl">Could not load the egress board</span>' + esc((e && e.message) || "failed") + "</div>";
        if (out.querySelector(".egtable")) out.insertAdjacentHTML("afterbegin", banner); else out.innerHTML = banner;
        return false;
      }
      const tracks = (b && b.tracks) || [], sum = (b && b.summary) || {};
      if (!tracks.length) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">no egress tracks — nothing here can reach outside. Add an Output (Set up → Output) to create one.</div>';
        return true;
      }
      // the headline the board exists for — spoken as words, not just a number
      let h = '<div class="ro" role="status" style="font-size:12px;margin-bottom:8px"><b>' + (sum.can_act_outside || 0) + "</b> of <b>" + (sum.tracks || tracks.length) + "</b> egress track" + ((sum.tracks || tracks.length) === 1 ? "" : "s") + " can act outside right now"
        + (sum.unplugged ? (' · <span style="color:#e2554a">' + sum.unplugged + " unplugged</span>") : "")
        + (sum.no_cable ? (" · " + sum.no_cable + " with no cable") : "") + "</div>";
      // board-level LLM-egress state: the one destination class a bound broker can
      // enforce. Glyph + word + colour (never colour alone); bound_here is the live
      // probe, absent/unreachable stays attested.
      const lb = (b && b.llm_broker) || { bound_here: false, reachable: false };
      h += '<div class="ro" role="status" style="font-size:11px;margin-bottom:8px" aria-label="LLM egress enforcement">'
        + (lb.bound_here
          ? '<span style="color:#92c4ac" title="a broker is bound to this folder and gates every LLM call, credentialed per track">⛓ <b>LLM egress enforced</b> — a broker holds the plug; every model call is gated</span>'
          : '<span style="color:#e6b483" title="no broker holds the LLM plug; RVND witnesses model calls but cannot cut them">◌ <b>LLM egress attested</b> — RVND witnesses model calls but cannot cut them' + (lb.reachable ? " (a proxy is reachable but not broker-bound here)" : "") + "</span>")
        + "</div>";
      h += '<table class="egtable" role="grid" aria-label="egress tracks — floor, mode and cable state" style="width:100%;border-collapse:collapse;font-size:11px">'
        + "<thead><tr>" + ["track", "channel", "destination", "floor", "mode", "cable"].map((c) => '<th scope="col" style="text-align:left;padding:4px 6px;color:var(--txt-dim);font-weight:600">' + c + "</th>").join("") + "</tr></thead><tbody>";
      for (const t of tracks) {
        const fl = FLOOR[t.floor] || FLOOR.permit;
        const arm = ARM[(t.credential || {}).status] || ARM.no_cable;
        const ref = (t.credential || {}).credential_ref;
        h += '<tr style="border-top:1px solid var(--line)">'
          + '<td style="padding:5px 6px"><b>' + esc(t.name || t.connector_id) + "</b></td>"
          + '<td style="padding:5px 6px;color:var(--txt-dim)">' + esc(t.channel || "") + "</td>"
          + '<td style="padding:5px 6px"><span aria-label="floor: ' + escA(fl.word) + '"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' + fl.col + ';margin-right:5px;vertical-align:middle"></span>' + esc(fl.word) + "</span></td>"
          + '<td style="padding:5px 6px;color:' + (t.destination_class === "undeclared" ? "var(--txt-dim)" : "var(--txt)") + '" title="declared destination class — set at registration, never guessed from the channel">' + esc(t.destination_class || "undeclared") + "</td>"
          + '<td style="padding:5px 6px" title="' + (t.mode === "enforced" ? "this track declares the llm destination and a bound broker gates every model call" : "RVND witnesses this track’s calls but cannot cut them — no broker holds its plug") + '">' + (t.mode === "enforced" ? "⛓ enforced" : "◌ attested") + "</td>"
          + '<td style="padding:5px 6px"><span aria-label="cable: ' + escA(arm.word + (ref ? (" (" + ref + ")") : "")) + '" style="color:' + arm.col + '">' + arm.glyph + " " + esc(arm.word) + (ref ? (' <span style="color:var(--txt-dim)">· ' + esc(ref) + "</span>") : "") + "</span></td>"
          + "</tr>";
      }
      h += "</tbody></table>";
      out.innerHTML = h;
      return true;
    };

    await load();
  },
});
