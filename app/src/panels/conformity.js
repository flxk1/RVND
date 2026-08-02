// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Conformity panel — the third pack entry behind
// docs/loomground-proposals/panel-mount-contract.md. Read-only projections
// of the signed log: it declares governance evidence, it does not certify
// compliance, and legal labels are attributed to the chosen regime, not
// asserted. This bundle calls only workspace_conformity's read ops
// (evidence_pack, oversight_attestation, trigger_map, drift_report,
// risk_register, threat_model).
Patchbay.register("conformity", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc } = ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Read-only projections of the signed log. This <b>declares governance " +
      "— it does not certify compliance.</b> Legal labels are <i>attributed</i> " +
      "to the chosen regime, not asserted.";
    host.appendChild(intro);

    const reg_label = document.createElement("label");
    reg_label.style.cssText = "font-size:11px;color:var(--txt-dim);display:flex;align-items:center;gap:6px;margin-bottom:8px";
    reg_label.innerHTML =
      'Reference regime <select id="cfreg" style="background:var(--bg);color:var(--txt);' +
      'border:1px solid var(--line);border-radius:6px;padding:3px 6px;font-size:11px">' +
      '<option value="">neutral (no statute cited)</option>' +
      '<option value="eu-ai-act">EU AI Act (attributed labels)</option></select>';
    host.appendChild(reg_label);

    const out = document.createElement("div");
    out.id = "cfout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const load = async () => {
      if (!ctx.workspace.path) {
        out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to project its evidence</div>';
        return;
      }
      const regsel = host.querySelector("#cfreg");
      const reg = regsel ? regsel.value : "";
      const card = (t, b, note) =>
        '<div class="finding info" style="margin-bottom:8px"><span class="ttl">' + t + "</span>" + b +
        (note ? '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:3px">' + esc(note) + "</div>" : "") + "</div>";
      const get = async (op) => {
        try {
          const p = { folder_context: ctx.workspace.path };
          if (reg) p.regime = reg;
          return await tool("workspace_conformity", { op, params: p });
        } catch (e) {
          return { error: (e && e.message) || "failed" };
        }
      };
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">projecting…</div>';
      const [ev, ov, tm, dr, rr, th] = await Promise.all([
        get("evidence_pack"), get("oversight_attestation"), get("trigger_map"),
        get("drift_report"), get("risk_register"), get("threat_model"),
      ]);
      const basis = (r) => (r && r.basis ? "attributed to " + esc(typeof r.basis === "string" ? r.basis : JSON.stringify(r.basis)) : "");
      let h = "";
      if (ev.error) h += card("Evidence pack — could not project", esc(ev.error));
      else h += card("Evidence pack",
        (ev.chain && ev.chain.ok ? '<b style="color:#92c4ac">record intact</b>' : '<b style="color:#e6b483">record check failed</b>') +
        " · " + esc((ev.records || []).length) + " records · " + Object.keys(ev.counts_by_kind || {}).length + " event kinds", basis(ev));
      if (!ov.error) h += card("Oversight attestation",
        (ov.attested ? '<b style="color:#92c4ac">attested</b>' : '<b style="color:#e6b483">not attested</b>') +
        " · " + esc((ov.determinations || []).length) + " determinations · " + esc((ov.conditional_releases || []).length) +
        " conditional · " + esc((ov.bypassed_events || []).length) + " bypassed", ov.statement || basis(ov));
      if (!tm.error) h += card("Trigger map",
        esc((tm.actions || []).length) + " external action(s) · " + esc((tm.instruments_union || []).length) + " instrument(s) activated" +
        ((tm.operator_questions || []).length ? ' · <b style="color:#e6b483">' + esc(tm.operator_questions.length) + " operator question(s)</b>" : ""), basis(tm));
      if (!dr.error) h += card("Drift report",
        esc((dr.baselines || []).length) + " baseline(s) · " +
        ((dr.open_findings || []).length ? '<b style="color:#e6b483">' + esc(dr.open_findings.length) + " open finding(s)</b>" : "no open findings"), basis(dr));
      if (!rr.error) h += card("Risk register",
        'posture <b>' + esc(rr.posture || "—") + "</b> · oversight " + esc(typeof rr.oversight === "string" ? rr.oversight : JSON.stringify(rr.oversight || "—")) +
        " · " + esc((rr.observed_actions || []).length) + " observed action(s)", basis(rr));
      if (!th.error) h += card("Threat model",
        esc((th.categories || []).length) + " threat categor" + ((th.categories || []).length === 1 ? "y" : "ies") + " covered by tests", basis(th));
      h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:2px">' +
        (reg ? "Labels attributed to <b>" + esc(reg) + "</b>. " : "No statute cited (neutral). ") +
        "Rvnd produces the evidence the articles ask for; it does not judge that you meet them.</div>";
      out.innerHTML = h;
    };

    reg_label.querySelector("#cfreg").addEventListener("change", load);
    await load();
  },
});
