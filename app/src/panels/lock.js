// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Privacy Lock panel — behind
// docs/loomground-proposals/panel-mount-contract.md, and (with audit and
// lens) one of the write panels: reading the redaction floor and the lock's
// decision history (workspace_lock "threshold_get" / "audit_query") takes no
// write, but setting the floor, sealing/unsealing for reading, and
// reclassifying every stored pair are governed, recorded writes. Raising the
// floor and sealing are direct (they tighten protection); lowering the
// floor, unsealing and reclassifying loosen or rewrite the record, so they
// confirm first (lowering also requires a typed reason). The drawer also
// carries the backend-setup wizard CTA (workspace_lock "setup") shown when
// the Tier C semantic scan backend has not been configured — a sub-flow of
// this same drawer, not a separate panel. The manifest declares a custom
// "read + WRITE: floor/seal/reclassify" badge since this panel is not purely
// read.
Patchbay.register("lock", {
  async open(ctx) {
    const { host, tool } = ctx;
    const { esc, escA } = ctx.ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "The minimisation gate and the reading seal. Re-locking and raising the floor are direct; " +
      "unsealing for reading and lowering the floor loosen protection, so they ask to confirm and are " +
      "recorded. The server enforces; this drawer requests.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "lkout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to read its privacy lock</div>';
        return;
      }
      const card = (t, b, k) => '<div class="finding ' + (k || "info") + '" style="margin-bottom:8px"><span class="ttl">' + t + "</span>" + b + "</div>";
      const get = async (op, extra) => {
        try {
          return await tool("workspace_lock", { op, params: Object.assign({ folder_context: ctx.workspace.path }, extra || {}) });
        } catch (e) {
          return { error: (e && e.message) || "failed" };
        }
      };
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">reading the seal…</div>';
      // audit_query is itself audited — it demands a non-empty reason that would survive a regulator's read
      const [thr, aud, st] = await Promise.all([get("threshold_get"), get("audit_query", { reason_for_query: "privacy-lock drawer — read lock decision history" }), get("setup_status")]);
      let h = ""; let curThr = 0;
      // Backend onboarding — the CLI wizard, reachable from the drawer. The server
      // guards the loosening path (real → mock needs accepted_by + reason).
      if (st && !st.error && st.ok !== false) {
        if (st.configured === false) {
          h += '<div class="finding warn" style="margin-bottom:8px"><span class="ttl">Semantic scan backend — not set up</span>'
            + "The lock's deterministic gates run regardless; the Tier C semantic scan uses a permissive mock until a real local model is configured."
            + '<details id="lksetupbox" style="margin-top:6px"><summary style="cursor:pointer;font-size:11px" id="lksetupcta">Set up — run the onboarding wizard</summary>'
            + '<div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:4px">Leave the backend empty to accept the wizard\'s recommendation, or give <span class="path">mock</span> / <span class="path">llama_cpp:/path/model.gguf</span>. The smoke test probes the chosen backend with known PII before the config is kept.</div>'
            + '<input type="text" id="lksubackend" placeholder="backend (empty = recommended)" style="width:100%;margin-top:4px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px">'
            + '<input type="text" id="lksuaudit" placeholder="audit log path (optional)" style="width:100%;margin-top:4px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px">'
            + '<label style="display:block;font-size:10.5px;color:var(--txt-dim);margin-top:4px"><input type="checkbox" id="lksuskip"> skip the smoke test</label>'
            + '<button class="tool" id="lksetupbtn" style="margin-top:5px;width:100%">Run setup</button>'
            + '<div id="lksuout"></div></details></div>';
        } else {
          h += '<div class="finding info" style="margin-bottom:8px"><span class="ttl">Semantic scan backend</span><span class="path">' + esc(st.backend_spec || "?") + "</span> · configured" + (st.audit_log_path ? ' · log <span class="path">' + esc(st.audit_log_path) + "</span>" : "") + "</div>";
        }
      }
      // Redaction floor — the server-configured confidence floor below which findings are dropped.
      // Rendered as a discrete state / plain server value, NEVER a 0-1 dial or percentage bar.
      if (thr.error) h += card("Redaction floor — could not read", esc(thr.error), "warn");
      else if (thr.ok === false) h += card("Redaction floor — could not read", esc(thr.error || "unavailable"), "warn");
      else {
        const t = Number(thr.threshold || 0); curThr = t;
        h += card("Redaction floor", (t <= 0 ? '<b style="color:#aab0bd">no filter</b> — every finding the gate detects is surfaced' : 'confidence floor <b>' + esc(thr.threshold) + "</b> — findings below this are dropped by the gate"), "info");
      }
      // Lock decision history — what the minimisation gate has decided (allow / strip / refuse), as recorded.
      if (aud.error) h += card("Lock decisions — could not read", esc(aud.error), "warn");
      else if (aud.ok === false) h += card("Lock decisions — could not read", esc(aud.error || "refused"), "warn");
      else {
        const entries = aud.entries || []; const total = aud.total_lines_in_log || 0; const path = aud.audit_log_path;
        if (!path && aud.note) h += card("Lock decisions", esc(aud.note), "info");
        else if (!entries.length) h += card("Lock decisions", (aud.note ? esc(aud.note) : "the lock has recorded no decisions for this folder yet"), "info");
        else h += card("Lock decisions", esc(entries.length) + " recent of " + esc(total) + ' recorded · log <span class="path">' + esc(path) + "</span>", "info");
      }
      // ---- writes: floor (raise=direct, lower=confirm+reason) · seal (direct) · unseal (confirm) · reclassify (confirm) ----
      const inp = 'style="width:100%;margin-top:4px;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px"';
      h += '<details style="margin-top:8px"><summary style="cursor:pointer;font-size:11px;color:var(--txt-dim)">+ set the redaction floor</summary>'
        + '<div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:4px">A confidence value the server stores (0 = surface everything; clamped 0–1). Findings below it are dropped. Raising it redacts more (direct); lowering it lets more through (asks to confirm + records a reason).</div>'
        + '<input type="number" id="lkfloor" min="0" max="1" step="0.05" value="' + escA(String(curThr)) + '" ' + inp + '>'
        + '<input type="text" id="lkfloorreason" placeholder="reason (required when lowering)" ' + inp + '>'
        + '<button class="tool" id="lkfloorbtn" style="margin-top:5px;width:100%">Set redaction floor</button></details>';
      h += '<details style="margin-top:6px"><summary style="cursor:pointer;font-size:11px;color:var(--txt-dim)">+ seal / unseal · reclassify</summary>'
        + '<div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:4px">Seal drops the in-memory reading key now (safe, immediate). Unseal opens an at-rest-sealed workspace for reading and needs its passphrase — it loosens access, so it confirms. Reclassify re-runs the gate over every stored pair and rewrites them.</div>'
        + '<button class="tool" id="lksealbtn" style="margin-top:5px;width:100%">Seal — re-lock reading now</button>'
        + '<input type="password" id="lkpass" placeholder="passphrase (to unseal for reading)" autocomplete="off" ' + inp + '>'
        + '<button class="tool" id="lkunsealbtn" style="margin-top:5px;width:100%">Unseal for reading</button>'
        + '<button class="tool" id="lkreclassbtn" style="margin-top:8px;width:100%">Reclassify all pairs</button></details>';
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:4px">The server enforces every decision; this drawer requests and surfaces the result. Querying lock decisions is itself written to the audit chain it reads.</div>';
      out.innerHTML = h;
      const fb = out.querySelector("#lkfloorbtn"); if (fb) fb.addEventListener("click", () => lockSetFloor(curThr));
      const sb = out.querySelector("#lksealbtn"); if (sb) sb.addEventListener("click", lockSeal);
      const ub = out.querySelector("#lkunsealbtn"); if (ub) ub.addEventListener("click", lockUnseal);
      const rb = out.querySelector("#lkreclassbtn"); if (rb) rb.addEventListener("click", lockReclassify);
      const su = out.querySelector("#lksetupbtn"); if (su) su.addEventListener("click", lockSetup);
    };

    const lockSetup = async () => {
      const g = (id) => host.querySelector("#" + id);
      const res = g("lksuout"); if (!res) return;
      const params = {
        backend_spec: ((g("lksubackend") || {}).value || "").trim(),
        audit_log_path: ((g("lksuaudit") || {}).value || "").trim(),
        skip_smoke_test: !!(g("lksuskip") && g("lksuskip").checked),
      };
      const btn = g("lksetupbtn"); if (btn) btn.disabled = true;
      res.innerHTML = '<div class="ro" style="font-size:10.5px;color:var(--txt-dim);margin-top:4px">running the wizard…</div>';
      let r; try { r = await tool("workspace_lock", { op: "setup", params }); } catch (e) { r = { error: (e && e.message) || "failed" }; }
      if (btn) btn.disabled = false;
      if (r.error || r.ok === false) { res.innerHTML = '<div class="finding warn" style="margin-top:6px"><span class="ttl">Setup refused</span>' + esc(r.error || "the wizard did not complete") + "</div>"; return; }
      const smoke = (r.smoke_test_passed === true) ? "passed" : (r.smoke_test_passed === false ? "failed — the wizard fell back rather than keep a broken backend" : "skipped");
      res.innerHTML = '<div class="finding info" style="margin-top:6px"><span class="ttl">Setup complete</span>backend <span class="path">' + esc(r.backend_spec || "?") + "</span> · smoke test " + esc(smoke)
        + (r.transcript ? '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:4px;white-space:pre-wrap;max-height:140px;overflow:auto">' + esc(r.transcript) + "</div>" : "") + "</div>";
      announce("Privacy-lock backend configured: " + (r.backend_spec || "?") + ".");
      setTimeout(load, 900);   // refresh — the CTA card becomes the configured card
    };

    /* ---- Privacy Lock write handlers — server decides + records; tighten direct, loosen confirm+reason ---- */
    const lockSetFloor = async (prev) => {
      if (!ctx.workspace.path) return;
      const v0 = Number((host.querySelector("#lkfloor") || {}).value); if (!isFinite(v0)) { announce("the floor must be a number 0–1"); return; }
      const v = Math.max(0, Math.min(1, v0));
      const reason = ((host.querySelector("#lkfloorreason") || {}).value || "").trim();
      if (v < Number(prev || 0)) {
        if (!reason) { announce("lowering the floor surfaces more — a reason is required"); return; }
        if (!confirm("Lower the redaction floor from " + (prev || 0) + " to " + v + "? Less is redacted — more passes through to readers. Recorded.")) return;
      }
      let msg;
      try {
        const r = await tool("workspace_lock", { op: "threshold_set", params: { folder_context: ctx.workspace.path, threshold: v, actor: "app-user", reason } });
        msg = (r && (r.ok === false || r.error)) ? ("Could not set floor: " + esc(r.error || "failed")) : ("Redaction floor set to " + esc(r.threshold) + " (was " + esc(r.previous) + "), recorded.");
      } catch (e) {
        msg = "Could not set floor: " + ((e && e.message) || "failed");
      }
      try { await load(); } catch (_) { }
      announce(msg);
    };

    const lockSeal = async () => {
      if (!ctx.workspace.path) return;
      let msg;
      try {
        const r = await tool("workspace_lock", { op: "seal", params: { folder_context: ctx.workspace.path } });
        msg = (r && (r.ok === false || r.error)) ? ("Could not seal: " + esc(r.error || "failed")) : "Reading sealed — the in-memory key was " + (r.was_unlocked ? "dropped" : "already absent") + ".";
      } catch (e) {
        msg = "Could not seal: " + ((e && e.message) || "failed");
      }
      try { await load(); } catch (_) { }
      announce(msg);
    };

    const lockUnseal = async () => {
      if (!ctx.workspace.path) return;
      const pass = ((host.querySelector("#lkpass") || {}).value || ""); if (!pass) { announce("unsealing needs the passphrase"); return; }
      if (!confirm("Unseal this workspace for reading? Its memory becomes readable this session, and the action is recorded.")) return;
      let msg;
      try {
        const r = await tool("workspace_lock", { op: "unseal", params: { folder_context: ctx.workspace.path, passphrase: pass, actor: "app-user" } });
        msg = (r && (r.ok === false || r.error)) ? ("Could not unseal: " + esc(r.error || "wrong passphrase or not sealed")) : ("Unsealed — " + esc(r.files || 0) + " file(s) available this session.");
      } catch (e) {
        msg = "Could not unseal: " + ((e && e.message) || "failed");
      }
      const pe = host.querySelector("#lkpass"); if (pe) pe.value = "";
      try { await load(); } catch (_) { }
      announce(msg);
    };

    const lockReclassify = async () => {
      if (!ctx.workspace.path) return;
      if (!confirm("Reclassify every stored pair against the current lock rules? This rewrites the pairs in the signed record.")) return;
      let msg;
      try {
        const r = await tool("workspace_lock", { op: "reclassify", params: { folder_context: ctx.workspace.path } });
        msg = (r && (r.ok === false || r.error)) ? ("Could not reclassify: " + esc(r.error || "failed")) : ("Reclassified — " + esc(r.pairs_reclassified || 0) + " updated, " + esc(r.pairs_already_current || 0) + " current of " + esc(r.pairs_total || 0) + ".");
      } catch (e) {
        msg = "Could not reclassify: " + ((e && e.message) || "failed");
      }
      try { await load(); } catch (_) { }
      announce(msg);
    };

    await load();
  },
});
