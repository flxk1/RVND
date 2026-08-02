// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Connected tools (federation) panel — ported from the inline
// openFederationPanel/loadFederation/fedCompose/fedGroupFloor/fedRevoke/
// fedRevokeGroup functions behind
// docs/loomground-proposals/panel-mount-contract.md. Third-party governance
// tools are CHANNELS under their client GROUP-bus; the server joins every
// channel's verdict with the workspace's own local verdict strictest-wins
// into one federated decision per use case, surfacing the spread as a
// disagreement badge rather than hiding it. Kill switches (mute a channel,
// mute a whole client) and the per-GROUP floor control are governed,
// recorded writes; every other element here is a read projection of the
// server's join.
//
// A second surface — the Check panel's inline federated-verdict row
// (federatedCheck() in app/src/index.html, gated by
// app/panels/federated_verdict_render_test.py) reads the same
// federated_decision op but renders into the canvas's findings box, not
// this drawer. It is a different surface and stays inline, untouched by
// this migration.
Patchbay.register("federation", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc, escA } = ui;

    const FEDTRI = { permit: "#5aa886", hold: "#df8b46", deny: "#a8332b" };
    const fedChip = (v) => {
      const c = FEDTRI[v] || "#5a616f";
      return '<span style="font-size:10px;border:1px solid ' + c + ";color:" + c + ';border-radius:8px;padding:1px 7px">' + esc(v || "permit") + "</span>";
    };

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Each external tool is a <b>channel</b>; an MCP client sending many is a <b>group bus</b>. Floors and verdicts join <b>strictest-wins</b> at the master — a lone deny denies. Rvnd records + joins; the host calls the tool. <b>Mute</b> a channel or a whole client; the kill is recorded.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "fdout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    // One federated decision, decomposed: who said what, which of the three
    // inputs (tool verdict, channel floor, group floor) binds each
    // contribution, where the join's strictest came from, and every muted
    // channel with its last state — never silently dropped. Pure render
    // over the join the server returned.
    const fedCompose = (d) => {
      const sev = { permit: 0, hold: 1, deny: 2 };
      const mono = "font-family:IBM Plex Mono,monospace";
      const srcs = [...(d.sources || [])].sort((a, b) => (sev[b.verdict] || 0) - (sev[a.verdict] || 0) || String(a.connector_id).localeCompare(String(b.connector_id)));
      const bindOf = (s) => {
        const hit = [["tool said", s.tool_verdict], ["channel floor", s.floor], ["group floor", s.group_floor]].find((p) => p[1] === s.verdict);
        return hit ? hit[0] : "the join";
      };
      const winners = srcs.filter((s) => s.verdict === d.decision);
      let sent;
      if (d.decision === "permit") sent = "Joins at <b>permit</b> — every input agrees; no source dominates.";
      else if (winners.length && d.local === d.decision)
        sent = "The local verdict and <b>" + esc(winners[0].connector_id) + "</b>" + (winners.length > 1 ? " (+" + (winners.length - 1) + " more)" : "") + " agree at <b>" + esc(d.decision) + "</b>.";
      else if (winners.length)
        sent = "Strictest: <b>" + esc(winners[0].connector_id) + "</b> — its " + esc(bindOf(winners[0])) + " set <b>" + esc(d.decision) + "</b>" + (winners.length > 1 ? " (" + (winners.length - 1) + " more at " + esc(d.decision) + ")" : "") + ".";
      else sent = "Decision <b>" + esc(d.decision) + "</b> from the local verdict alone.";
      let h = '<div style="font-size:11px;margin:7px 0 3px;border-top:1px solid #2a2f39;padding-top:6px">' + sent + "</div>";
      if (d.disagreement) h += '<div class="finding warn" style="margin:4px 0"><span class="ttl">Sources disagree</span>The inputs span more than one verdict; the strictest wins and the spread stays on the record — agreement is not assumed.</div>';
      h += '<div style="font-size:9.5px;color:var(--txt-dim)">strictest first</div>';
      h += srcs
        .map(
          (s) =>
            '<div style="display:flex;gap:8px;align-items:baseline;border-top:1px solid #2a2f39;padding:4px 0;font-size:11px">' +
            '<span style="' + mono + ';font-size:10px">' + esc(s.connector_id) + (s.group ? '<span style="color:var(--txt-dim)">@' + esc(s.group) + "</span>" : "") + "</span>" +
            "<span>contributes " + fedChip(s.verdict) + "</span>" +
            '<span style="font-size:10px;color:var(--txt-dim);flex:1">' + (s.tool_verdict ? "tool said " + esc(s.tool_verdict) : "no verdict — the floor holds the line") + " · channel floor " + esc(s.floor) + " · group floor " + esc(s.group_floor) + " — binding: " + esc(bindOf(s)) + "</span>" +
            (s.input_digest ? '<span style="' + mono + ';font-size:9px;color:var(--txt-dim)" title="digest of the input the tool judged — matches the record, carries no content">' + esc(String(s.input_digest).slice(0, 10)) + "…</span>" : "") +
            "</div>"
        )
        .join("");
      h += '<div style="display:flex;gap:8px;align-items:baseline;border-top:1px solid #2a2f39;padding:4px 0;font-size:11px"><span style="' + mono + ';font-size:10px">local</span><span>contributes ' + fedChip(d.local) + '</span><span style="font-size:10px;color:var(--txt-dim)">the workspace\'s own gate</span></div>';
      const rvs = d.revoked_sources || [];
      if (rvs.length)
        h +=
          '<div style="font-size:11px;margin-top:7px"><b style="font-family:Space Grotesk,sans-serif;font-size:11px">Muted channels</b> <span style="font-size:9.5px;color:var(--txt-dim)">killed, last state shown — never silently dropped</span>' +
          rvs.map((s) => '<div style="text-decoration:line-through;opacity:.7;font-size:10.5px;padding:2px 0"><span style="' + mono + '">' + esc(s.connector_id) + (s.group ? "@" + esc(s.group) : "") + '</span> — ' + (s.verdict ? "last said " + esc(s.verdict) : "no verdict recorded") + "</div>").join("") +
          "</div>";
      return h;
    };

    // Tightening cycles permit→hold→deny; the loosest step (deny→permit)
    // asks first, fail-safe (loosening governance is the deliberate
    // direction). Recorded on the chain.
    const fedGroupFloor = async (g, cur) => {
      const order = ["permit", "hold", "deny"];
      const next = order[(order.indexOf(cur) + 1) % order.length];
      if (cur === "deny" && !window.confirm('Loosen client “' + g + '” from deny back to permit? This widens what the whole group may do (recorded).')) return;
      try {
        await tool("workspace_workflow", { op: "group_floor", params: { folder_context: ctx.workspace.path, group_id: g, floor: next, actor: "app-user" } });
      } catch (e) {
        alert("Could not set group floor: " + ((e && e.message) || "failed"));
        return;
      }
      load();
    };

    const fedRevoke = async (cid) => {
      if (!window.confirm('Mute channel “' + cid + '” — drop its verdicts from every future join? (recorded)')) return;
      try {
        await tool("workspace_workflow", { op: "tool_revoke", params: { folder_context: ctx.workspace.path, connector_id: cid, actor: "app-user" } });
      } catch (e) {
        alert("Could not mute: " + ((e && e.message) || "failed"));
        return;
      }
      load();
    };

    const fedRevokeGroup = async (g) => {
      if (!window.confirm('Mute client “' + g + '” — drop EVERY channel in the group from every future join? (recorded)')) return;
      try {
        await tool("workspace_workflow", { op: "group_revoke", params: { folder_context: ctx.workspace.path, group_id: g, actor: "app-user" } });
      } catch (e) {
        alert("Could not mute client: " + ((e && e.message) || "failed"));
        return;
      }
      load();
    };

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a workspace to see its connected tools</div>';
        return;
      }
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
      let conns = [];
      try {
        const r = await tool("workspace_workflow", { op: "connector_list", params: { folder_context: ctx.workspace.path } });
        conns = (r && r.connectors) || [];
      } catch (e) {
        out.innerHTML = '<div class="finding warn"><span class="ttl">Could not load connectors</span>' + esc((e && e.message) || "failed") + "</div>";
        return;
      }
      // the use cases any channel links → join a federated decision for each
      const ucs = [...new Set(conns.flatMap((c) => c.use_cases || []))].sort();
      const decisions = {};
      for (const u of ucs) {
        try {
          decisions[u] = await tool("workspace_workflow", { op: "federated_decision", params: { folder_context: ctx.workspace.path, use_case_id: u, local: "permit" } });
        } catch (_) {}
      }
      // group the channels by client group-bus ('' = ungrouped)
      const groups = {};
      conns.forEach((c) => {
        (groups[(c.group || "").trim()] = groups[(c.group || "").trim()] || []).push(c);
      });
      // The per-GROUP floor (the client group-bus's own floor) — the strictest
      // a whole client may ever be. Read from the join's per-source
      // group_floor; a group with no policy floor sits at permit. Discrete
      // chip + a click-to-tighten control.
      const groupFloors = {};
      ucs.forEach((u) => {
        const d = decisions[u];
        (d && d.sources || []).forEach((s) => {
          if (s.group) groupFloors[s.group] = s.group_floor || "permit";
        });
      });

      let h = "";
      if (!conns.length) h += '<div class="ro" style="color:var(--txt-dim);font-size:11px">no connected tools yet — register a connector (a tool channel) to begin</div>';
      Object.keys(groups)
        .sort()
        .forEach((g) => {
          const gv = g || "(ungrouped)";
          const gfl = groupFloors[g] || "permit";
          h +=
            '<div class="finding info" style="margin-bottom:8px"><span class="ttl">' + esc(gv) +
            (g ? '  <span style="font-size:10px;color:var(--txt-dim)">group bus</span>' : "") +
            (g ? ' <span class="grpfloor" data-grpfloor="' + escA(g) + '">group floor ' + fedChip(gfl) + '</span> <button class="tool" data-fedgf="' + escA(g) + '" title="tighten the whole client one notch (permit→hold→deny→permit); recorded" style="padding:1px 7px">set floor</button>' : "") +
            (g ? ' <button class="tool" style="padding:1px 7px;border-color:#d98b8b;color:#d98b8b" data-fedrg="' + escA(g) + '">mute client</button>' : "") +
            "</span>";
          groups[g].forEach((c) => {
            const cid = c.connector_id || "";
            const fl = c.floor || "permit";
            h +=
              '<div style="display:flex;gap:6px;align-items:center;margin:3px 0;font-size:11px"><span style="flex:1">' + esc(c.role || "") + " · " + esc(c.channel || "") + " <b>" + esc(cid) + "</b></span>" +
              " floor " + fedChip(fl) + (c.tags && c.tags.length ? ' <span style="color:#7fae97">tags: ' + esc(c.tags.join(",")) + "</span>" : "") +
              ' <button class="tool" style="padding:1px 7px;border-color:#d98b8b;color:#d98b8b" data-fedrev="' + escA(cid) + '">mute</button></div>';
          });
          h += "</div>";
        });
      if (ucs.length) {
        h += '<div style="font-size:11px;color:var(--txt-dim);margin:10px 0 4px">Joined verdicts (local + tools → strictest)</div>';
        ucs.forEach((u) => {
          const d = decisions[u];
          if (!d) return;
          const dis = d.disagreement ? ' <span style="color:#e6b483" title="sources disagree">⚠ disagreement</span>' : "";
          let src = (d.sources || []).map((s) => esc(s.connector_id) + (s.group ? "@" + esc(s.group) : "") + " " + fedChip(s.verdict)).join(" · ");
          const rv = (d.revoked_sources || []).length ? ' · <span style="color:var(--txt-dim)">muted: ' + d.revoked_sources.map((s) => esc(s.connector_id)).join(",") + "</span>" : "";
          h +=
            '<div class="finding" style="margin-bottom:6px"><span class="ttl">' + esc(u) + " → " + fedChip(d.decision) + dis +
            ' <span role="button" tabindex="0" data-fedcomp="' + escA(u) + '" style="cursor:pointer;font-size:10px;font-weight:400;color:#8fb9d6;margin-left:6px">composition ▸</span></span>' +
            '<div style="font-size:10px;color:var(--txt-dim)">local ' + esc(d.local) + (src ? " · " + src : "") + rv + "</div>" +
            '<div data-comp="' + escA(u) + '" style="display:none"></div></div>';
        });
      }
      out.innerHTML = h;
      out.querySelectorAll("[data-fedrev]").forEach((b) => b.addEventListener("click", () => fedRevoke(b.dataset.fedrev)));
      out.querySelectorAll("[data-fedrg]").forEach((b) => b.addEventListener("click", () => fedRevokeGroup(b.dataset.fedrg)));
      out.querySelectorAll("[data-fedgf]").forEach((b) => b.addEventListener("click", () => fedGroupFloor(b.dataset.fedgf, groupFloors[b.dataset.fedgf] || "permit")));
      out.querySelectorAll("[data-fedcomp]").forEach((b) => {
        const openComp = () => {
          const u = b.dataset.fedcomp;
          const box = out.querySelector('[data-comp="' + u + '"]');
          if (!box) return;
          if (box.style.display !== "none") {
            box.style.display = "none";
            b.textContent = "composition ▸";
            return;
          }
          if (!box.innerHTML) box.innerHTML = fedCompose(decisions[u] || {});
          box.style.display = "block";
          b.textContent = "composition ▾";
        };
        b.addEventListener("click", openComp);
        b.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            openComp();
          }
        });
      });
    };

    await load();
  },
});
