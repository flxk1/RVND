// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Protections panel — the thirteenth pack entry behind
// docs/loomground-proposals/panel-mount-contract.md, and one of the write
// panels: reading the snapshot, the jurisdiction-pack stack and the party
// roster (workspace_policy "snapshot" / "juris_packs" / "party_list") takes
// no write, but every dial here is a governed, recorded write — turning a
// protection on or off, setting the default oversight level, setting the
// lock mode, declaring or clearing the AI-training opt-out, setting the
// jurisdiction-pack stack and delegating signing authority. Tightening
// (turning a protection on, raising oversight, raising the lock mode) is
// direct; loosening any of them confirms first and, for turning a protection
// off or loosening the lock mode, requires a typed "accepted by" name and
// reason. The manifest declares a custom "reads · sets protections" badge
// since this panel is far more write than the plain read-only badge implies.
//
// The "Privacy lock" dial here is a simple on/off + lock-mode selector on the
// workspace_policy dial mechanism — a distinct, simpler control from the
// full Privacy Lock drawer (app/src/panels/lock.js, workspace_lock: redaction
// floor, seal/unseal, reclassify, backend setup). The two are not
// duplicates: this dial toggles the protection and its strictness tier; the
// Lock drawer works the semantic-scan machinery underneath it.
Patchbay.register("protections", {
  async open(ctx) {
    const { host, tool } = ctx;
    const { esc, escA } = ctx.ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Turning a protection <b>on</b> is immediate. Turning one <b>off</b> records who accepted it and why. " +
      "The server enforces these; this sets them.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "psout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to see its policy</div>';
        return true;
      }
      let s;
      try {
        s = await tool("workspace_policy", { op: "snapshot", params: { folder_context: ctx.workspace.path } });
      } catch (e) {
        const b = '<div class="finding bad"><span class="ttl">Could not load policy</span>' + esc((e && e.message) || "failed") + "</div>";
        if (out.querySelector(".psrow")) out.insertAdjacentHTML("afterbegin", b); else out.innerHTML = b;
        return false;
      }
      let jp = {};
      try { jp = await tool("workspace_policy", { op: "juris_packs", params: { folder_context: ctx.workspace.path } }); } catch (_) { }   // current jurisdiction-pack stack (read)

      const dialRow = (key, label, active, detail) => '<div class="psrow"><div style="display:flex;align-items:center;gap:8px"><b style="font-size:12px">' + esc(label) + '</b><span class="' + (active ? "pson" : "psoff") + '" style="font-size:11px">' + (active ? "on" : "off") + '</span><span style="flex:1"></span>' + (active ? '<button class="psbtn" data-off="' + key + '">Turn off…</button>' : '<button class="psbtn" data-on="' + key + '">Turn on</button>') + "</div>" + (detail ? '<div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:4px">' + detail + "</div>" : "") + '<div data-form="' + key + '" style="margin-top:8px"></div></div>';
      let h = "";
      h += dialRow("lock", "Privacy lock", !!s.lock_is_active, "mode: " + esc(s.lock_mode || "—") + " · seals sensitive data before it leaves");
      h += dialRow("oversight", "Human oversight", !!s.oversight_is_active, "default level: " + esc(s.oversight_default_level || "—"));
      h += '<div class="psrow"><b style="font-size:12px">Oversight level</b><div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin:3px 0 0">how much a person is in the loop by default — autonomous (least) → manual (most)</div><div class="pslevels" role="group" aria-label="oversight level">' + _OV_ORDER.map((l) => '<button class="pslevel' + (s.oversight_default_level === l ? " on" : "") + '" data-ovl="' + esc(l) + '" aria-pressed="' + (s.oversight_default_level === l) + '" aria-label="set oversight level to ' + esc(l) + '">' + esc(l) + "</button>").join("") + "</div></div>";
      const curMode = s.lock_mode || "clean_room_with_algo";
      h += '<div class="psrow"><b style="font-size:12px">Lock mode</b><div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin:3px 0 0">how the privacy lock seals data — clean room + algorithm (most protective) → off (least). Loosening is recorded.</div><div class="pslevels" role="group" aria-label="lock mode">' + _LOCK_MODES.map((m) => '<button class="pslevel' + (curMode === m ? " on" : "") + '" data-lm="' + esc(m) + '" aria-pressed="' + (curMode === m) + '" aria-label="set lock mode to ' + esc(_LOCK_LABEL[m] || m) + '">' + esc(_LOCK_LABEL[m] || m) + "</button>").join("") + '</div><div data-form="lockmode" style="margin-top:8px"></div></div>';
      const tdm = !!s.ai_training_optout;
      h += '<div class="psrow"><div style="display:flex;align-items:center;gap:8px"><b style="font-size:12px">AI-training opt-out</b><span class="' + (tdm ? "pson" : "psoff") + '" style="font-size:11px">' + (tdm ? "on" : "off") + '</span><span style="flex:1"></span><button class="psbtn" data-tdm="' + (tdm ? "0" : "1") + '">' + (tdm ? "Allow training…" : "Opt out") + "</button></div>" + '<div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:4px">declare this folder’s content must not be used to train models</div>' + (tdm ? '<div style="margin-top:6px"><button class="psbtn" data-tdmdecl="1" title="write a machine-readable ai-training.txt reservation file into this folder">Write reservation file…</button></div>' : "") + "</div>";
      // Jurisdiction-pack stack (declare the regulatory packs this folder claims,
      // audited) + delegate signing authority human→human (no competence; recorded).
      const PSI = "width:100%;margin-top:4px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px";
      const own = (jp && jp.own) || [];
      h += '<div class="psrow"><b style="font-size:12px">Jurisdiction packs</b><div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin:3px 0">the regulatory pack stack this folder declares (audited). ' + (own.length ? "current: <b>" + esc(own.join(", ")) + "</b>" : "none declared") + '</div><input id="jpStack" aria-label="jurisdiction pack ids, comma-separated" placeholder="reference packs e.g. eu-base, de-overlay (blank = clear)" value="' + escA(own.join(",")) + '" style="' + PSI + '"><button class="psbtn" data-jpset="1" style="margin-top:5px">Set packs</button></div>';
      h += '<div class="psrow"><b style="font-size:12px">Delegate signing</b><div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin:3px 0">grant one human signer authority to record sign-offs on another’s behalf — carries no competence; recorded.</div><input id="dsFrom" aria-label="delegator party id" placeholder="from party (the delegator, an active human)" style="' + PSI + '"><input id="dsTo" aria-label="delegate party id" placeholder="to party (the delegate)" style="' + PSI + '"><button class="psbtn" data-dsdel="1" style="margin-top:5px;border-color:#e6b483;color:#e6b483">Delegate signing…</button></div>';
      h += '<div class="psrow" id="psparties"><b style="font-size:12px">Parties</b><div class="ro" style="font-size:11px;color:var(--txt-dim)">loading…</div></div>';
      out.innerHTML = h;
      const reload = () => load();
      const freezeP = () => out.querySelectorAll(".psbtn,.pslevel,[data-tdm]").forEach((b) => { b.disabled = true; });   // no rapid-click double-write while a change is in flight
      out.querySelectorAll("[data-on]").forEach((b) => b.addEventListener("click", async () => {
        freezeP(); let msg;
        try { await tool("workspace_policy", { op: "enable", params: { folder_context: ctx.workspace.path, dial: b.dataset.on } }); await reload(); msg = b.dataset.on + " protection turned on."; }
        catch (e) { await reload(); msg = "Could not turn on: " + ((e && e.message) || "failed"); }
        announce(msg);
      }));
      out.querySelectorAll("[data-off]").forEach((b) => b.addEventListener("click", () => {
        const key = b.dataset.off, form = out.querySelector('[data-form="' + key + '"]'); if (!form) return; b.disabled = true;
        form.innerHTML = '<div class="ro" style="font-size:10.5px;color:#e6b483;margin-bottom:4px">Turning this off loosens protection — it’s recorded.</div><input type="text" id="acc_' + key + '" aria-label="accepted by (your name)" placeholder="accepted by (your name)" style="width:100%;margin-bottom:5px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px"><input type="text" id="rsn_' + key + '" aria-label="reason for turning off this protection" placeholder="reason" style="width:100%;margin-bottom:5px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px"><div style="display:flex;gap:6px"><button class="del" id="cfm_' + key + '" aria-label="Confirm turning off the ' + esc(key) + ' protection (recorded)" style="flex:1">Confirm — turn off</button><button class="psbtn" id="cnl_' + key + '" aria-label="Cancel — keep the protection on" style="flex:1">Cancel</button></div>';
        const acc = form.querySelector("#acc_" + key); if (acc && acc.focus) { try { acc.focus(); } catch (_) { } }
        form.querySelector("#cnl_" + key).addEventListener("click", () => load());   // restore the on-state + re-enable
        form.querySelector("#cfm_" + key).addEventListener("click", async () => {
          const a = (form.querySelector("#acc_" + key).value || "").trim(), r = (form.querySelector("#rsn_" + key).value || "").trim();
          if (!a || !r) { announce('Both “accepted by” and “reason” are required to turn off a protection.'); return; }
          freezeP(); form.querySelectorAll("button,input").forEach((el) => { el.disabled = true; });
          let msg;
          try { await tool("workspace_policy", { op: "disable", params: { folder_context: ctx.workspace.path, dial: key, accepted_by: a, reason: r } }); await reload(); msg = key + " protection turned off (recorded)."; }
          catch (e) { await reload(); msg = "Could not turn off: " + ((e && e.message) || "failed"); }
          announce(msg);
        });
      }));
      out.querySelectorAll("[data-ovl]").forEach((b) => b.addEventListener("click", async () => {
        const lvl = b.dataset.ovl, cur = s.oversight_default_level; if (lvl === cur) return;
        if (_OV_ORDER.indexOf(lvl) < _OV_ORDER.indexOf(cur) && !confirm('Set oversight to “' + lvl + '”? That is less oversight than “' + cur + '”.')) return;
        freezeP();
        let msg;
        try { await tool("workspace_policy", { op: "set_oversight_level", params: { folder_context: ctx.workspace.path, level: lvl, actor: "app-user" } }); await reload(); msg = "Oversight level set to " + lvl + "."; }
        catch (e) { await reload(); msg = "Could not set level: " + ((e && e.message) || "failed"); }
        announce(msg);
      }));
      const tb = out.querySelector("[data-tdm]"); if (tb) tb.addEventListener("click", async () => {
        const enable = tb.dataset.tdm === "1";
        if (!enable && !confirm("Allow this folder’s content to be used for AI training? This removes the opt-out.")) return;
        freezeP();
        let msg;
        try { await tool("workspace_policy", { op: "tdm_optout", params: { folder_context: ctx.workspace.path, enabled: enable, actor: "app-user" } }); await reload(); msg = enable ? "Opted out of AI training." : "AI-training opt-out removed."; }
        catch (e) { await reload(); msg = "Could not update: " + ((e && e.message) || "failed"); }
        announce(msg);
      });
      out.querySelectorAll("[data-lm]").forEach((b) => b.addEventListener("click", () => {
        const mode = b.dataset.lm; if (mode === curMode) return;
        const tightening = _LOCK_MODES.indexOf(mode) <= _LOCK_MODES.indexOf(curMode);   // lower index = stricter
        const form = out.querySelector('[data-form="lockmode"]'); if (!form) return;
        if (tightening) {
          freezeP();
          (async () => {
            let msg;
            try { const r = await tool("workspace_policy", { op: "set_lock_mode", params: { folder_context: ctx.workspace.path, mode: mode } }); if (r && r.ok === false) throw new Error(r.error || "refused"); await reload(); msg = "Lock mode set to " + mode + "."; }
            catch (e) { await reload(); msg = "Could not set lock mode: " + ((e && e.message) || "failed"); }
            announce(msg);
          })();
          return;
        }
        out.querySelectorAll("[data-lm]").forEach((x) => { x.disabled = true; });
        form.innerHTML = '<div class="ro" style="font-size:10.5px;color:#e6b483;margin-bottom:4px">Loosening the lock mode to “' + esc(mode) + '” reduces protection — it’s recorded.</div><input type="text" id="acc_lm" placeholder="accepted by (your name)" style="width:100%;margin-bottom:5px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px"><input type="text" id="rsn_lm" placeholder="reason" style="width:100%;margin-bottom:5px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px"><div style="display:flex;gap:6px"><button class="del" id="cfm_lm" style="flex:1">Confirm — loosen</button><button class="psbtn" id="cnl_lm" style="flex:1">Cancel</button></div>';
        const acc = form.querySelector("#acc_lm"); if (acc && acc.focus) { try { acc.focus(); } catch (_) { } }
        form.querySelector("#cnl_lm").addEventListener("click", () => load());
        form.querySelector("#cfm_lm").addEventListener("click", async () => {
          const a = (form.querySelector("#acc_lm").value || "").trim(), r = (form.querySelector("#rsn_lm").value || "").trim();
          if (!a || !r) { announce('Both “accepted by” and “reason” are required to loosen the lock mode.'); return; }
          freezeP(); form.querySelectorAll("button,input").forEach((el) => { el.disabled = true; });
          let msg;
          try { const res = await tool("workspace_policy", { op: "set_lock_mode", params: { folder_context: ctx.workspace.path, mode: mode, accepted_by: a, reason: r } }); if (res && res.ok === false) throw new Error(res.error || "refused"); await reload(); msg = "Lock mode loosened to " + mode + " (recorded)."; }
          catch (e) { await reload(); msg = "Could not loosen: " + ((e && e.message) || "failed"); }
          announce(msg);
        });
      }));
      const td = out.querySelector("[data-tdmdecl]"); if (td) td.addEventListener("click", async () => {
        freezeP();
        let msg;
        try { const r = await tool("workspace_policy", { op: "tdm_declare", params: { folder_context: ctx.workspace.path, actor: "app-user" } }); if (r && r.ok === false) throw new Error(r.error || "failed"); await reload(); msg = "Reservation file written (" + esc((r && r.declaration) || "ai-training.txt") + ")."; }
        catch (e) { await reload(); msg = "Could not write reservation: " + ((e && e.message) || "failed"); }
        announce(msg);
      });
      // Set jurisdiction packs (declare the regulatory stack; blank clears it)
      const jps = out.querySelector("[data-jpset]"); if (jps) jps.addEventListener("click", async () => {
        freezeP();
        const packs = (((out.querySelector("#jpStack") || {}).value) || "").split(",").map((x) => x.trim()).filter(Boolean);
        let msg;
        try { const r = await tool("workspace_policy", { op: "juris_packs", params: { folder_context: ctx.workspace.path, packs: packs, actor: "app-user" } }); if (r && r.ok === false) throw new Error(r.error || "failed"); await reload(); msg = packs.length ? ("Jurisdiction packs set: " + esc(packs.join(", ")) + ".") : "Jurisdiction packs cleared."; }
        catch (e) { await reload(); msg = "Could not set packs: " + ((e && e.message) || "failed"); }
        announce(msg);
      });
      // Delegate signing authority (human→human; carries no competence; recorded)
      const dsd = out.querySelector("[data-dsdel]"); if (dsd) dsd.addEventListener("click", async () => {
        const fr = (((out.querySelector("#dsFrom") || {}).value) || "").trim(), to = (((out.querySelector("#dsTo") || {}).value) || "").trim();
        if (!(fr && to)) { announce("both a delegator and a delegate party are required"); return; }
        if (!confirm('Delegate signing authority from “' + fr + '” to “' + to + '”? ' + to + " will be able to record sign-offs on " + fr + "’s behalf (recorded).")) return;
        freezeP();
        let msg;
        try { const r = await tool("workspace_policy", { op: "delegate_signing", params: { folder_context: ctx.workspace.path, from_party: fr, to_party: to, actor: "app-user" } }); if (r && (r.ok === false || r.error)) throw new Error(r.error || "failed"); await reload(); msg = "Signing delegated: " + esc(to) + " may sign for " + esc(fr) + "."; }
        catch (e) { await reload(); msg = "Could not delegate: " + ((e && e.message) || "failed"); }
        announce(msg);
      });
      try {
        const pl = await tool("workspace_policy", { op: "party_list", params: { folder_context: ctx.workspace.path } });
        const ps = out.querySelector("#psparties");
        if (ps) {
          const arr = Array.isArray(pl) ? pl : (pl.parties || pl.rows || []);
          ps.innerHTML = '<b style="font-size:12px">Parties</b>' + (arr.length ? ('<div style="margin-top:4px">' + arr.map((pp) => '<div style="display:flex;gap:8px;font-size:11px;border-top:1px solid var(--line);padding:3px 0"><span>' + esc(pp.party_id || pp.id || "?") + '</span><span style="color:var(--txt-dim)">' + esc(pp.kind || "") + '</span><span style="flex:1"></span><span class="' + ((pp.status || "active") === "active" ? "pson" : "psoff") + '">' + esc(pp.status || "active") + "</span></div>").join("") + "</div>") : '<div class="ro" style="font-size:11px;color:var(--txt-dim)">none registered</div>');
        }
      } catch (e) {
        const ps = out.querySelector("#psparties"); if (ps) ps.innerHTML = '<b style="font-size:12px">Parties</b><div class="ro" style="font-size:11px;color:var(--bad)">could not load parties: ' + esc((e && e.message) || "failed") + "</div>";
      }
      return true;
    };

    await load();
  },
});
