// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Roles & competence panel — the fifth pack entry behind
// docs/loomground-proposals/panel-mount-contract.md. Lists who holds which
// competence, in which role, reachable on which channels: competence drives
// approver routing and the task x role lens; the rules speak role and
// competence, never a named identity. Listing (workspace_policy/party_list)
// is a read; registering a party (party_register) is the one governed write
// this drawer exposes, so the manifest declares a custom "reads · registers"
// badge rather than the plain read-only one. Suspending or killing a party
// stays on the party itself (rail + register), not here.
Patchbay.register("roles", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc, escA } = ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Who holds which <b>competence</b>, in which role. Competence is what routing " +
      "matches — a reserved act reaches the people who hold its competence (Task × role " +
      "shows the gaps). The rules speak role and competence, never a named identity.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "rlout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const STATUS = {
      active: { col: "#5f9088", word: "active" },
      suspended: { col: "#c8a23f", word: "suspended" },
      killed: { col: "#cf463c", word: "killed" },
    };

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its roles &amp; competence</div>';
        return true;
      }
      let r;
      try {
        r = await tool("workspace_policy", { op: "party_list", params: { folder_context: ctx.workspace.path } });
      } catch (e) {
        out.innerHTML = '<div class="finding bad"><span class="ttl">Could not load roles &amp; competence</span>' + esc((e && e.message) || "failed") + "</div>";
        return false;
      }
      const rows = Array.isArray(r) ? r : ((r && (r.parties || r.rows)) || []);
      let h = "";
      for (const [kind, title, hint] of [
        ["human", "People", "sign-offs route to them by competence"],
        ["agent", "Agents", "they run tasks; authority lives on the task"],
      ]) {
        const group = rows.filter((p) => (p.party_kind || p.kind) === kind);
        h += '<div style="margin:8px 0 2px;font-family:Space Grotesk,sans-serif;font-size:12px;color:#f3f1ea">' + title + ' <span style="font-size:10px;color:var(--txt-dim)">' + esc(hint) + "</span></div>";
        if (!group.length) { h += '<div class="ro" style="font-size:11px;color:var(--txt-dim)">none yet</div>'; continue; }
        for (const p of group) {
          const st = STATUS[p.status || "active"] || STATUS.active;
          const comps = (p.competences || [])
            .map((c) => '<span style="display:inline-block;border:1px solid var(--line);border-radius:8px;padding:1px 7px;font-size:10px;margin:1px 2px">' + esc(c) + "</span>")
            .join("") || '<span style="font-size:10px;color:var(--txt-dim)">no competence — routing cannot reach ' + (kind === "human" ? "them" : "it") + "</span>";
          h += '<div style="display:flex;align-items:center;gap:8px;border-top:1px solid var(--line);padding:6px 0" data-party="' + escA(p.party_id) + '">'
            + '<span aria-label="status: ' + escA(st.word) + '" title="' + escA(st.word) + '" style="width:9px;height:9px;border-radius:50%;background:' + st.col + ';flex:none"></span>'
            + '<span style="font-size:12px;min-width:110px"><b>' + esc(p.name || p.party_id) + "</b>" + (p.role ? '<div style="font-size:10px;color:var(--txt-dim)">' + esc(p.role) + "</div>" : "") + "</span>"
            + '<span style="flex:1">' + comps + "</span>"
            + '<span style="font-size:10px;color:var(--txt-dim)" title="registered channels">' + ((p.channels || []).length) + " ch</span>"
            + "</div>";
        }
      }
      h += '<div style="border-top:1px solid var(--line);margin-top:10px;padding-top:8px">'
        + '<div style="font-size:11px;color:var(--txt-dim);margin-bottom:6px">Register a party — a governed write on the chain</div>'
        + '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">'
        + '<input id="rlid" placeholder="id (e.g. dana)" aria-label="party id" style="font-size:11px;background:var(--panel-2);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:4px 6px;width:110px">'
        + '<select id="rlkind" aria-label="kind" style="font-size:11px;background:var(--panel-2);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:4px 6px"><option value="human">human</option><option value="agent">agent</option></select>'
        + '<input id="rlrole" placeholder="role (optional)" aria-label="role" style="font-size:11px;background:var(--panel-2);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:4px 6px;width:110px">'
        + '<input id="rlcomp" placeholder="competences, comma-separated" aria-label="competences" style="font-size:11px;background:var(--panel-2);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:4px 6px;width:170px">'
        + '<button class="psbtn" id="rlregbtn">Register — signed</button></div></div>';
      out.innerHTML = h;
      const regbtn = out.querySelector("#rlregbtn");
      if (regbtn) regbtn.addEventListener("click", register);
      return true;
    };

    async function register() {
      const id = (host.querySelector("#rlid") || {}).value || "";
      const kind = (host.querySelector("#rlkind") || {}).value || "human";
      const role = (host.querySelector("#rlrole") || {}).value || "";
      const comps = ((host.querySelector("#rlcomp") || {}).value || "").split(",").map((s) => s.trim()).filter(Boolean);
      if (!id.trim()) { announce("A party needs an id."); return; }
      try {
        const r = await tool("workspace_policy", { op: "party_register", params: { folder_context: ctx.workspace.path, party_id: id.trim(), kind, role: role.trim(), competences: comps, actor: "app-user" } });
        announce((r && (r.ok === false || r.error)) ? ("Could not register: " + (r.error || "failed")) : ("Registered " + id.trim() + " — signed"));
      } catch (e) {
        announce("Could not register: " + ((e && e.message) || "failed"));
      }
      await load();
      reload();
    }

    await load();
  },
});
