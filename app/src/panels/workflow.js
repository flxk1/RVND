// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Workflow board panel — behind
// docs/loomground-proposals/panel-mount-contract.md, and (with contract) a
// mode-routing write panel: two prior menu entries (Record → Run board,
// Pending → Stuck runs) opened the same dialog with a mode argument. The
// shell mounts one host per manifest id, so — following contract.js's exact
// precedent — this is now one panel with an in-panel tab toggle between the
// two views, exposing a `host._workflowSetTab` hook (not a window global) for
// the back-compat wrapper to route a direct-to-a-tab open. Run board (the
// default): definitions, active runs, the run queue, transport audit — zero
// write controls. Stuck runs: waiting runs with cancel/resume, plus per-
// definition run/enqueue/delete. Delete here is also exercised by
// wf_unpin_render_test.py, which pairs it with the unrelated AI & Capture
// skill-unpin action in one gate file; this bundle owns only the workflow
// half of that gate.
Patchbay.register("workflow", {
  async open(ctx) {
    const { host, tool } = ctx;
    const { esc, escA } = ctx.ui;

    let mode = "read"; // "read" | "act" — in-panel toggle; the shell draws one fixed frame

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Defined workflows, the run queue, and runs in flight. Run and Enqueue start work; " +
      "Resume re-queues a stuck run; Cancel is a safe stop; Delete removes a workflow " +
      "definition from the board. The server records every run and transition.";
    host.appendChild(intro);

    const TABSTYLE = "cursor:pointer;padding:3px 10px;border:1px solid var(--line);border-radius:6px;font-size:11px";
    const tabs = document.createElement("div");
    tabs.style.cssText = "display:flex;gap:8px;margin-bottom:8px";
    tabs.innerHTML =
      '<span data-wftab="read" role="tab" tabindex="0" aria-selected="true" style="' + TABSTYLE + '">Run board</span>' +
      '<span data-wftab="act" role="tab" tabindex="0" aria-selected="false" style="' + TABSTYLE + '">Stuck runs</span>';
    host.appendChild(tabs);

    const out = document.createElement("div");
    out.id = "wfout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const card = (t, b, k) => '<div class="finding ' + (k || "info") + '" style="margin-bottom:8px"><span class="ttl">' + t + "</span>" + b + "</div>";
    const get = async (op) => {
      try {
        return await tool("workspace_workflow", { op, params: { folder_context: ctx.workspace.path } });
      } catch (e) {
        return { error: (e && e.message) || "failed" };
      }
    };

    const wfRun = async (ne) => {
      const name = decodeURIComponent(ne);
      if (!name) return;
      if (!confirm('Run "' + name + '" now? This starts work and dispatches its steps.')) return;
      try {
        const r = await tool("workspace_workflow", { op: "run", params: { folder_context: ctx.workspace.path, name } });
        announce((r && (r.ok === false || r.error)) ? ("Could not run: " + esc(r.error || "failed")) : ('Ran "' + esc(name) + '" — ' + esc((r && r.final_state) || "done")));
      } catch (e) {
        announce("Could not run: " + ((e && e.message) || "failed"));
      }
      load();
    };
    const wfEnqueue = async (ne) => {
      const name = decodeURIComponent(ne);
      if (!name) return;
      if (!confirm('Enqueue "' + name + '" for the background worker? This starts work.')) return;
      try {
        const r = await tool("workspace_workflow", { op: "enqueue", params: { folder_context: ctx.workspace.path, name, enqueued_by: "app-user" } });
        announce((r && (r.ok === false || r.error)) ? ("Could not enqueue: " + esc(r.error || "failed")) : "Enqueued — " + esc((r && r.run_id) || ""));
      } catch (e) {
        announce("Could not enqueue: " + ((e && e.message) || "failed"));
      }
      load();
    };
    const wfDelete = async (ne) => {
      const name = decodeURIComponent(ne);
      if (!name) return;
      if (!confirm('Delete the workflow definition "' + name + '"? This removes it from the board. In-flight runs are unaffected; recorded.')) return;
      try {
        const r = await tool("workspace_workflow", { op: "delete", params: { folder_context: ctx.workspace.path, name } });
        announce((r && (r.ok === false || r.error)) ? ("Could not delete: " + esc(r.error || "failed")) : ('Deleted "' + esc(name) + '".'));
      } catch (e) {
        announce("Could not delete: " + ((e && e.message) || "failed"));
      }
      load();
    };
    const wfCancel = async (rid) => {
      const id = decodeURIComponent(rid);
      if (!id) return;
      try {
        const r = await tool("workspace_workflow", { op: "cancel", params: { run_id: id } });
        announce((r && (r.ok === false || r.error)) ? ("Could not cancel: " + esc(r.error || "failed")) : "Run cancelled.");
      } catch (e) {
        announce("Could not cancel: " + ((e && e.message) || "failed"));
      }
      load();
    };
    const wfResume = async (rid) => {
      const id = decodeURIComponent(rid);
      if (!id) return;
      if (!confirm("Resume run " + id + "? This re-queues it for the worker.")) return;
      try {
        const r = await tool("workspace_workflow", { op: "resume", params: { run_id: id } });
        announce((r && (r.ok === false || r.error)) ? ("Could not resume: " + esc(r.error || "failed")) : "Run resumed.");
      } catch (e) {
        announce("Could not resume: " + ((e && e.message) || "failed"));
      }
      load();
    };

    const bindActControls = () => {
      out.querySelectorAll("[data-wfrun]").forEach((b) => b.addEventListener("click", () => wfRun(b.dataset.wfrun)));
      out.querySelectorAll("[data-wfenqueue]").forEach((b) => b.addEventListener("click", () => wfEnqueue(b.dataset.wfenqueue)));
      out.querySelectorAll("[data-wfdelete]").forEach((b) => b.addEventListener("click", () => wfDelete(b.dataset.wfdelete)));
      out.querySelectorAll("[data-wfcancel]").forEach((b) => b.addEventListener("click", () => wfCancel(b.dataset.wfcancel)));
      out.querySelectorAll("[data-wfresume]").forEach((b) => b.addEventListener("click", () => wfResume(b.dataset.wfresume)));
    };

    const load = async () => {
      const act = mode === "act";
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its workflows</div>';
        return;
      }
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">reading the board…</div>';
      const [wfs, actv, q, stuck, ta] = await Promise.all([get("list"), get("active"), get("queue"), get("inspect_stuck"), get("transport_audit")]);
      let h = "";
      const ws = (wfs && wfs.workflows) || [];
      const entries = (q && q.entries) || [];
      const isTerm = (e) => (e.state === "done" || e.state === "cancelled" || e.state === "failed");
      const badge = (s) => { const c = s === "done" ? "#92c4ac" : (s === "failed" || s === "cancelled" ? "#d98b8b" : (s === "leased" ? "#e6b483" : "#aab0bd")); return '<span style="font-size:10px;border:1px solid ' + c + ";color:" + c + ';border-radius:8px;padding:1px 7px">' + esc(s || "pending") + "</span>"; };
      // one row renderer for both modes: act mode adds the controls, read mode never renders them
      const qrow = (e) => {
        const rid = encodeURIComponent(e.run_id || "");
        let ctl = "";
        if (act) {
          if (!isTerm(e)) ctl += '<button class="tool" style="padding:1px 7px;border-color:#d98b8b;color:#d98b8b" data-wfcancel="' + escA(rid) + '">cancel</button>';
          if (e.state === "leased") ctl += '<button class="tool" style="padding:1px 7px" data-wfresume="' + escA(rid) + '">resume</button>';
        }
        return '<div style="font-size:11px;margin:4px 0;display:flex;gap:6px;align-items:center;flex-wrap:wrap">' + esc(e.workflow_name || "?") + " " + badge(e.state) + ' <span style="color:var(--txt-dim)">' + esc(e.run_id || "") + "</span>" + ctl + "</div>";
      };
      const defRow = (w) => {
        const nm = encodeURIComponent(w.name || "");
        return '<div class="finding info" style="margin-bottom:6px"><span class="ttl">' + esc(w.name) + " · " + esc(w.step_count || 0) + ' step(s)</span><div style="font-size:11px;color:var(--txt-dim)">' + esc(w.description || "") + "</div>" +
          (act ? '<div style="display:flex;gap:6px;margin-top:4px"><button class="tool" style="padding:2px 8px" data-wfrun="' + escA(nm) + '">run</button><button class="tool" style="padding:2px 8px" data-wfenqueue="' + escA(nm) + '">enqueue</button><button class="tool" style="padding:2px 8px;border-color:#cf463c;color:#cf463c" data-wfdelete="' + escA(nm) + '">delete</button></div>' : "") + "</div>";
      };
      const sk = (stuck && stuck.stuck) || [];
      if (act) {
        const waiting = entries.filter((e) => !isTerm(e));
        if (!waiting.length) h += card("Stuck runs", "none — nothing waiting on a person", "ok");
        else h += '<div class="finding warn" style="margin-bottom:8px"><span class="ttl">Stuck runs · ' + esc(waiting.length) + "</span>" + waiting.map(qrow).join("") + "</div>";
        if (sk.length) h += card("⚠ Flagged as stuck", esc(sk.length) + " — worth a look", "warn");
      }
      if (wfs.error) h += card("Workflows — could not read", esc(wfs.error), "warn");
      else if (!ws.length) h += card("Workflows", "none defined on this workspace", "info");
      else h += ws.map(defRow).join("");
      if (!act) {
        const a = (actv && actv.active) || [];
        h += card("Active runs", a.length ? a.map((r) => esc(r.workflow || "?") + " — <b>" + esc(r.state || "") + '</b> · <span style="color:var(--txt-dim)">' + esc(r.run_id || "") + "</span>").join("<br>") : "no run in flight", "info");
        if (!entries.length) h += card("Run queue", "the queue is empty", "info");
        else h += '<div class="finding info" style="margin-bottom:8px"><span class="ttl">Run queue · ' + esc(entries.length) + "</span>" + entries.map(qrow).join("") + "</div>";
        if (sk.length) h += card("⚠ Stuck runs", esc(sk.length) + " — worth a look · resume/cancel in the Stuck runs tab", "warn");
        if (ta && !ta.error) h += card(ta.holds ? "Transport audit · holds" : "⚠ Transport audit", esc(ta.actor_present || 0) + " of " + esc(ta.total || 0) + " run(s) carry an external actor" + (ta.missing_actor ? " · " + esc(ta.missing_actor) + " missing" : ""), ta.holds ? "ok" : "warn");
      }
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:2px">Per-contract sign-off lives in the Approvals drawer. The server decides and signs every transition.</div>';
      out.innerHTML = h;
      if (act) bindActControls();
    };

    const setTab = async (m) => {
      mode = m;
      tabs.querySelectorAll("[data-wftab]").forEach((t) => {
        const sel = t.dataset.wftab === m;
        t.setAttribute("aria-selected", String(sel));
        t.style.background = sel ? "var(--panel-2)" : "transparent";
        t.style.color = sel ? "var(--txt)" : "var(--txt-dim)";
      });
      await load();
    };
    tabs.querySelectorAll("[data-wftab]").forEach((t) => {
      t.addEventListener("click", () => setTab(t.dataset.wftab));
      t.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); setTab(t.dataset.wftab); } });
    });
    // Exposed on the host, not on window (§3.5), so the back-compat wrapper in
    // index.html can still honor a direct-to-Stuck-runs open when the panel
    // is already mounted (openWorkflowPanel('act') while it's showing 'read').
    host._workflowSetTab = setTab;
    await setTab("read");
  },
});
