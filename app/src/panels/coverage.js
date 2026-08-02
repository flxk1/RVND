// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Coverage panel — one drawer, three lenses over the same governed data:
// agents × tasks (who can run what, from governance_graph — a filled cell is
// authority to run, its colour the task's latest verdict), and the server's
// coverage_matrix projection switched between kind × risk (where autonomy is
// weak) and task × role (reserved acts vs the competent roster). An empty
// cell is the gap. Pure projection — no writes; granting or tightening
// authority is a governed step on the patch or in Rules, not here. The same
// coverage_matrix projection also backs the shell's separate MATRIX canvas
// view (a view-toggle state, not this panel) — that view keeps its own copy
// of the cell-rendering logic in app/src/index.html, per the panel-mount
// contract's ban on a bundle reaching into shell internals.
Patchbay.register("coverage", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc, escA } = ui;

    // Local verdict palette: col + label is all a coverage cell needs; the
    // shell's own VERDICT table also carries cord-drawing fields (dash, dbl,
    // sever) this panel has no use for, so this is its own small copy.
    const VERDICT = {
      auto: { col: "#5aa886", label: "auto" },
      human: { col: "#df8b46", label: "needs a person" },
      refused: { col: "#d96a4a", label: "refused now" },
      reserved: { col: "#e2554a", label: "reserved" },
      prohibited: { col: "#a8332b", label: "not allowed" },
      unfired: { col: "#5a616f", label: "" },
      permitted: { col: "#5f6675", label: "" },
    };
    const VINFO = (v) => VERDICT[v] || VERDICT.permitted;
    const ABBR = { auto: "auto", human: "person", refused: "refused", reserved: "reserved", prohibited: "blocked", unfired: "—", permitted: "ok" };
    const DARKTEXT = new Set(["auto", "unfired"]); // light-background verdicts read better with dark text
    const short = (s) => { s = String(s == null ? "" : s); return s.length > 16 ? s.slice(0, 15) + "…" : s; };
    // fills a background for verdicts the run-verdict palette doesn't carry —
    // the coverage-only states (a reservation that is covered, or a fail-closed gap).
    const EXTRA = { covered: "#3f7d63", gap: "#c2453b" };
    const cellBg = (v) => EXTRA[v] || VINFO(v).col;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Agents down the side, tasks across the top. A <b>filled</b> cell means that agent may run that task; " +
      "its colour is how the task’s latest run resolved. An <b>empty</b> cell means <b>no authority</b> " +
      "— the agent can’t touch that task. An empty column is a task no agent can run; an empty row is an " +
      "agent with no authority. Read-only — granting authority is a governed step on the patch.";
    host.appendChild(intro);

    const controls = document.createElement("div");
    controls.className = "ro";
    controls.style.cssText = "display:flex;align-items:center;gap:10px;margin:8px 0 4px;flex-wrap:wrap";
    controls.innerHTML =
      '<label for="cvpreset" style="font-size:11px;color:var(--txt-dim)">Lens</label>' +
      '<select id="cvpreset" aria-label="coverage lens preset" style="font-size:11px;background:var(--panel);color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:2px 6px">' +
      '<option value="agent_task">Agents × tasks — who can run what</option>' +
      '<option value="kind_risk">Kind × risk — where autonomy is weak</option>' +
      '<option value="task_role">Task × role — reserved acts vs roles</option></select>' +
      '<label style="font-size:11px;color:var(--txt-dim);display:inline-flex;align-items:center;gap:4px">' +
      '<input type="checkbox" id="cvgaps"> gaps only</label>';
    host.appendChild(controls);

    const out = document.createElement("div");
    out.id = "cvout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    async function loadAgentTask() {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its coverage</div>';
        return;
      }
      let g;
      try {
        g = await tool("workspace_workflow", { op: "governance_graph", params: { folder_context: ctx.workspace.path } });
      } catch (e) {
        // fail-safe: surface the error without wiping a prior view
        const banner = '<div class="finding bad" style="margin-bottom:6px"><span class="ttl">Could not load coverage</span>' + esc((e && e.message) || "failed") + "</div>";
        if (out.querySelector(".cvtable")) out.insertAdjacentHTML("afterbegin", banner); else out.innerHTML = banner;
        return;
      }
      const nodes = Array.isArray(g && g.nodes) ? g.nodes : [], edges = Array.isArray(g && g.edges) ? g.edges : [];
      const agents = nodes.filter((n) => n.kind === "agent"); // humans sign off, they don't run tasks
      const tasks = nodes.filter((n) => n.kind === "use_case");
      const auth = new Set(edges.filter((e) => e.kind === "authority").map((e) => e.from + "|" + e.to)); // party:agent → uc:task
      const verdict = {};
      for (const e of edges) { if (e.kind === "egress") verdict[e.from] = e.verdict || "unfired"; }
      if (!agents.length || !tasks.length) {
        const msg = (!agents.length && !tasks.length) ? "no agents or tasks yet — add some on the patch"
          : (!agents.length ? "no agents yet — add an agent to see coverage" : "no tasks yet — add a use case to see coverage");
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">' + msg + "</div>";
        return;
      }
      let h = '<table class="cvtable mxtable" role="grid" aria-label="agents by task — a filled cell means authority to run"><thead><tr><th></th>';
      for (const t of tasks) {
        const v = verdict[t.id] || "unfired";
        h += '<th scope="col" class="mxhdr" title="' + escA((t.label || t.id) + " · risk " + (t.risk || "—") + " · " + (VINFO(v).label || v)) + '">' + esc(short(t.label || t.id)) + "</th>";
      }
      h += "</tr></thead><tbody>";
      for (const a of agents) {
        h += '<tr><th scope="row" class="mxrowh" title="' + escA(a.label || a.id) + '">' + esc(short(a.label || a.id)) + "</th>";
        for (const t of tasks) {
          const v = verdict[t.id] || "unfired";
          if (auth.has(a.id + "|" + t.id)) {
            const info = VINFO(v), dark = DARKTEXT.has(v);
            h += '<td style="padding:0"><div class="mxcell" style="background:' + info.col + ";color:" + (dark ? "#15171c" : "#fff") + ';cursor:default" aria-label="' +
              escA((a.label || a.id) + " may run " + (t.label || t.id) + " — " + (info.label || v)) + '" title="' +
              escA((a.label || a.id) + " → " + (t.label || t.id) + " · " + (info.label || v)) + '">' + esc(ABBR[v] || v) + "</div></td>";
          } else {
            h += '<td style="padding:0"><div class="mxcell" style="background:#1b1e24;color:#4b525e;cursor:default" aria-label="' +
              escA((a.label || a.id) + " has no authority to run " + (t.label || t.id)) + '" title="' +
              escA((a.label || a.id) + " — no authority for " + (t.label || t.id)) + '">·</div></td>';
          }
        }
        h += "</tr>";
      }
      h += "</tbody></table>";
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:8px;display:flex;flex-wrap:wrap;gap:8px" aria-label="legend">'
        + ["auto", "human", "reserved", "refused", "prohibited", "unfired"].map((v) =>
            '<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:10px;border-radius:2px;background:' +
            VERDICT[v].col + ';display:inline-block"></span>' + esc(ABBR[v] || v) + "</span>").join("")
        + '<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:10px;border-radius:2px;background:#1b1e24;display:inline-block"></span>no authority</span></div>';
      out.innerHTML = h;
    }

    async function loadMatrix() {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its policy shape</div>';
        return;
      }
      const preset = (host.querySelector("#cvpreset") || {}).value || "kind_risk";
      const gapsOnly = !!(host.querySelector("#cvgaps") || {}).checked;
      let m;
      try {
        m = await tool("workspace_workflow", { op: "coverage_matrix", params: { folder_context: ctx.workspace.path, preset, gaps_only: gapsOnly } });
      } catch (e) {
        const banner = '<div class="finding bad" style="margin-bottom:6px"><span class="ttl">Could not load the lens</span>' + esc((e && e.message) || "failed") + "</div>";
        if (out.querySelector(".cvtable")) out.insertAdjacentHTML("afterbegin", banner); else out.innerHTML = banner;
        return;
      }
      if (m && m.error) { out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">' + esc(m.error) + "</div>"; return; }
      if (!m || m.empty || !Array.isArray(m.rows) || !m.rows.length) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">' + (gapsOnly ? "no gaps — every cell is clean" : "nothing to show for this lens yet") + "</div>";
        return;
      }
      const colAxis = m.col_axis || "column", noun = preset === "task_role" ? "approver" : "use case";
      let h = '<table class="cvtable mxtable" role="grid" aria-label="' + escA((m.title || "coverage") + " — a ringed cell is a finding") + '"><thead><tr><th></th>';
      for (const c of m.cols) { h += '<th scope="col" class="mxhdr" title="' + escA(colAxis + ": " + c) + '">' + esc(short(c)) + "</th>"; }
      h += "</tr></thead><tbody>";
      m.cells.forEach((row, ri) => {
        h += '<tr><th scope="row" class="mxrowh" title="' + escA(m.rows[ri]) + '">' + esc(short(m.rows[ri])) + "</th>";
        for (const cell of row) {
          if (!cell || cell.verdict === "none") {
            h += '<td style="padding:0"><div class="mxcell" style="background:#1b1e24;color:#4b525e;cursor:default" aria-label="' +
              escA(m.rows[ri] + " at " + cell.col + " — none") + '" title="' + escA(m.rows[ri] + " · " + cell.col + " · none") + '">·</div></td>';
            continue;
          }
          const info = VINFO(cell.verdict), dark = DARKTEXT.has(cell.verdict);
          const ring = cell.finding ? ";box-shadow:0 0 0 2px #e2554a" : "";
          const editable = !!cell.editable;
          const al = cell.row + " at " + cell.col + " — " + (ABBR[cell.verdict] || info.label || cell.verdict) + " (" + cell.count + " " + noun + (cell.count === 1 ? "" : "s") + ")" +
            (cell.finding ? " — finding: " + cell.why : "") + (editable ? " — activate to revoke (tighten-only)" : "");
          h += '<td style="padding:0"><div class="mxcell' + (editable ? " mxedit" : "") + '" style="background:' + cellBg(cell.verdict) + ";color:" + (dark ? "#15171c" : "#fff") +
            ";cursor:" + (editable ? "pointer" : "default") + ring + '"' +
            (editable ? ' role="button" tabindex="0" data-uc="' + escA(cell.use_case_id) + '" data-agent="' + escA(cell.agent_id) + '"' : "") +
            ' aria-label="' + escA(al) + '" title="' + escA(al) + '">' + esc(cell.letter || ABBR[cell.verdict] || cell.verdict) + "</div></td>";
        }
        h += "</tr>";
      });
      h += "</tbody></table>";
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:8px">'
        + (m.findings ? '<b style="color:#e2554a">' + m.findings + " finding" + (m.findings === 1 ? "" : "s") + "</b> (ringed). " : "no findings. ")
        + esc(m.note || "Read-only — loosening is a governed step in Rules.") + "</div>";
      out.innerHTML = h;
    }

    async function load() {
      const preset = (host.querySelector("#cvpreset") || {}).value || "agent_task";
      if (preset !== "agent_task") return loadMatrix();
      return loadAgentTask();
    }

    controls.querySelector("#cvpreset").addEventListener("change", load);
    controls.querySelector("#cvgaps").addEventListener("change", load);
    await load();
  },
});
