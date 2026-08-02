// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Data drawer — local memory, redacted mirrors, and the mirror-review
// lifecycle (history/diff/discard) behind
// docs/loomground-proposals/panel-mount-contract.md. This drawer WRITES
// (remember a fact, generate/approve/reveal a mirror, discard a draft), so
// the manifest declares plain access:"write" with no badge override — the
// shell draws no badge at all for it (panel-mount-contract.md §3.1).
//
// Erasure and Bring-in used to live in this same function; they are now
// separate pack panels (erasure.js, and the Bring-in drawer respectively) —
// re-homed to Rules → Erasure and Set up → Bring-in, not part of this
// bundle. Data-lineage tags are a distinct, Inspector-embedded feature
// (rendered inside index.html's inspect()/#inspectBody, keyed to a selected
// node) and stay there — not part of this drawer.
Patchbay.register("data", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc, escA } = ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "What is stored locally — <b>remembered</b> facts and the redacted " +
      "<b>mirrors</b>. Forward actions are exposed here; the <b>server " +
      "decides</b> and signs each. Revealing redacted content loosens — it " +
      "asks first. Erasure lives in Rules → Erasure; bring-in in Set up " +
      "→ Bring-in.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "dtout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const DIP = "width:100%;margin-top:4px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:5px;font-size:11px";

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to read its local data</div>';
        return;
      }
      const head = (t) => '<div style="font-weight:600;color:#fff;font-size:11px;margin:10px 0 6px">' + esc(t) + "</div>";
      const card = (t, b, k) => '<div class="finding ' + (k || "info") + '" style="margin-bottom:8px"><span class="ttl">' + t + "</span>" + b + "</div>";
      const get = async (tn, op) => {
        try {
          return await tool(tn, { op: op, params: { folder_context: ctx.workspace.path } });
        } catch (e) {
          return { error: (e && e.message) || "failed" };
        }
      };
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">reading the local facades…</div>';
      const [mem, mir] = await Promise.all([get("workspace_memory", "recent"), get("workspace_mirror", "list")]);
      let h = "";
      h += head("Memory");
      if (mem.error) h += card("Remembered pairs — could not read", esc(mem.error), "warn");
      else if (mem.served_sealed) h += card("Remembered pairs", esc(mem.count || 0) + " pair(s) served from a sealed workspace", "info");
      else {
        const n = Number(mem.count || 0);
        if (!n) h += card("Remembered pairs", "nothing remembered yet for this folder", "info");
        else {
          const ex = (mem.results || []).slice(0, 3).map((p) => {
            const id = (p && p.id) || "";
            const sm = (p && p.problem && p.problem.summary) || "";
            return '<div class="path" title="' + escA(esc(id)) + '">' + esc(sm || id || "(pair)") + "</div>";
          }).join("");
          h += card("Remembered pairs", esc(n) + " live pair(s) in scope" + ex, "info");
        }
      }
      h += '<details style="margin:2px 0 8px"><summary style="cursor:pointer;font-size:11px;color:var(--txt-dim)">+ remember a fact (subject · predicate · object)</summary>'
        + '<input id="memS" placeholder="subject" style="' + DIP + '"><input id="memP" placeholder="predicate" style="' + DIP + '"><input id="memO" placeholder="object" style="' + DIP + '">'
        + '<button class="tool" id="membtn" style="margin-top:5px;width:100%">Remember</button></details>';
      h += head("Mirror");
      if (mir.error) h += card("Redacted mirrors — could not read", esc(mir.error), "warn");
      else if (mir.ok === false) h += card("Redacted mirrors — could not read", esc(mir.error || "unavailable"), "warn");
      else {
        const ms = mir.mirrors || [];
        const n = Number(mir.count || ms.length || 0);
        if (!n) h += card("Redacted mirrors", "no mirror generated yet for this folder", "info");
        else {
          const ex = ms.slice(0, 3).map((m) => {
            const sp = (m && (m.source_path || m.mirror_path)) || "";
            const kd = (m && m.kind) || "";
            const sc = m && m.span_count != null ? m.span_count : "";
            return '<div class="path" title="' + escA(esc(sp)) + '">' + esc(bn(sp) || "(mirror)") + (kd ? " · " + esc(kd) : "") + (sc !== "" ? " · " + esc(sc) + " span(s)" : "") + "</div>";
          }).join("");
          h += card("Redacted mirrors", esc(n) + " mirror(s) under this folder" + ex, "info");
        }
      }
      h += '<details style="margin:2px 0 8px"><summary style="cursor:pointer;font-size:11px;color:var(--txt-dim)">+ generate · approve · reveal a mirror</summary>'
        + '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:4px">Generate redacts a source file; approve promotes it — both forward. Reveal un-redacts a span: it loosens privacy, so it confirms and needs a controller key.</div>'
        + '<input id="mirSrc" placeholder="source file path (to generate)" style="' + DIP + '"><button class="tool" id="mirgenbtn" style="margin-top:5px;width:100%">Generate mirror</button>'
        + '<input id="mirPath" placeholder="mirror path (to approve)" style="' + DIP + '"><input id="mirApprover" placeholder="approver" style="' + DIP + '"><button class="tool" id="mirapprbtn" style="margin-top:5px;width:100%">Approve mirror</button>'
        + '<input id="urMirPath" placeholder="mirror path (to reveal)" style="' + DIP + '"><input id="urSpan" placeholder="span id" style="' + DIP + '"><input id="urKey" placeholder="controller key" style="' + DIP + '"><button class="tool" id="urbtn" style="margin-top:5px;width:100%;border-color:#df8b46;color:#df8b46">Reveal redacted span…</button></details>';
      // Review the redaction draft BEFORE approving — its revision history, a
      // diff against an earlier revision, and discard (roll back a bad draft + release
      // the edit lock). history/diff are reads; discard loosens nothing but destroys a
      // draft, so it confirms first. Closes the half-built B9 mirror-edit lifecycle.
      h += '<details style="margin:2px 0 8px"><summary style="cursor:pointer;font-size:11px;color:var(--txt-dim)">+ review a draft · history · diff · discard</summary>'
        + '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:4px">Inspect a mirror draft before you approve it: its revisions, what changed since a revision, or discard the draft and release its edit lock.</div>'
        + '<input id="mrvPath" placeholder="mirror path" style="' + DIP + '"><button class="tool" id="mirhistbtn" style="margin-top:5px;width:100%">Show revision history</button>'
        + '<input id="mrvFrom" placeholder="from revision (e.g. 1)" style="' + DIP + '"><button class="tool" id="mirdiffbtn" style="margin-top:5px;width:100%">Diff against revision</button>'
        + '<button class="tool" id="mirdiscbtn" style="margin-top:5px;width:100%;border-color:#cf463c;color:#cf463c">Discard draft…</button>'
        + '<div id="mirRev" class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:6px"></div></details>';
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:4px">Forward actions are exposed here; the server decides and signs each. Revealing redacted content loosens — it asks first and names the consequence.</div>';
      out.innerHTML = h;
      bindWrites();
    };

    const bindWrites = () => {
      const v = (id) => ((host.querySelector("#" + id) || {}).value || "").trim();
      const ok = (r) => !(r && (r.error || r.ok === false || r.ingested === false || r.remembered === false));
      const err = (r) => (r && (r.error || (r.lock && r.lock.reason))) || "failed";
      const submit = async (btnId, tn, op, params, okMsg) => {
        const b = host.querySelector("#" + btnId);
        if (b) b.disabled = true;
        let r;
        try {
          r = await tool(tn, { op: op, params: Object.assign({ folder_context: ctx.workspace.path }, params) });
        } catch (e) {
          r = { error: (e && e.message) || "failed" };
        }
        if (ok(r)) {
          announce(typeof okMsg === "function" ? okMsg(r) : okMsg);
          try { await load(); } catch (_) { if (b) b.disabled = false; }
        } else {
          announce("Refused: " + err(r));
          if (b) b.disabled = false;
        }
      };
      const on = (id, fn) => {
        const b = host.querySelector("#" + id);
        if (b) b.addEventListener("click", fn);
      };
      on("membtn", () => {
        const s = v("memS"), p = v("memP"), o = v("memO");
        if (!(s && p && o)) { announce("subject, predicate and object are all required"); return; }
        submit("membtn", "workspace_memory", "remember", { subject: s, predicate: p, object: o }, "remembered, signed");
      });
      on("mirgenbtn", () => {
        const sp = v("mirSrc");
        if (!sp) { announce("a source file path is required"); return; }
        submit("mirgenbtn", "workspace_mirror", "generate", { source_path: sp, actor: "app-user" }, (r) => "mirror generated — " + ((r && r.span_count) || 0) + " span(s), signed");
      });
      on("mirapprbtn", () => {
        const mp = v("mirPath"), ap = v("mirApprover");
        if (!(mp && ap)) { announce("mirror path and approver are required"); return; }
        submit("mirapprbtn", "workspace_mirror", "approve", { mirror_path: mp, approver: ap }, "mirror approved, signed");
      });
      on("urbtn", () => {
        const mp = v("urMirPath"), sp = v("urSpan"), ck = v("urKey");
        if (!(mp && sp && ck)) { announce("mirror path, span id and a controller key are required"); return; }
        if (!confirm("Reveal the redacted content of span " + sp + "? This RESTORES previously-redacted text — it loosens privacy and is signed.")) return;
        submit("urbtn", "workspace_mirror", "un_redact", { mirror_path: mp, span_id: sp, controller_key: ck, actor: "app-user" }, "span revealed, signed");
      });
      // Mirror review: history + diff are reads (render into mirRev); discard is a
      // write that destroys a draft, so it confirms and names the consequence.
      const mirRead = async (btnId, op, params, render) => {
        const b = host.querySelector("#" + btnId), o = host.querySelector("#mirRev");
        if (b) b.disabled = true;
        if (o) o.textContent = "loading…";
        let r;
        try {
          r = await tool("workspace_mirror", { op: op, params: Object.assign({ folder_context: ctx.workspace.path }, params) });
        } catch (e) {
          r = { error: (e && e.message) || "failed" };
        }
        if (b) b.disabled = false;
        if (o) o.innerHTML = r && r.error ? '<span style="color:#df8b46">could not read: ' + esc(r.error) + "</span>" : render(r);
      };
      on("mirhistbtn", () => {
        const mp = v("mrvPath");
        if (!mp) { announce("a mirror path is required"); return; }
        mirRead("mirhistbtn", "history", { mirror_path: mp }, (r) => {
          const rev = r.revisions || r.history || [];
          if (!rev.length) return "no revisions recorded for this draft";
          const rn = (x) => (x.rev != null ? x.rev : x.revision != null ? x.revision : "?");
          return "<b>" + esc(rev.length) + " revision(s)</b>" + rev.map((x) => '<div class="path">rev ' + esc(rn(x)) + " · " + esc(x.actor || x.by || "?") + (x.operation ? " · " + esc(x.operation) : "") + (x.reason ? " — " + esc(x.reason) : "") + "</div>").join("");
        });
      });
      on("mirdiffbtn", () => {
        const mp = v("mrvPath"), fr = v("mrvFrom");
        if (!(mp && fr)) { announce("mirror path and a from-revision are required"); return; }
        mirRead("mirdiffbtn", "diff", { mirror_path: mp, from_rev: isNaN(+fr) ? fr : +fr }, (r) => {
          const d = r.diff;
          if (typeof d === "string") {
            const lines = d.split("\n").filter(Boolean);
            if (!lines.length) return "<b>diff from rev " + esc(fr) + "</b> · no change";
            return "<b>diff from rev " + esc(fr) + '</b><pre style="white-space:pre-wrap;font-size:10px;margin:3px 0;color:#cdd2dc">' + lines.slice(0, 30).map(esc).join("\n") + "</pre>";
          }
          const ch = Array.isArray(d) ? d : r.changes || r.spans || [];
          return "<b>diff from rev " + esc(fr) + "</b> · " + esc(ch.length) + " change(s)" + ch.slice(0, 8).map((c) => '<div class="path">' + esc(typeof c === "string" ? c : JSON.stringify(c)) + "</div>").join("");
        });
      });
      on("mirdiscbtn", () => {
        const mp = v("mrvPath");
        if (!mp) { announce("a mirror path is required"); return; }
        if (!confirm("Discard the draft at " + mp + "? This rolls back the unapproved edits and releases the edit lock (recorded). It does not touch an already-approved mirror.")) return;
        submit("mirdiscbtn", "workspace_mirror", "discard", { mirror_path: mp, actor: "app-user", reason: "discarded from Data drawer" }, "draft discarded, lock released, signed");
      });
    };

    await load();
  },
});
