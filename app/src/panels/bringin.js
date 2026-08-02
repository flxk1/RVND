// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Bring-in panel — the ninth pack entry behind
// docs/loomground-proposals/panel-mount-contract.md, and (with Audit and
// Erasure) one of the write panels moved so far. Brings source material
// inside the boundary through workspace_ingest: a file path stays local and
// signs a set of memory pairs; a URL crosses the boundary — the server
// fetches it, enforces robots.txt / TDM, and the drawer confirms first; a
// skill source signs into the skill register. The manifest declares a
// custom "acts on the record" badge (panel-mount-contract's badge
// extension), the same wording as Erasure's, since every control here is a
// recorded write and none of them is purely read.
Patchbay.register("bringin", {
  async open(ctx) {
    const { host, tool } = ctx;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "A file or skill stays local; a URL <b>crosses the boundary</b> — the server " +
      "fetches it, enforces robots / TDM, and confirms first. Every ingest is decided " +
      "and signed by the server.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "biout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const DIP = "width:100%;margin-top:4px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:5px;font-size:11px";

    const bindWrites = () => {
      const v = (id) => ((out.querySelector("#" + id) || {}).value || "").trim();
      const err = (r) => (r && r.error) || "failed";
      const on = (id, fn) => { const b = out.querySelector("#" + id); if (b) b.addEventListener("click", fn); };
      const submit = async (btnId, op, params, okMsg) => {
        const b = out.querySelector("#" + btnId); if (b) b.disabled = true;
        let r;
        try { r = await tool("workspace_ingest", { op, params: Object.assign({ folder_context: ctx.workspace.path }, params) }); }
        catch (e) { r = { error: (e && e.message) || "failed" }; }
        if (b) b.disabled = false;
        if (r && !(r.error || r.ok === false || r.ingested === false)) announce(typeof okMsg === "function" ? okMsg(r) : okMsg);
        else announce("Refused: " + err(r));
      };
      on("ingpathbtn", () => {
        const fp = v("ingPath");
        if (!fp) { announce("a file path is required"); return; }
        submit("ingpathbtn", "path", { file_path: fp }, (r) => (r && r.idempotent_noop) ? "already ingested — no change" : ("ingested — " + ((r && r.count) || 0) + " pair(s), signed"));
      });
      on("ingurlbtn", () => {
        const u = v("ingUrl");
        if (!/^https?:\/\//i.test(u)) { announce("an http(s) URL is required"); return; }
        if (!confirm("Fetch this URL into the folder?\n\n" + u + "\n\nThis CROSSES THE BOUNDARY — the server fetches it and enforces robots.txt / TDM. Recorded.")) return;
        submit("ingurlbtn", "url", { url: u, actor: "app-user" }, (r) => "URL " + ((r && r.state) || "recorded") + ", signed");
      });
      on("ingskillbtn", () => {
        const src = v("ingSkill");
        if (!src) { announce("skill source is required"); return; }
        submit("ingskillbtn", "skill", { source: src, author: "app-user" }, (r) => "skill ingested" + ((r && r.skill_id) ? (" — " + r.skill_id) : "") + ", signed");
      });
    };

    const load = () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to bring material in</div>';
        return;
      }
      out.innerHTML = '<input id="ingPath" placeholder="absolute file path" style="' + DIP + '"><button class="tool" id="ingpathbtn" style="margin-top:5px;width:100%">Ingest file</button>'
        + '<input id="ingUrl" placeholder="https:// URL to fetch" style="' + DIP + '"><button class="tool" id="ingurlbtn" style="margin-top:5px;width:100%;border-color:#c8a23f;color:#c8a23f">Fetch URL…</button>'
        + '<textarea id="ingSkill" placeholder="skill source (SKILL.md / prose)" rows="2" style="' + DIP + ';resize:vertical"></textarea><button class="tool" id="ingskillbtn" style="margin-top:5px;width:100%">Ingest skill</button>';
      bindWrites();
    };

    load();
  },
});
