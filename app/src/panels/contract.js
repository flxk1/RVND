// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Contract execution panel — the eighth pack entry behind
// docs/loomground-proposals/panel-mount-contract.md, and the third write
// panel (with audit, erasure). Covers workspace_contract end to end: recorded
// reviews, ingested contracts and the obligations the runtime watches are
// read projections; tick (deadline sweep), ingest (extract obligations from
// pasted text) and resolve (close an obligation — needs a named actor and a
// reason) are the governed writes. The shell mounts one host for this
// manifest id, so the old two-menu-entry, two-mode dialog (Rules → Contracts
// / Pending → Decision queue) is now one panel with an in-panel toggle
// between the two views; the toggle itself renders as non-button tab
// elements so the read view still renders zero <button>s, same invariant the
// original two-dialog design held. The manifest declares a custom "reads ·
// resolves" badge (panel-mount-contract's badge extension) since the panel
// reads more than it writes but is not purely read.
Patchbay.register("contract", {
  async open(ctx) {
    const { host, tool } = ctx;
    const { esc, escA } = ctx.ui;

    let mode = "read"; // "read" | "act" — in-panel toggle; the shell draws one fixed frame

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Recorded reviews, ingested contracts and the obligations the runtime watches. " +
      "Tick sweeps deadlines; resolve closes an obligation (needs a named actor + reason); " +
      "sign-off lives in Sign-offs. The server decides and signs every write.";
    host.appendChild(intro);

    const TABSTYLE = "cursor:pointer;padding:3px 10px;border:1px solid var(--line);border-radius:6px;font-size:11px";
    const tabs = document.createElement("div");
    tabs.style.cssText = "display:flex;gap:8px;margin-bottom:8px";
    tabs.innerHTML =
      '<span data-cttab="read" role="tab" tabindex="0" aria-selected="true" style="' + TABSTYLE + '">Terms &amp; obligations</span>' +
      '<span data-cttab="act" role="tab" tabindex="0" aria-selected="false" style="' + TABSTYLE + '">Decision queue</span>';
    host.appendChild(tabs);

    const out = document.createElement("div");
    out.id = "ctout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const card = (t, b, k) => '<div class="finding ' + (k || "info") + '" style="margin-bottom:8px"><span class="ttl">' + t + "</span>" + b + "</div>";
    const get = async (op) => {
      try {
        return await tool("workspace_contract", { op, params: { folder_context: ctx.workspace.path } });
      } catch (e) {
        return { error: (e && e.message) || "failed" };
      }
    };

    const ctTick = async () => {
      if (!ctx.workspace.path) return;
      if (!confirm("Sweep deadlines now? Tick advances obligation states by date arithmetic and stops at breach-candidate.")) return;
      try {
        const r = await tool("workspace_contract", { op: "tick", params: { folder_context: ctx.workspace.path } });
        announce((r && (r.ok === false || r.error)) ? ("Could not tick: " + esc(r.error || "failed")) : "Ticked — deadlines swept.");
      } catch (e) {
        announce("Could not tick: " + ((e && e.message) || "failed"));
      }
      load();
    };

    const ctIngest = async () => {
      if (!ctx.workspace.path) return;
      const text = ((out.querySelector("#cttext") || {}).value || "");
      if (!text.trim()) { announce("paste contract text to ingest"); return; }
      if (!confirm("Ingest this contract into the runtime? It extracts obligations and starts watching them.")) return;
      try {
        const r = await tool("workspace_contract", { op: "ingest", params: { folder_context: ctx.workspace.path, text } });
        announce((r && (r.ok === false || r.error)) ? ("Could not ingest: " + esc(r.error || "failed")) : "Contract ingested, signed.");
      } catch (e) {
        announce("Could not ingest: " + ((e && e.message) || "failed"));
      }
      load();
    };

    const ctResolve = async (oidEnc, choice) => {
      const oid = decodeURIComponent(oidEnc);
      if (!oid) return;
      const actor = ((out.querySelector("#ctactor") || {}).value || "").trim();
      if (!actor) { announce("set a named actor before resolving"); return; }
      const rationale = prompt('Reason for marking this obligation "' + choice + '" (recorded as ' + actor + "):", "");
      if (rationale === null) return;
      if (!rationale.trim()) { announce("a rationale is required"); return; }
      try {
        const r = await tool("workspace_contract", { op: "resolve", params: { folder_context: ctx.workspace.path, obligation_id: oid, choice, actor, rationale: rationale.trim() } });
        announce((r && (r.ok === false || r.error)) ? ("Could not resolve: " + esc(r.error || "failed")) : "Obligation resolved as " + esc(choice) + ", recorded.");
      } catch (e) {
        announce("Could not resolve: " + ((e && e.message) || "failed"));
      }
      load();
    };

    const bindActControls = () => {
      const tick = out.querySelector("#cttick");
      if (tick) tick.addEventListener("click", ctTick);
      const ing = out.querySelector("#ctingbtn");
      if (ing) ing.addEventListener("click", ctIngest);
      const ap = out.querySelector("#ctapprovals");
      if (ap) ap.addEventListener("click", () => { window.openApprovalsPanel && window.openApprovalsPanel(); });
      out.querySelectorAll("[data-ctresolve]").forEach((b) => b.addEventListener("click", () => {
        const parts = b.dataset.ctresolve.split("|");
        ctResolve(parts[0], parts[1]);
      }));
    };

    const load = async () => {
      const act = mode === "act";
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its contracts</div>';
        return;
      }
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">reading the contract stack…</div>';
      const [rev, st] = await Promise.all([get("list_reviews"), get("state")]);
      let h = "";
      if (!act) {
        const rs = (rev && rev.reviews) || [];
        if (rev.error) h += card("Reviews — could not read", esc(rev.error), "warn");
        else if (!rs.length) h += card("Reviews", "no contract reviews recorded", "info");
        else {
          const tl = (t) => {
            const c = t === "green" ? "#92c4ac" : (t === "red" ? "#d98b8b" : (t === "amber" ? "#e6b483" : "#aab0bd"));
            return '<span style="font-size:10px;border:1px solid ' + c + ";color:" + c + ';border-radius:8px;padding:1px 7px">' + esc(t || "grey") + "</span>";
          };
          h += '<div class="finding info" style="margin-bottom:8px"><span class="ttl">Reviews · ' + esc(rev.count || rs.length) + "</span>" +
            rs.slice(0, 8).map((r) => '<div style="font-size:11px;margin:3px 0">' + esc(r.contract_id || "—") + " " + tl(r.traffic_light) +
              ' <span style="color:var(--txt-dim)">' + esc(r.decision || "") + "</span></div>").join("") + "</div>";
        }
      }
      if (st.error) {
        h += card("Contract stack — could not read", esc(st.error), "warn");
      } else {
        const contracts = st.contracts || [], obligations = st.obligations || [], queue = st.decision_queue || [];
        if (!act) {
          h += card("Contracts", esc(contracts.length) + " ingested" +
            (contracts.length ? " · " + contracts.slice(0, 6).map((c) => esc(c.ref || (c.instance && c.instance.contract_id) || "")).join(" · ") : ""), "info");
          h += card("Obligations", obligations.length ? (esc(obligations.length) + " watched") : "none — ingest a contract to populate the runtime", "info");
        } else if (queue.length) {
          h += '<div class="finding warn" style="margin-bottom:8px"><span class="ttl">Decision queue · ' + esc(queue.length) + "</span>" +
            queue.map((qz) => {
              let ctl = "";
              if (qz.obligation_id) {
                const oid = encodeURIComponent(qz.obligation_id);
                (qz.options || []).filter((o) => o.id === "satisfied" || o.id === "waived").forEach((o) => {
                  ctl += '<button class="tool" style="padding:1px 7px;margin-left:4px" data-ctresolve="' + escA(oid) + "|" + escA(o.id) + '">' + esc(o.label || o.id) + "</button>";
                });
              }
              return '<div style="font-size:11px;margin:4px 0">' + esc(qz.kind || "") + " — " + esc(qz.subject || "") + ctl + "</div>";
            }).join("") + "</div>";
        } else {
          h += card("Decision queue", "clear — nothing waiting on a human judgment", "ok");
        }
      }
      if (act) {
        h += '<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">' +
          '<label style="font-size:11px;color:var(--txt-dim)">acting as <input id="ctactor" value="operator" style="background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:3px 6px;font-size:11px;width:110px"></label>' +
          '<button class="tool" id="cttick" style="padding:2px 8px">tick</button>' +
          '<button class="tool" id="ctapprovals" style="padding:2px 8px">approvals →</button></div>';
        h += '<div class="finding info" style="margin-bottom:6px"><span class="ttl">Ingest a contract</span>' +
          '<textarea id="cttext" placeholder="paste contract text…" style="width:100%;height:70px;margin:4px 0;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:6px;font-size:11px;font-family:IBM Plex Mono,monospace"></textarea>' +
          '<button class="tool" id="ctingbtn" style="padding:2px 8px">ingest</button></div>';
      }
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:2px">' +
        (act ? "Tick advances by date arithmetic and stops at breach-candidate. Resolve demands a named actor + reason. The server decides and signs."
             : "The server decides and signs. Deciding lives in the Decision queue tab.") + "</div>";
      out.innerHTML = h;
      if (act) bindActControls();
    };

    const setTab = async (m) => {
      mode = m;
      tabs.querySelectorAll("[data-cttab]").forEach((t) => {
        const sel = t.dataset.cttab === m;
        t.setAttribute("aria-selected", String(sel));
        t.style.background = sel ? "var(--panel-2)" : "transparent";
        t.style.color = sel ? "var(--txt)" : "var(--txt-dim)";
      });
      await load();
    };
    tabs.querySelectorAll("[data-cttab]").forEach((t) => {
      t.addEventListener("click", () => setTab(t.dataset.cttab));
      t.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); setTab(t.dataset.cttab); } });
    });
    // Exposed on the host, not on window (§3.5), so the back-compat wrapper in
    // index.html can still honor a direct-to-Decision-queue open when the panel
    // is already mounted (openContractPanel('act') while it's showing 'read').
    host._contractSetTab = setTab;
    await setTab("read");
  },
});
