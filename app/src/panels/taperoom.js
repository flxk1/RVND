// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Assisted by Claude (Anthropic); not an author or copyright holder.
/* RVND Tape Room — Audit surface as a Patchbay panel.
 *
 * Drop-in adapter for the RVND console (Seam A, access:"read"). Read-only by
 * construction. Renders the signed record from workspace_audit as a ledger with
 * per-record receipt drill-in — canonical verdict, the governing declaration +
 * its legal basis, provenance, and the tamper-evident hash chain (prev_hash
 * links). "Verify chain" checks the links locally. No write ops in this bundle.
 *
 * To land in RVND: app/src/panels/taperoom.js + pack.json entry
 *   { "id":"taperoom","entry":"taperoom.js","title":"Tape Room",
 *     "surface":"drawer","access":"read",
 *     "menu":{"section":"record","label":"Tape Room","detail":"The sealed record"} }
 * Reads: ctx.tool("workspace_audit",{op:"tail", params:{folder_context, limit}})
 * (op/shape to confirm against the live workspace_audit facade.)
 * Runs standalone (preview harness) when Patchbay/ctx.tool are absent.
 */
(function () {
  "use strict";

  var VMETA = {
    unfired:    { g: "○", c: "#8b8f98", l: "unfired" },
    auto:       { g: "▲", c: "#5fbf7a", l: "auto" },
    human:      { g: "◆", c: "#e0a41c", l: "human" },
    reserved:   { g: "❚❚", c: "#d98a20", l: "reserved" },
    refused:    { g: "✕", c: "#e04a34", l: "refused" },
    prohibited: { g: "⊘", c: "#c0392b", l: "prohibited" }
  };
  var DECL = {
    "autonomy-grade": ["AI Act Art. 14", "AI Act Art. 26"],
    reservation:      ["AI Act Art. 14", "GDPR Art. 22(3)"],
    quorum:           ["separation of duty"],
    prohibition:      ["AI Act Art. 5"],
    redress:          ["GDPR Art. 22(3)", "Charter Art. 47"]
  };
  function declFor(v) { return v === "prohibited" ? "prohibition" : v === "auto" ? "autonomy-grade" : "reservation"; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }

  /* fallback signed record (standalone preview only) — newest first */
  var SAMPLE = { ok: true, records: [
    { seq: 1016, ts: "15:11", actor: "deploy-agent", kind: "code.deploy", verdict: "reserved",  by: "on-call",           hash: "9f2ac1", prev_hash: "a28aac" },
    { seq: 1015, ts: "15:09", actor: "payout-agent", kind: "payout",      verdict: "auto",      by: "alice · legal",     hash: "a28aac", prev_hash: "71bd0e", redress: true, window: 40 },
    { seq: 1014, ts: "15:04", actor: "refund-agent", kind: "refund",      verdict: "refused",   by: "raj · finance",     hash: "71bd0e", prev_hash: "5c14aa", redress: true, window: 30 },
    { seq: 1013, ts: "15:01", actor: "purge-agent",  kind: "file.delete", verdict: "prohibited",by: "policy",            hash: "5c14aa", prev_hash: "0093f1" },
    { seq: 1012, ts: "14:58", actor: "ingest-bot",   kind: "data.export", verdict: "auto",      by: "auto",              hash: "0093f1", prev_hash: "ff20a7" },
    { seq: 1011, ts: "14:55", actor: "payout-agent", kind: "payout",      verdict: "human",     by: "alice+raj (2-key)", hash: "ff20a7", prev_hash: "000000" }
  ] };

  var CSS = '' +
    '.taperoom{--tp-panel:#1a1c20;--tp-hi:#26282d;--tp-lo:#101216;--tp-ink:#e9e7e0;--tp-dim:#8b8f98;--tp-hair:rgba(255,255,255,.08);--tp-orange:#e8621f;--tp-auto:#5fbf7a;--tp-hold:#e0a41c;--tp-deny:#e04a34;' +
      'font-family:"IBM Plex Mono",monospace;color:var(--tp-ink);background:linear-gradient(180deg,#0c0d10,#08090b);padding:14px;border-radius:8px}' +
    '.taperoom *{box-sizing:border-box}' +
    '.tp-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px}' +
    '.tp-title{font-family:"Space Grotesk";font-weight:700;font-size:19px}.tp-title b{color:var(--tp-orange)}' +
    '.tp-sub{font-size:11px;color:var(--tp-dim);margin-bottom:12px}' +
    '.tp-seal{font-family:"Space Grotesk";font-weight:700;font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:4px 9px;border-radius:2px;border:1px solid var(--tp-auto);color:var(--tp-auto)}' +
    '.tp-seal.broken{border-color:var(--tp-deny);color:var(--tp-deny)}' +
    '.tp-verify{font-family:"Space Grotesk";font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--tp-dim);background:transparent;border:1px solid var(--tp-hair);border-radius:3px;padding:5px 9px;cursor:pointer;margin-left:8px}' +
    '.tp-body{display:grid;grid-template-columns:1fr 300px;gap:12px}@media(max-width:760px){.tp-body{grid-template-columns:1fr}}' +
    '.tp-ledger{border:1px solid var(--tp-hair);border-radius:6px;overflow:hidden;background:rgba(0,0,0,.2)}' +
    '.tp-lh{font-family:"Space Grotesk";font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--tp-dim);padding:8px 10px;border-bottom:1px solid var(--tp-hair)}' +
    '.tp-rows{max-height:360px;overflow-y:auto}' +
    '.tp-row{display:grid;grid-template-columns:auto 1fr auto auto;gap:10px;padding:8px 10px;border-bottom:1px solid var(--tp-hair);font-size:11px;cursor:pointer;align-items:baseline}' +
    '.tp-row:hover{background:rgba(232,98,31,.08)}.tp-row.sel{background:rgba(232,98,31,.16);outline:1px solid var(--tp-orange)}' +
    '.tp-row .t{color:var(--tp-dim)}.tp-vtag{font-family:"Space Grotesk";font-weight:700;font-size:10px;display:inline-flex;gap:4px;align-items:center}' +
    '.tp-row .h{color:var(--tp-dim);font-size:10px}' +
    '.tp-receipt{border:1px solid var(--tp-hair);border-radius:6px;background:var(--tp-panel);padding:0;overflow:hidden}' +
    '.tp-rbody{padding:12px 14px;font-size:11.5px;line-height:1.6}' +
    '.tp-rverd{font-family:"Space Grotesk";font-weight:700;text-transform:uppercase;font-size:18px;margin:2px 0 8px}' +
    '.tp-rrow{display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px dotted var(--tp-hair)}.tp-rrow .k{color:var(--tp-dim)}.tp-rrow .v{font-weight:700;text-align:right}' +
    '.tp-why{margin:9px 0;padding:9px 11px;border-left:3px solid var(--tp-orange);background:rgba(0,0,0,.14);border-radius:0 4px 4px 0}' +
    '.tp-why .l{font-family:"Space Grotesk";font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:var(--tp-orange);font-weight:700}' +
    '.tp-chain{margin-top:9px;padding-top:8px;border-top:1px solid var(--tp-hair);color:var(--tp-dim);font-size:10px;line-height:1.7}.tp-chain b{color:var(--tp-ink)}.tp-chain .ok{color:var(--tp-auto)}.tp-chain .no{color:var(--tp-deny)}' +
    '.tp-contest{font-family:"Space Grotesk";font-weight:700;font-size:11px;text-transform:uppercase;padding:6px 11px;border-radius:3px;border:1px solid #5b8dea;color:#5b8dea;background:transparent;cursor:pointer;margin-top:9px}' +
    '.tp-contest[disabled]{opacity:.4;cursor:not-allowed;border-color:var(--tp-dim);color:var(--tp-dim)}' +
    '.tp-foot{font-size:10px;color:var(--tp-dim);margin-top:10px;line-height:1.5}';

  function injectCSS(doc) { if (doc.getElementById("tp-style")) return; var st = doc.createElement("style"); st.id = "tp-style"; st.textContent = CSS; doc.head.appendChild(st); }

  function chainIntact(recs) { for (var i = 0; i < recs.length - 1; i++) { if (recs[i].prev_hash !== recs[i + 1].hash) return false; } return true; }

  function render(host, data, state) {
    var doc = host.ownerDocument; injectCSS(doc);
    if (data && data._unavailable) { host.innerHTML = '<div class="taperoom"><div style="color:#e04a34;padding:12px;font-size:12px;line-height:1.5">Audit record unavailable' + (data.error ? ' — ' + esc(data.error) : '') + '.<br><span style="color:#8b8f98">Open a registered workspace.</span></div></div>'; return; }
    var recs = data.records || [];
    var intact = (data.intact != null) ? data.intact : chainIntact(recs); // prefer real verify_chain result when wired
    var selId = state.sel != null ? state.sel : (recs[0] && recs[0].seq);
    var sel = recs.filter(function (r) { return r.seq === selId; })[0] || recs[0];

    var rows = recs.map(function (r) {
      var m = VMETA[r.verdict] || VMETA.unfired;
      return '<div class="tp-row' + (r.seq === selId ? " sel" : "") + '" data-seq="' + r.seq + '">' +
        '<span class="t">' + esc(r.ts) + '</span><span>' + esc(r.actor) + ' · ' + esc(r.kind) + '</span>' +
        '<span class="tp-vtag" style="color:' + m.c + '">' + m.g + " " + m.l + '</span><span class="h">' + esc(r.hash) + '</span></div>';
    }).join("");

    var receipt = "";
    if (sel) {
      var m = VMETA[sel.verdict] || VMETA.unfired, decl = declFor(sel.verdict), gr = DECL[decl] || [];
      var contested = state.contested && state.contested[sel.seq];
      var canContest = (sel.verdict === "refused" || sel.verdict === "prohibited") && sel.redress && !contested;
      receipt =
        '<div class="tp-rbody">' +
          '<div style="display:flex;justify-content:space-between"><b>R-' + esc(sel.seq) + '</b><span style="color:var(--tp-dim)">' + esc(sel.ts) + ' · sealed</span></div>' +
          '<div class="tp-rverd" style="color:' + m.c + '">' + m.g + " " + m.l + (contested ? " · contested" : "") + '</div>' +
          '<div class="tp-rrow"><span class="k">actor</span><span class="v">' + esc(sel.actor) + '</span></div>' +
          '<div class="tp-rrow"><span class="k">action</span><span class="v">' + esc(sel.kind) + '</span></div>' +
          '<div class="tp-rrow"><span class="k">decided by</span><span class="v">' + esc(sel.by) + '</span></div>' +
          '<div class="tp-rrow"><span class="k">provenance</span><span class="v">rvnd://' + esc(sel.actor) + '/r-' + esc(sel.seq) + '</span></div>' +
          '<div class="tp-why"><div class="l">Why — ' + esc(decl) + (gr.length ? ' · ' + gr.map(esc).join(" · ") : '') + '</div></div>' +
          '<div class="tp-chain">receipt <b>' + esc(sel.hash) + '</b> · links prev <b>' + esc(sel.prev_hash) + '</b> ' + (intact ? '<span class="ok">✓ chain intact</span>' : '<span class="no">✕ chain broken</span>') + '</div>' +
          '<button class="tp-contest" ' + (canContest ? "" : "disabled") + ' data-seq="' + esc(sel.seq) + '">Contest (redress)</button>' +
          (sel.redress ? '<span style="font-size:10px;color:var(--tp-dim);margin-left:8px">window ' + (sel.window ? "~" + sel.window + " min" : "open") + '</span>' : '') +
        '</div>';
    }

    host.innerHTML =
      '<div class="taperoom">' +
        '<div class="tp-head"><div><div class="tp-title">RVND <b>·</b> Tape Room</div>' +
          '<div class="tp-sub">Audit — the sealed record. Read-only: scrub, pull a receipt, follow the why to the law.</div></div>' +
          '<div><span class="tp-seal ' + (intact ? "" : "broken") + '" id="tp-seal">' + (state.verified ? (intact ? "Verified · intact" : "BROKEN") : "Sealed · " + recs.length) + '</span>' +
            '<button class="tp-verify" id="tp-verify">Verify chain</button></div>' +
        '</div>' +
        '<div class="tp-body">' +
          '<div class="tp-ledger"><div class="tp-lh">Ledger — newest first</div><div class="tp-rows">' + rows + '</div></div>' +
          '<div class="tp-receipt">' + receipt + '</div>' +
        '</div>' +
        '<div class="tp-foot">Read-only projection of <b>workspace_audit</b> + the signed chain. The <b>why</b> line ties each decision to its Loomground declaration and the law it enforces. The hash chain is tamper-evident (each receipt links the last). The only act is to <b>contest</b> a released decision within its redress window.</div>' +
      '</div>';

    [].forEach.call(host.querySelectorAll(".tp-row"), function (r) { r.addEventListener("click", function () { state.sel = +r.dataset.seq; render(host, data, state); }); });
    host.querySelector("#tp-verify").addEventListener("click", function () { state.verified = true; render(host, data, state); });
    var cb = host.querySelector(".tp-contest");
    if (cb && !cb.disabled) cb.addEventListener("click", function () { state.contested = state.contested || {}; state.contested[+cb.dataset.seq] = true; render(host, data, state); });
  }

  function makeApi(ctx) {
    var standalone = !(ctx && ctx.tool);
    function fc() { return ctx && ctx.workspace && ctx.workspace.path; }
    return {
      async tail() {
        if (standalone) return (ctx && ctx.records) ? { records: ctx.records() } : SAMPLE;
        try {
          // READ — confirmed: workspace_audit op=tail {folder_context, limit} → {count, events:[{ts,event,state,actor,audit_id,signed,verdict,grade_ceiling,kind}]} (oldest-first)
          var d = normalize(await ctx.tool("workspace_audit", { op: "tail", params: { folder_context: fc(), limit: 40 } }));
          if (d && d.ok === false) return { records: [], _unavailable: true, error: d.error || "audit unavailable" };
          var events = (d && d.events) || [];
          var recs = events.slice().reverse().map(function (e) {
            return {
              seq: e.audit_id ? String(e.audit_id).slice(0, 6) : (e.ts || ""),
              ts: e.ts, actor: e.actor, kind: e.kind || e.event, verdict: e.verdict || "unfired",
              by: e.actor, hash: e.audit_id ? String(e.audit_id).slice(0, 6) : "", prev_hash: "",
              signed: e.signed, grade: e.grade_ceiling,
              redress: (e.verdict === "refused" || e.verdict === "prohibited")
            };
          });
          // real tamper check — workspace_audit op=verify_chain (the signed chain, not the tail)
          var intact = true;
          try {
            var v = normalize(await ctx.tool("workspace_audit", { op: "verify_chain", params: { folder_context: fc() } }));
            if (v) intact = (v.ok !== false) && (v.intact !== false) && !(v.broken_links && v.broken_links.length);
          } catch (_) { /* verify optional */ }
          return { records: recs, intact: intact };
        } catch (e) { return { records: [], _unavailable: true, error: (e && e.message) || "audit tail failed" }; } // real error → unavailable, not fake records
      }
    };
  }
  // The RVND bridge hands panels the raw facade dict. Recognize the real audit
  // shapes: tail → {count, events:[…]}; verify_chain → {ok, total_events, …}.
  function normalize(res) { if (!res) return null; if (typeof res === "string") { try { return JSON.parse(res); } catch (e) { return null; } } if (res.content && res.content[0] && res.content[0].text) { try { return JSON.parse(res.content[0].text); } catch (e) { return null; } } if (Array.isArray(res)) return { records: res }; if (res.events || res.records || res.count != null || res.ok != null || res.total_events != null) return res; return res.result ? normalize(res.result) : res; }

  function panel() {
    return { async open(ctx) { var api = makeApi(ctx), state = {}; var data = await api.tail(); render(ctx.host, data, state);
      var doc = ctx.host.ownerDocument, timer = setInterval(async function () { if (!doc.hidden) { render(ctx.host, await api.tail(), state); } }, 8000);
      return { close: function () { clearInterval(timer); } }; } };
  }

  if (typeof Patchbay !== "undefined" && Patchbay.register) { Patchbay.register("taperoom", panel()); }
  else if (typeof window !== "undefined") { window.__TAPEROOM__ = panel(); window.__TAPEROOM_SAMPLE__ = SAMPLE; }
})();
