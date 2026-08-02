// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Erasure panel — the fifth pack entry behind
// docs/loomground-proposals/panel-mount-contract.md, and (with Audit) one of
// the two write panels moved so far. Removes a person from this machine's
// record (mutation logs, drafts, cards) through workspace_erase: a sweep
// previews scope with no writes, a confirm-gated request queues the erasure
// for review (it never purges on its own), and a status read reports back
// per request id. The manifest declares a custom "acts on the record" badge
// (panel-mount-contract's badge extension) since every control here save the
// sweep preview is a recorded write.
Patchbay.register("erasure", {
  async open(ctx) {
    const { host, tool } = ctx;
    const { esc } = ctx.ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Removes a person from <b>this machine’s record</b> — mutation logs, drafts, cards. " +
      "It does not reach into any external system. Sweep previews scope (no writes); request " +
      "queues an erasure for review (it does not purge); the purge itself is not one-click here.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "erout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const DIP = "width:100%;margin-top:4px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:5px;font-size:11px";

    const bindWrites = () => {
      const v = (id) => ((out.querySelector("#" + id) || {}).value || "").trim();
      const err = (r) => (r && r.error) || "failed";
      const on = (id, fn) => { const b = out.querySelector("#" + id); if (b) b.addEventListener("click", fn); };
      on("ersweepbtn", async () => {
        const sub = v("erSubject");
        const eo = out.querySelector("#eraseout");
        if (!sub) { announce("a subject is required"); return; }
        let r;
        try { r = await tool("workspace_erase", { op: "sweep", params: { folder_context: ctx.workspace.path, subject: sub } }); }
        catch (e) { r = { error: (e && e.message) || "failed" }; }
        if (!eo) return;
        if (r && !r.error && r.ok !== false && r.sweep) {
          const ds = r.sweep.drafts_sealed || [];
          eo.innerHTML = "preview for <b>" + esc(sub) + "</b>: " + esc((r.sweep.total_hits != null ? r.sweep.total_hits : 0)) +
            " hit(s) — no writes made" + (ds.length ? (" · " + esc(ds.length) + " folder(s) sealed — drafts not inspected") : "");
        } else eo.innerHTML = "Could not preview: " + esc(err(r));
      });
      on("erreqbtn", async () => {
        const sub = v("erSubject"), rq = v("erRequester"), rs = v("erReason");
        if (!sub) { announce("name the subject to erase"); return; }
        if (!rq) { announce("an erasure request needs a requester / actor — never one-click"); return; }
        if (!rs) { announce("an erasure request needs a reason"); return; }
        if (!confirm('Request erasure of "' + sub + '"? This queues a Right-to-Erasure workflow; once executed it PURGES the subject from this machine’s record and is IRREVERSIBLE. Recorded against ' + rq + ".")) return;
        const b = out.querySelector("#erreqbtn"); if (b) b.disabled = true;
        let r;
        try { r = await tool("workspace_erase", { op: "request", params: { folder_context: ctx.workspace.path, subject: sub, requester_ref: rq, reason: rs, actor: rq } }); }
        catch (e) { r = { error: (e && e.message) || "failed" }; }
        if (b) b.disabled = false;
        const eo = out.querySelector("#eraseout");
        if (r && !r.error && r.ok !== false) {
          announce("erasure requested (queued) — " + ((r && r.request_id) || ""));
          if (eo) eo.innerHTML = "request queued — id <b>" + esc(r.request_id || "") + "</b> · keep it to read status";
        } else announce("Refused: " + err(r));
      });
      on("erstatbtn", async () => {
        const id = v("erReqId");
        const eo = out.querySelector("#eraseout");
        if (!id) { announce("a request id is required"); return; }
        let r;
        try { r = await tool("workspace_erase", { op: "status", params: { folder_context: ctx.workspace.path, request_id: id } }); }
        catch (e) { r = { error: (e && e.message) || "failed" }; }
        if (eo) eo.innerHTML = (r && !r.error && r.ok !== false && r.manifest)
          ? ("request <b>" + esc(id) + "</b>: " + ((r.manifest.executed) ? "executed" : "requested, not yet executed"))
          : ("Could not read status: " + esc(err(r)));
      });
    };

    const load = () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to manage its erasure requests</div>';
        return;
      }
      let h = "";
      h += '<div class="finding info" style="margin-bottom:8px"><span class="ttl">Status</span>' +
        '<div class="ro" style="font-size:10.5px;color:var(--txt-dim)">Open requests, sweeps and tombstones are read from the signed record <b>per request id</b>.</div>' +
        '<input id="erReqId" placeholder="request id" style="' + DIP + '"><button class="tool" id="erstatbtn" style="margin-top:5px;width:100%">Read status</button></div>';
      h += '<div class="finding warn" style="margin-bottom:8px"><span class="ttl">Request</span>' +
        '<div class="ro" style="font-size:10.5px;color:var(--txt-dim)">Sweep previews what an erasure would touch — no writes. Request queues the erasure for review against a named requester and reason.</div>' +
        '<input id="erSubject" placeholder="subject (e.g. an email)" style="' + DIP + '"><button class="tool" id="ersweepbtn" style="margin-top:5px;width:100%">Preview sweep (no writes)</button>' +
        '<input id="erRequester" placeholder="requester / actor" style="' + DIP + '"><input id="erReason" placeholder="reason" style="' + DIP + '">' +
        '<button class="tool" id="erreqbtn" style="margin-top:5px;width:100%;border-color:#cf463c;color:#cf463c">Request erasure…</button></div>' +
        '<div id="eraseout" class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:6px"></div>';
      out.innerHTML = h;
      bindWrites();
    };

    load();
  },
});
