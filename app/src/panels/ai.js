// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// AI & Capture panel — the eleventh pack entry behind
// docs/loomground-proposals/panel-mount-contract.md. One drawer covering two
// gate files: models/runtime status/the capture ledger/recent dispatches are
// reads (workspace_model, workspace_capture, workspace_dispatch "list_pinned"
// / "recent"), but pinning a skill — one at a time or many in a batch
// (workspace_dispatch "pin" / "pin_many"), unpinning, and suggesting
// companion skills to pin — are the drawer's governed, recorded writes. It
// never completes, classifies, captures or dispatches a skill; those invoke
// a model or run a skill and stay deferred. The manifest declares a custom
// "reads · pins" badge (panel-mount-contract's badge extension) since this
// panel is not purely read.
Patchbay.register("ai", {
  async open(ctx) {
    const { host, tool } = ctx;
    const { esc, escA } = ctx.ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Read-only view of the available models, the capture ledger (what left vs stayed) and dispatch " +
      "activity. Completing, classifying, capturing and dispatching are <b>not exposed here</b> — this view " +
      "<b>reads</b> these facades, it never invokes a model or runs a skill.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "aiout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const aiPin = async () => {
      const F = ctx.workspace.path; if (!F) return;
      const ids = ((out.querySelector("#aiPinIds") || {}).value || "").split(",").map((x) => x.trim()).filter(Boolean);
      if (!ids.length) { announce("a skill id is required"); return; }
      let msg;
      try {
        const r = ids.length === 1
          ? await tool("workspace_dispatch", { op: "pin", params: { folder_context: F, skill_id: ids[0], pinned_by: "app-user" } })
          : await tool("workspace_dispatch", { op: "pin_many", params: { folder_context: F, skill_ids: ids, pinned_by: "app-user" } }); // bulk pin in one signed batch
        msg = (r && (r.ok === false || r.error)) ? ("Could not pin: " + esc(r.error || "failed")) : ("Pinned " + ids.length + " skill" + (ids.length === 1 ? "" : "s") + ".");
      } catch (e) {
        msg = "Could not pin: " + ((e && e.message) || "failed");
      }
      announce(msg);
      try { await load(); } catch (_) { }
    };

    const aiPinOne = async (id) => {
      const F = ctx.workspace.path; if (!id || !F) return;
      let msg;
      try {
        const r = await tool("workspace_dispatch", { op: "pin", params: { folder_context: F, skill_id: id, pinned_by: "app-user" } });
        msg = (r && (r.ok === false || r.error)) ? ("Could not pin: " + esc(r.error || "failed")) : ("Pinned " + esc(id) + ".");
      } catch (e) {
        msg = "Could not pin: " + ((e && e.message) || "failed");
      }
      announce(msg);
      try { await load(); } catch (_) { }
    };

    const aiUnpin = async (id) => {
      if (!id || !ctx.workspace.path) return;
      if (!confirm("Unpin the skill “" + id + "” from this folder? It is no longer auto-available to agents here (recorded).")) return;
      let msg;
      try {
        const r = await tool("workspace_dispatch", { op: "unpin", params: { folder_context: ctx.workspace.path, skill_id: id } });
        msg = (r && (r.ok === false || r.error)) ? ("Could not unpin: " + esc(r.error || "failed")) : ("Unpinned “" + esc(id) + "”.");
      } catch (e) {
        msg = "Could not unpin: " + ((e && e.message) || "failed");
      }
      announce(msg);
      try { await load(); } catch (_) { }
    };

    const aiSuggest = async () => {
      const F = ctx.workspace.path || "";
      const id = ((out.querySelector("#aiSugId") || {}).value || "").trim();
      const o = out.querySelector("#aiSugOut");
      if (!id) { announce("a skill id is required"); return; }
      if (o) o.textContent = "loading…";
      let r;
      try { r = await tool("workspace_dispatch", { op: "suggest", params: { skill_id: id, folder_context: F } }); } catch (e) { r = { error: (e && e.message) || "failed" }; }
      if (!o) return;
      if (r && r.error) { o.innerHTML = '<span style="color:#df8b46">could not suggest: ' + esc(r.error) + "</span>"; return; }
      const sug = r.companions || r.suggestions || r.skills || [];
      if (!sug.length) { o.innerHTML = "no companion skills suggested" + (r.family_label ? " for <b>" + esc(r.family_label) + "</b>" : ""); return; }
      o.innerHTML = "<b>companions" + (r.family_label ? " · " + esc(r.family_label) : "") + ":</b>" + sug.map((s) => {
        const sid = String(s && s.skill_id ? s.skill_id : (s && s.id ? s.id : s));
        return '<div style="display:flex;gap:6px;align-items:center;margin-top:2px"><span style="flex:1">' + esc(sid) + '</span><button class="tool" style="padding:1px 7px" data-aipinone="' + escA(sid) + '">pin</button></div>';
      }).join("");
      o.querySelectorAll("[data-aipinone]").forEach((b) => b.addEventListener("click", () => aiPinOne(b.getAttribute("data-aipinone"))));
    };

    const load = async () => {
      const head = (t) => '<div style="font-weight:600;color:#fff;font-size:11px;margin:10px 0 6px">' + esc(t) + "</div>";
      const card = (t, b, k) => '<div class="finding ' + (k || "info") + '" style="margin-bottom:8px"><span class="ttl">' + t + "</span>" + b + "</div>";
      const get = async (tn, op, extra) => {
        try { return await tool(tn, { op, params: Object.assign({}, extra || {}) }); } catch (e) { return { error: (e && e.message) || "failed" }; }
      };
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">reading the AI &amp; capture facades…</div>';
      const folder = ctx.workspace.path || "";
      const [mdl, sts, cap, pin, rec] = await Promise.all([
        get("workspace_model", "list", {}),
        get("workspace_model", "status", { probe_endpoint: true }),
        folder ? get("workspace_capture", "read", { folder_context: folder }) : Promise.resolve({ __nofolder: true }),
        folder ? get("workspace_dispatch", "list_pinned", { folder_context: folder }) : Promise.resolve({ __nofolder: true }),
        folder ? get("workspace_dispatch", "recent", { folder_context: folder, limit: 20 }) : Promise.resolve({ __nofolder: true }),
      ]);
      let h = "";
      h += head("Models");
      if (mdl.error) h += card("Models — could not read", esc(mdl.error), "warn");
      else if (mdl.ok === false || mdl.reachable === false) {
        const ep = mdl.endpoint ? (' · <span class="path">' + esc(mdl.endpoint) + "</span>") : "";
        h += card("Models — backend unreachable", esc(mdl.error || "the local-LLM endpoint is not available") + ep, "warn");
      } else {
        const models = mdl.models || []; const ep = mdl.endpoint ? (' · endpoint <span class="path">' + esc(mdl.endpoint) + "</span>") : "";
        if (!models.length) h += card("Models", "no models reported by the backend" + ep, "info");
        else {
          const items = models.map((m) => '<div style="margin:2px 0" title="' + escA(esc(String(m))) + '">' + esc(String(m)) + "</div>").join("");
          h += card("Models", "<b>" + esc(models.length) + "</b> available" + ep + items, "info");
        }
      }
      // runtime status — per-task readiness, the Tier C backend, endpoint health.
      // Degrades are declared, never silent: each degraded task states its bounded action.
      if (sts.error || sts.ok === false) h += card("Runtime status — could not read", esc(sts.error || "unavailable"), "warn");
      else {
        const rd = sts.readiness || {}; const tasks = rd.tasks || {};
        const rows = Object.keys(tasks).sort().map((k) => {
          const t = tasks[k] || {};
          return '<div style="margin:2px 0"><b>' + esc(k) + "</b> — " + (t.capable
            ? '<span style="color:#8fd1ad">ready</span>' + (t.model_id ? ' · <span class="path">' + esc(t.model_id) + "</span>" : "")
            : '<span style="color:#e6b483">degraded → ' + esc(t.action || "?") + '</span><span style="color:var(--txt-dim)"> · ' + esc(t.reason || "") + "</span>") + "</div>";
        }).join("");
        h += card("Task readiness", ((rd.ready || []).length) + " ready · " + ((rd.degraded || []).length) + " degraded" + rows, ((rd.degraded || []).length) ? "warn" : "info");
        const tc = sts.tier_c || {};
        h += card("Tier C semantic scan", (tc.available ? '<span style="color:#8fd1ad">available</span>' : '<span style="color:#e6b483">unavailable</span>')
          + ' · backend <span class="path">' + esc(tc.backend || "?") + "</span>"
          + '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:2px">' + (tc.fail_closed ? "fail-closed: a broken real backend refuses egress rather than letting it through" : "permissive mock — for onboarding and tests; configure a real backend in Rules → Privacy lock") + "</div>", tc.available ? "info" : "warn");
        const en = sts.endpoint;
        if (en) h += card("Local-LLM endpoint", (en.reachable ? '<span style="color:#8fd1ad">reachable</span>' : '<span style="color:#e6b483">unreachable</span>') + (en.endpoint ? ' · <span class="path">' + esc(en.endpoint) + "</span>" : "") + (!en.reachable && en.error ? '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:2px">' + esc(en.error) + "</div>" : ""), en.reachable ? "info" : "warn");
      }
      h += head("Capture");
      if (cap.__nofolder) h += card("Capture ledger", "open a folder to read its capture ledger", "info");
      else if (cap.error) h += card("Capture ledger — could not read", esc(cap.error), "warn");
      else {
        const rows = cap.captures || []; const n = cap.count != null ? cap.count : rows.length;
        if (!rows.length) h += card("Capture ledger", "nothing has left this folder yet — no LLM or web exchanges recorded", "info");
        else {
          const items = rows.slice(0, 12).map((r) => {
            const model = String(r.model || ""); const scope = String(r.scope || ""); const sum = String(r.summary || ""); const lbl = (model || scope || "capture");
            return '<div style="margin:3px 0" title="' + escA(esc(sum || lbl)) + '"><b>' + esc(lbl) + "</b>" + (scope && model ? (" · " + esc(scope)) : "") + "</div>";
          }).join("");
          h += card("Capture ledger", "<b>" + esc(n) + "</b> recorded" + items, "info");
        }
      }
      h += head("Dispatch");
      if (pin.__nofolder) h += card("Pinned skills", "open a folder to read its pinned skills", "info");
      else if (pin.error || pin.ok === false) h += card("Pinned skills — could not read", esc(pin.error || "unavailable"), "warn");
      else {
        const skills = pin.skills || [];
        if (!skills.length) h += card("Pinned skills", "no skills pinned to this folder", "info");
        else {
          const items = skills.slice(0, 20).map((s) => {
            const id = String(s.id || ""); const by = String(s.pinned_by || ""); const note = String(s.note || "");
            return '<div style="margin:2px 0;display:flex;gap:6px;align-items:center" title="' + escA(esc(note || (by ? ("pinned by " + by) : id))) + '"><span style="flex:1">' + esc(id) + '</span><button class="tool" style="padding:1px 7px;border-color:#df8b46;color:#df8b46" data-aiunpin="' + escA(id) + '">unpin</button></div>';
          }).join("");
          h += card("Pinned skills", "<b>" + esc(skills.length) + "</b> pinned" + items, "info");
        }
      }
      if (!rec.__nofolder) {
        if (rec.error || rec.ok === false) h += card("Recent dispatches — could not read", esc(rec.error || "unavailable"), "warn");
        else {
          const events = rec.events || [];
          if (!events.length) h += card("Recent dispatches", "no skills dispatched in this folder yet", "info");
          else {
            const items = events.slice(0, 20).map((e) => {
              const sid = String(e.skill_id || ""); const kind = String(e.kind || ""); const ts = String(e.timestamp || ""); const via = String(e.chosen_via || "");
              return '<div style="margin:2px 0" title="' + escA(esc(ts + (via ? (" · " + via) : ""))) + '"><b>' + esc(sid || kind || "event") + "</b> · " + esc(kind) + "</div>";
            }).join("");
            h += card("Recent dispatches", "<b>" + esc(events.length) + "</b> recent" + items, "info");
          }
        }
      }
      // Pin skills (one or many) + companion suggestions. Pinning keeps a skill in scope
      // for agents in this folder; suggest surfaces skills that commonly pair with one.
      if (folder) {
        const AIP = "width:100%;margin-top:4px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:5px;font-size:11px";
        h += '<details style="margin:4px 0 6px"><summary style="cursor:pointer;font-size:11px;color:var(--txt-dim)">+ pin skills · suggest companions</summary>'
          + '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:4px">Pin keeps a skill in scope for agents here (multiple ids, comma-separated, pin in one signed batch). Suggest lists skills that commonly pair with one — pin any from the list.</div>'
          + '<input id="aiPinIds" aria-label="skill ids to pin, comma-separated" placeholder="skill id(s), comma-separated" style="' + AIP + '"><button class="tool" id="aipinbtn" style="margin-top:5px;width:100%">Pin skill(s)</button>'
          + '<input id="aiSugId" aria-label="skill id to suggest companions for" placeholder="skill id — suggest companions" style="' + AIP + '"><button class="tool" id="aisugbtn" style="margin-top:5px;width:100%">Suggest companions</button>'
          + '<div id="aiSugOut" class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:6px"></div></details>';
      }
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:6px">Completing, classifying, capturing (llm/web) and dispatching skills are deferred — they invoke a model or run a skill. Pinning is recorded; this drawer otherwise reads.</div>';
      out.innerHTML = h;
      const _pb = out.querySelector("#aipinbtn"); if (_pb) _pb.addEventListener("click", aiPin);
      const _sb = out.querySelector("#aisugbtn"); if (_sb) _sb.addEventListener("click", aiSuggest);
      out.querySelectorAll("[data-aiunpin]").forEach((b) => b.addEventListener("click", () => aiUnpin(b.getAttribute("data-aiunpin"))));
    };

    await load();
  },
});
