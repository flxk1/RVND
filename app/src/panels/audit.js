// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Audit panel — the fourth pack entry behind
// docs/loomground-proposals/panel-mount-contract.md, and the first with a
// governed write action alongside its reads: the integrity checks
// (verify_chain, discipline, shadow_scan, overrides, override_recurrence,
// calibration) only read the signed record, but the model-attestation
// battery (baseline / run / admit) is recorded workspace_model writes. The
// manifest declares a custom "reads · attests" badge (panel-mount-contract's
// badge extension) rather than the plain read-only one, since this panel is
// not purely read.
Patchbay.register("audit", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc, escA } = ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "The checks only read this workspace’s tamper-evident record; the " +
      "model-attestation buttons are governed, recorded writes.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "auout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const AIN = "width:100%;background:var(--panel-2);border:1px solid var(--line);color:#fff;border-radius:6px;padding:6px;font-family:inherit;font-size:11px;margin-bottom:5px";

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to audit its record</div>';
        return;
      }
      const card = (t, b, k) => '<div class="finding ' + (k || "info") + '" style="margin-bottom:8px"><span class="ttl">' + t + "</span>" + b + "</div>";
      const get = async (op, extra) => {
        try {
          return await tool("workspace_audit", { op, params: Object.assign({ folder_context: ctx.workspace.path }, extra || {}) });
        } catch (e) {
          return { error: (e && e.message) || "failed" };
        }
      };
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">running checks…</div>';
      const att = tool("workspace_model", { op: "attest_status", params: { folder_context: ctx.workspace.path } })
        .catch((e) => ({ error: (e && e.message) || "failed" }));
      const [vc, disc, shadow, ovs, rec, cal, at] = await Promise.all([
        get("verify_chain"), get("discipline"), get("shadow_scan"), get("overrides"), get("override_recurrence"), get("calibration"), att,
      ]);
      let h = "";
      if (vc.error) h += card("Signed record — could not verify", esc(vc.error), "bad");
      else if (vc.ok) h += card("✓ Signed record intact", esc(vc.total_events || 0) + " events · no broken links, no signature failures", "ok");
      else h += card("✗ Signed record FAILED verification",
        "broken links: " + ((vc.broken_links || []).length) + " · signature failures: " + ((vc.signature_failures || []).length) +
        " · unsigned: " + esc(vc.unsigned_events || 0) + " · malformed: " + esc(vc.malformed_lines || 0), "bad");
      if (disc.error) h += card("Discipline — could not run", esc(disc.error), "warn");
      else h += card(disc.clean ? "✓ Discipline clean" : "⚠ Discipline — " + esc(disc.failures || 0) + " failure(s), " + esc(disc.warnings || 0) + " warning(s)",
        esc(disc.scanned || 0) + " scanned", disc.clean ? "ok" : "warn");
      if (!shadow.error) {
        const hot = (shadow.high_fan_in || shadow.hotspots || shadow.findings || []);
        h += card("Emergent structure", (hot.length ? esc(hot.length) + " high-fan-in node(s) — worth a look" : "none flagged"), "info");
      }
      const ovl = Array.isArray(ovs) ? ovs : (ovs.overrides || []);
      const recl = Array.isArray(rec) ? rec : (rec.flags || rec.recurring || []);
      h += card("Per-step overrides",
        (ovl.length ? esc(ovl.length) + " recorded" + (recl.length ? ' · <b style="color:#e6b483">' + esc(recl.length) + "</b> recurring (worth review)" : "") : "none recorded"), "info");
      if (!cal.error) {
        h += card("Oversight calibration",
          "reuse " + esc(cal.reuse_count || 0) + " · sampled " + esc(cal.sampled || 0) + " · " +
          (cal.responsible ? '<b style="color:#92c4ac">responsible</b>' : '<b style="color:#e6b483">review</b>') +
          (cal.flag ? " · " + esc(cal.flag) : ""), "info");
      }
      // Attestation — the probe battery's recorded outcomes plus its governed
      // writes (a status read never runs a probe; baseline/run/admit are recorded
      // workspace_model ops surfaced on the cards below).
      const atm = (at && !at.error && at.ok !== false) ? (at.models || []) : null;
      if (atm) {
        if (!atm.length) h += card("Model attestation",
          "no probe battery recorded for this workspace — baseline a model to start holding behaviour to a gold set" +
          '<div style="margin-top:6px"><input type="text" id="atbmodel" aria-label="model id to baseline" placeholder="model id" style="' + AIN + '">' +
          '<textarea id="atbprobes" aria-label="gold probes — one per line, as id: input text" placeholder="one probe per line — id: input text" style="' + AIN + ';height:64px;resize:vertical;font-family:IBM Plex Mono,monospace;font-size:10.5px"></textarea>' +
          '<button class="tool" data-atbase aria-label="Baseline — run the probes against the model now and record the gold set" style="width:100%">Baseline — capture the gold set (recorded)</button></div><div data-aterr="base"></div>', "info");
        atm.forEach((m, i) => {
          const r = m.latest_run;
          const drifted = r && (r.verdict === "EXPLAINED_DRIFT" || r.verdict === "UNLOGGED_LEARNING");
          const acts = '<div style="display:flex;gap:6px;margin-top:6px"><button class="tool" data-atrun="' + i + '" aria-label="Run the probe battery against ' + escA(m.model_id) + ' now (recorded)" style="flex:1">Run battery</button>' +
            (drifted ? '<button class="tool" data-atadmit="' + i + '" aria-label="Admit a deliberate change to ' + escA(m.model_id) + ' — note required (recorded)" style="flex:1">Admit change…</button>' : "") +
            '</div><div data-atform="' + i + '"></div><div data-aterr="' + i + '"></div>';
          if (!r) { h += card("Model attestation — " + esc(m.model_id), "baselined (" + esc(m.baselines) + "×) · never run — the gold set exists, behaviour has not been checked against it" + acts, "info"); return; }
          const kind = r.verdict === "PASS" ? "ok" : (r.verdict === "EXPLAINED_DRIFT" ? "warn" : "bad");
          const word = r.verdict === "PASS" ? "✓ behaviour matches its baseline" : (r.verdict === "EXPLAINED_DRIFT" ? "⚠ drift, explained by declared changes" : "✗ UNDECLARED behaviour change");
          let b = word + ' — <q style="font-style:normal">' + esc(r.reason || "") + "</q>";
          if ((r.diverged || []).length) b += '<div style="font-size:10.5px;margin-top:3px">diverged (drift): <span style="font-family:IBM Plex Mono,monospace;font-size:10px">' + esc(r.diverged.join(", ")) + "</span></div>";
          if ((r.unobserved || []).length) b += '<div style="font-size:10.5px;margin-top:3px;color:var(--txt-dim)">unobserved (coverage gap, not drift): <span style="font-family:IBM Plex Mono,monospace;font-size:10px">' + esc(r.unobserved.join(", ")) + "</span></div>";
          b += '<div style="font-size:10px;color:var(--txt-dim);margin-top:3px">' + esc(r.probe_count || 0) + " probes · admitted changes in window: " + esc(r.admitted_learning_events || 0) + " · model file " + esc(r.hash_state || "unknown") + " · " + esc(String(r.at || "").slice(0, 19)) + (kind === "bad" ? " · holding the agent is a governed action (Run state / All-Stop)" : "") + "</div>";
          h += card("Model attestation — " + esc(m.model_id), b + acts, kind);
        });
      }
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:2px">Per-run guards (completeness · variety · accountability · sampling) are checked on a run, not here.</div>';
      out.innerHTML = h;
      if (!atm) return;
      const aterr = (k, msg) => { const d = out.querySelector('[data-aterr="' + k + '"]'); if (d) d.innerHTML = '<div class="ro" style="font-size:11px;color:var(--bad)">' + esc(msg) + "</div>"; };
      out.querySelectorAll("[data-atrun]").forEach((b) => b.addEventListener("click", async () => {
        const i = b.dataset.atrun, mid = (atm[i] || {}).model_id; b.disabled = true;
        let r; try { r = await tool("workspace_model", { op: "attest_run", params: { folder_context: ctx.workspace.path, model_id: mid, actor: "app-user" } }); } catch (e) { r = { ok: false, error: (e && e.message) || "failed" }; }
        if (!r || r.ok === false) { b.disabled = false; aterr(i, "Could not run the battery: " + ((r && r.error) || "refused")); return; }
        announce("Battery run recorded for " + mid + " — verdict " + ((r && r.verdict) || "?") + "."); load();
      }));
      out.querySelectorAll("[data-atadmit]").forEach((b) => b.addEventListener("click", () => {
        const i = b.dataset.atadmit, mid = (atm[i] || {}).model_id, form = out.querySelector('[data-atform="' + i + '"]'); if (!form) return; b.disabled = true;
        form.innerHTML = '<div class="ro" style="font-size:10.5px;color:#e6b483;margin:4px 0">Admitting declares a deliberate model change so the next run reconciles it — the note is required, and it’s recorded.</div><input type="text" id="atnote_' + i + '" aria-label="what changed, deliberately" placeholder="what changed, deliberately" style="' + AIN + '"><div style="display:flex;gap:6px"><button class="tool" id="atcfm_' + i + '" style="flex:1">Confirm — admit (recorded)</button><button class="psbtn" id="atcnl_' + i + '" style="flex:1">Cancel</button></div>';
        const nt = form.querySelector("#atnote_" + i); if (nt && nt.focus) { try { nt.focus(); } catch (_) { } }
        form.querySelector("#atcnl_" + i).addEventListener("click", () => load());
        form.querySelector("#atcfm_" + i).addEventListener("click", async () => {
          const note = ((form.querySelector("#atnote_" + i) || {}).value || "").trim();
          if (!note) { aterr(i, "a note is required — what changed, deliberately"); return; }
          form.querySelectorAll("button,input").forEach((el) => { el.disabled = true; });
          let r; try { r = await tool("workspace_model", { op: "attest_admit", params: { folder_context: ctx.workspace.path, model_id: mid, note, actor: "app-user" } }); } catch (e) { r = { ok: false, error: (e && e.message) || "failed" }; }
          if (!r || r.ok === false) { form.querySelectorAll("button,input").forEach((el) => { el.disabled = false; }); aterr(i, "Could not admit the change: " + ((r && r.error) || "refused")); return; }
          announce("Admitted change recorded for " + mid + " — the next battery run reconciles against it."); load();
        });
      }));
      const bb = out.querySelector("[data-atbase]");
      if (bb) bb.addEventListener("click", async () => {
        const mid = (((host.querySelector("#atbmodel")) || {}).value || "").trim();
        const probes = (((host.querySelector("#atbprobes")) || {}).value || "").split("\n").map((l) => l.trim()).filter(Boolean)
          .map((l) => { const k = l.indexOf(":"); return k > 0 ? { id: l.slice(0, k).trim(), input: l.slice(k + 1).trim() } : null; });
        if (!mid) { aterr("base", "a model id is required"); return; }
        if (!probes.length || probes.some((p) => !p || !p.id || !p.input)) { aterr("base", "probes must be one per line, as “id: input text”"); return; }
        bb.disabled = true;
        let r; try { r = await tool("workspace_model", { op: "attest_baseline", params: { folder_context: ctx.workspace.path, model_id: mid, probes, actor: "app-user" } }); } catch (e) { r = { ok: false, error: (e && e.message) || "failed" }; }
        if (!r || r.ok === false) { bb.disabled = false; aterr("base", "Could not capture the baseline: " + ((r && r.error) || "refused")); return; }
        announce("Baseline captured for " + mid + " — " + ((r && r.probe_count) || probes.length) + " probe(s), recorded."); load();
      });
    };

    await load();
  },
});
