// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Assisted by Claude (Anthropic); not an author or copyright holder.
/* RVND Policy Bench — Author surface as a Patchbay panel.
 *
 * Drop-in adapter for the RVND console (Seam A, access:"write"). It reads the
 * current protections for a kind via workspace_policy, lets a human set the
 * oversight intent as a STRUCTURED CARD (discrete selectors/steppers — not free
 * dials), shows what that compiles to (the governing Loomground declaration +
 * its legal grounding + the resulting primitive grades, read-only), and commits
 * through the governed facade. Honesty contract: a request is never shown as a
 * grant — Commit shows requested → confirmed / reserved / refused from the
 * SERVER's verdict, never an optimistic "granted".
 *
 * To land in RVND: app/src/panels/policybench.js + pack.json entry
 *   { "id":"policybench","entry":"policybench.js","title":"Policy Bench",
 *     "surface":"drawer","access":"write",
 *     "menu":{"section":"rules","label":"Policy Bench","detail":"Author governance"} }
 * Reads:  ctx.tool("workspace_policy",{op:"read", params:{folder_context, kind}})
 * Writes: ctx.tool("workspace_policy",{op:"set",  params:{folder_context, kind, oversight, role, quorum, prohibit}})
 * (op names to confirm against the live workspace_policy facade.)
 * Runs standalone (preview harness) when Patchbay/ctx.tool are absent.
 */
(function () {
  "use strict";

  /* Loomground declaration grounding (subset of vocabulary/declarations.json) */
  var DECL = {
    "autonomy-grade": { verb: "autonomy set to", grounding: ["AI Act Art. 14", "AI Act Art. 26", "ISO 22989"] },
    reservation:      { verb: "requires approval from", grounding: ["AI Act Art. 14", "AI Act Art. 26", "GDPR Art. 22(3)"] },
    quorum:           { verb: "needs distinct approvers", grounding: ["separation of duty"] },
    prohibition:      { verb: "is blocked", grounding: ["AI Act Art. 5"] }
  };
  var HUMAN_ROLES = ["Data Protection Officer", "Supervisory Authority", "Board", "Court"];
  var OVERSIGHT = [
    { v: 0, label: "None",            decl: "autonomy-grade" },
    { v: 1, label: "Notify",          decl: "reservation" },
    { v: 2, label: "Second approver", decl: "reservation" },
    { v: 3, label: "Two-key",         decl: "quorum" }
  ];
  var COOL = ["run on", "cool the rate", "freeze"];

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }

  /* fallback current-policy (standalone preview only) */
  var SAMPLE = { kind: "payout", oversight: 2, role: "Data Protection Officer", quorum: 2, cool: 1, prohibit: false, committed: true };

  /* compile the card → primitive grades (host would do this; shown read-only) */
  function compile(p) {
    var grade = [6, 4, 2, 1][p.oversight];
    var quorum = p.oversight === 0 ? 0 : (p.oversight === 3 ? Math.max(2, p.quorum) : p.quorum);
    var rate = [60, 12, 0][p.cool];
    return { grade: grade, quorum: quorum, rate: rate };
  }
  function sentence(p) {
    var out = "High-value <b>" + esc(p.kind) + "</b> — ";
    if (p.prohibit) return out + "<b style=\"color:var(--pb-deny)\">blocked outright</b>.";
    if (p.oversight === 0) out += "release with <b>no human in the loop</b>";
    else if (p.oversight === 1) out += "a human from <b>" + esc(p.role) + "</b> is <b>notified</b>";
    else if (p.oversight === 2) out += "require <b>" + p.quorum + " approver" + (p.quorum > 1 ? "s" : "") + "</b> from <b>" + esc(p.role) + "</b>";
    else out += "need <b>two-key sign-off</b> — " + Math.max(2, p.quorum) + " distinct parties from <b>" + esc(p.role) + "</b>";
    return out + "; while pending the swarm will <b>" + COOL[p.cool] + "</b>.";
  }

  var CSS = '' +
    '.policybench{--pb-panel:#1a1c20;--pb-hi:#26282d;--pb-lo:#101216;--pb-ink:#e9e7e0;--pb-dim:#8b8f98;--pb-hair:rgba(255,255,255,.08);--pb-orange:#e8621f;--pb-auto:#5fbf7a;--pb-hold:#e0a41c;--pb-deny:#e04a34;--pb-slot:#000;' +
      'font-family:"IBM Plex Mono",monospace;color:var(--pb-ink);background:linear-gradient(180deg,#0c0d10,#08090b);padding:14px;border-radius:8px}' +
    '.policybench *{box-sizing:border-box}' +
    '.pb-title{font-family:"Space Grotesk";font-weight:700;font-size:19px}.pb-title b{color:var(--pb-orange)}' +
    '.pb-sub{font-size:11px;color:var(--pb-dim);margin-bottom:12px}' +
    '.pb-shelf{background:var(--pb-panel);border:1px solid #000;border-radius:6px;padding:14px;position:relative}' +
    '.pb-shelf::after{content:"POLICY";position:absolute;top:-8px;left:14px;background:var(--pb-orange);color:#111;font-family:"Space Grotesk";font-weight:700;font-size:9px;letter-spacing:.14em;padding:2px 7px;border-radius:2px}' +
    '.pb-sentence{font-size:14px;line-height:1.5;margin:6px 0 4px}.pb-sentence em{font-style:normal;color:var(--pb-orange)}' +
    '.pb-ground{font-size:10.5px;color:var(--pb-dim);margin-bottom:14px}.pb-ground b{color:var(--pb-ink)}' +
    '.pb-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}' +
    '.pb-card{background:rgba(0,0,0,.18);border:1px solid var(--pb-hair);border-radius:5px;padding:10px}' +
    '.pb-lbl{font-family:"Space Grotesk";text-transform:uppercase;letter-spacing:.09em;font-size:10px;color:var(--pb-dim);margin-bottom:8px}' +
    '.pb-seg{display:flex;gap:4px;flex-wrap:wrap}' +
    '.pb-seg button{font-family:"IBM Plex Mono";font-size:11.5px;color:var(--pb-dim);background:linear-gradient(180deg,var(--pb-hi),var(--pb-lo));border:1px solid #000;border-radius:3px;padding:6px 8px;cursor:pointer;flex:1;min-width:max-content}' +
    '.pb-seg button[aria-pressed="true"]{background:var(--pb-orange);color:#111;font-weight:700}' +
    '.pb-step{display:flex;align-items:center;gap:10px}.pb-step button{width:28px;height:28px;font-size:16px;font-family:"Space Grotesk";font-weight:700;background:linear-gradient(180deg,var(--pb-hi),var(--pb-lo));border:1px solid #000;border-radius:3px;color:var(--pb-ink);cursor:pointer}' +
    '.pb-qv{font-weight:700;min-width:5ch;text-align:center}' +
    '.pb-prim{display:flex;gap:16px;margin-top:14px;padding-top:12px;border-top:1px solid var(--pb-hair)}' +
    '.pb-prim .p{text-align:center}.pb-prim .n{font-family:"Space Grotesk";font-weight:700;font-size:20px}.pb-prim .k{font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--pb-dim);margin-top:2px}' +
    '.pb-prim .lock{font-size:9px;color:var(--pb-dim)}' +
    '.pb-commit{margin-top:14px;display:flex;gap:12px;align-items:center}' +
    '.pb-btn{font-family:"Space Grotesk";font-weight:700;text-transform:uppercase;letter-spacing:.06em;font-size:13px;padding:9px 16px;border-radius:4px;border:1px solid var(--pb-orange);color:var(--pb-orange);background:transparent;cursor:pointer}' +
    '.pb-btn:active{transform:translateY(1px)}.pb-btn:focus-visible{outline:2px solid var(--pb-orange);outline-offset:2px}' +
    '.pb-status{font-size:11.5px;color:var(--pb-dim)}.pb-status.req{color:var(--pb-hold)}.pb-status.ok{color:var(--pb-auto)}.pb-status.res{color:var(--pb-hold)}.pb-status.no{color:var(--pb-deny)}' +
    '.pb-foot{font-size:10px;color:var(--pb-dim);margin-top:10px;line-height:1.5}';

  function injectCSS(doc) { if (doc.getElementById("pb-style")) return; var st = doc.createElement("style"); st.id = "pb-style"; st.textContent = CSS; doc.head.appendChild(st); }

  function render(host, p, api) {
    var doc = host.ownerDocument; injectCSS(doc);
    if (p && p._unavailable) { host.innerHTML = '<div class="policybench"><div style="color:#e04a34;padding:12px;font-size:12px;line-height:1.5">Policy unreadable' + (p.error ? ' — ' + esc(p.error) : '') + '.<br><span style="color:#8b8f98">Open a registered workspace to author its policy.</span></div></div>'; return; }
    var c = compile(p);
    var decl = p.prohibit ? "prohibition" : OVERSIGHT[p.oversight].decl;
    var gr = (DECL[decl] || {}).grounding || [];
    function seg(id, items, cur, cast) {
      return '<div class="pb-seg" data-seg="' + id + '">' + items.map(function (it) {
        var v = (cast === "n") ? it.v : it.v; var lab = it.label;
        return '<button data-v="' + esc(v) + '" aria-pressed="' + (String(cur) === String(v) ? "true" : "false") + '">' + esc(lab) + '</button>';
      }).join("") + '</div>';
    }
    host.innerHTML =
      '<div class="policybench">' +
        '<div class="pb-title">RVND <b>·</b> Policy Bench</div>' +
        '<div class="pb-sub">Author — set the oversight; the grammar compiles it. A request is shown as a request, never a grant.</div>' +
        '<div class="pb-shelf">' +
          '<div class="pb-sentence">' + sentence(p) + '</div>' +
          '<div class="pb-ground">compiles to <b>' + esc(decl) + '</b>' + (p.oversight === 3 && !p.prohibit ? ' + <b>quorum</b>' : '') + (gr.length ? ' · grounded in <b>' + gr.map(esc).join(", ") + '</b>' : '') + '</div>' +
          '<div class="pb-cards">' +
            '<div class="pb-card"><div class="pb-lbl">Human oversight</div>' + seg("oversight", OVERSIGHT, p.oversight, "n") + '</div>' +
            '<div class="pb-card"><div class="pb-lbl">Approver role · registry</div>' + seg("role", HUMAN_ROLES.map(function (r) { return { v: r, label: r.replace("Data Protection Officer", "DPO") }; }), p.role) + '</div>' +
            '<div class="pb-card"><div class="pb-lbl">Approvers required</div><div class="pb-step"><button data-q="-1">–</button><span class="pb-qv">' + p.quorum + ' of 5</span><button data-q="1">+</button></div></div>' +
            '<div class="pb-card"><div class="pb-lbl">While pending</div>' + seg("cool", COOL.map(function (l, i) { return { v: i, label: l }; }), p.cool, "n") + '</div>' +
          '</div>' +
          '<div class="pb-prim">' +
            '<div class="p"><div class="n">L' + c.grade + '</div><div class="k">autonomy</div><div class="lock">🔒 compiled</div></div>' +
            '<div class="p"><div class="n">' + c.quorum + '</div><div class="k">quorum</div><div class="lock">🔒 compiled</div></div>' +
            '<div class="p"><div class="n">' + c.rate + '</div><div class="k">rate /min</div><div class="lock">🔒 compiled</div></div>' +
          '</div>' +
        '</div>' +
        '<div class="pb-commit"><button class="pb-btn" id="pb-commit">Commit policy</button>' +
          '<span class="pb-status" id="pb-status">' + (p.committed ? "in effect (server-confirmed)" : "unsaved changes") + '</span></div>' +
        '<div class="pb-foot">Reads/writes <b>workspace_policy</b>. Commit sends the request to the governed facade and shows the server\'s verdict — <b>confirmed / reserved / refused</b> — never an optimistic grant. Autonomy/quorum/rate are <b>compiled</b> (read-only), never hand-dialed. Guards may only range over kind · risk · reversibility · uncertainty · party · tags.</div>' +
      '</div>';

    host.querySelector('[data-seg="oversight"]').addEventListener("click", function (e) { var b = e.target.closest("button"); if (b) { p.oversight = +b.dataset.v; p.committed = false; render(host, p, api); } });
    host.querySelector('[data-seg="role"]').addEventListener("click", function (e) { var b = e.target.closest("button"); if (b) { p.role = HUMAN_ROLES.filter(function (r) { return r.replace("Data Protection Officer", "DPO") === b.dataset.v || r === b.dataset.v; })[0] || b.dataset.v; p.committed = false; render(host, p, api); } });
    host.querySelector('[data-seg="cool"]').addEventListener("click", function (e) { var b = e.target.closest("button"); if (b) { p.cool = +b.dataset.v; p.committed = false; render(host, p, api); } });
    [].forEach.call(host.querySelectorAll("[data-q]"), function (b) { b.addEventListener("click", function () { p.quorum = Math.max(1, Math.min(5, p.quorum + (+b.dataset.q))); p.committed = false; render(host, p, api); }); });
    host.querySelector("#pb-commit").addEventListener("click", function () {
      var st = host.querySelector("#pb-status"); st.className = "pb-status req"; st.textContent = "requested — awaiting server verdict…";
      api.commit(p).then(function (verdict) {
        var map = { auto: ["ok", "confirmed — in effect"], confirmed: ["ok", "confirmed — in effect"], reserved: ["res", "reserved — referred for sign-off"], human: ["res", "held for human sign-off"], refused: ["no", "refused by policy"], prohibited: ["no", "prohibited — not permitted"] };
        var m = map[verdict] || ["res", String(verdict)];
        st.className = "pb-status " + m[0]; st.textContent = m[1];
        p.committed = (m[0] === "ok");
      });
    });
  }

  // level ↔ card-oversight-index mapping (confirm the real oversight-level vocabulary on a live server)
  function levelToIdx(l){ if (l == null) return 2; if (typeof l === "number") return Math.max(0,Math.min(3,l)); return ({none:0,off:0,notify:1,advise:1,approve:2,second:2,review:2,twokey:3,"two-key":3,dual:3})[String(l).toLowerCase()] || 2; }
  function idxToLevel(i){ return ["none","notify","approve","two-key"][i] || "approve"; }
  function roleSlug(r){ return String(r).toLowerCase().replace("data protection officer","dpo").replace(/[^a-z0-9]+/g,"-").replace(/(^-|-$)/g,""); }

  function makeApi(ctx) {
    var standalone = !(ctx && ctx.tool);
    function fc(){ return ctx && ctx.workspace && ctx.workspace.path; }
    function actor(){ return (ctx && ctx.actor) || "operator"; }
    var KIND = "payout";
    return {
      async current() {
        if (standalone) return Object.assign({}, (ctx && ctx.policy) ? ctx.policy() : SAMPLE);
        // committed stays false until the server actually answers a read — never
        // present the embedded SAMPLE as "server-confirmed".
        var p = Object.assign({}, SAMPLE, { kind: KIND, committed: false });
        try {
          // READ dials — workspace_policy op=snapshot {folder_context}
          var snap = normalize(await ctx.tool("workspace_policy", { op: "snapshot", params: { folder_context: fc() } }));
          if (snap && snap.oversight_default_level != null) p.oversight = levelToIdx(snap.oversight_default_level);
          // READ per-kind protection — workspace_workflow op=lane_capabilities {folder_context, actor, kinds}
          var lc = normalize(await ctx.tool("workspace_workflow", { op: "lane_capabilities", params: { folder_context: fc(), actor: actor(), kinds: [KIND] } }));
          var cap = lc && lc.capabilities && lc.capabilities.filter(function (c) { return c.kind === KIND; })[0];
          if (cap) { p.prohibit = (cap.verdict === "prohibited"); p._currentVerdict = cap.verdict; p._currentGrade = cap.grade; }
          if (snap || lc) p.committed = true; else p._unavailable = true;
        } catch (e) { p._unavailable = true; p.error = (e && e.message) || "policy read failed"; }
        return p;
      },
      async commit(p) {
        if (standalone) { await new Promise(function (r) { setTimeout(r, 500); }); return "confirmed"; }
        try {
          // 1) folder oversight level — workspace_policy op=set_oversight_level {folder_context, level}
          await ctx.tool("workspace_policy", { op: "set_oversight_level", params: { folder_context: fc(), level: idxToLevel(p.oversight) } });
          // 2) grade×oversight traffic light (tighten-only) — workspace_matrix op=set {folder_context, grade, oversight, light}
          var c = compile(p), light = p.prohibit ? "block" : (p.oversight >= 2 ? "ask" : "go");
          await ctx.tool("workspace_matrix", { op: "set", params: { folder_context: fc(), grade: "L" + c.grade, oversight: idxToLevel(p.oversight), light: light } });
          // 3) per-kind reserve/prohibit via the .lg patch (authoritative) — workspace_workflow op=patch_apply {folder_context, actor, netlist}
          var lines = [];
          if (p.prohibit) lines.push("prohibit " + p.kind);
          else if (p.oversight >= 2) lines.push("reserve " + p.kind + " by " + (p.oversight === 3 ? Math.max(2, p.quorum) + " of { " + roleSlug(p.role) + " }" : roleSlug(p.role)));
          // No reserve/prohibit line means the request is only an oversight-level
          // change, which the two writes above confirmed → "confirmed". When we DO
          // submit a protection, never claim a grant: default to the REQUESTED
          // protection (reserved / prohibited) and only overwrite it with the
          // server's own verdict. A request is never shown as a grant.
          var verdict = "confirmed";
          if (lines.length) {
            verdict = p.prohibit ? "prohibited" : "reserved";
            var r = normalize(await ctx.tool("workspace_workflow", { op: "patch_apply", params: { folder_context: fc(), actor: actor(), netlist: lines.join("\n") } }));
            if (r && r.error) verdict = "error";
            else if (r && r.verdicts && r.verdicts[p.kind]) verdict = r.verdicts[p.kind]; // server-decided strictest-wins
          }
          return verdict; // confirmed | reserved | refused | prohibited
        } catch (e) { return "error"; } // commit error is an error, not a policy refusal
      }
    };
  }
  // The RVND bridge hands panels the raw facade dict. Recognize the real shapes:
  // workspace_policy snapshot → {oversight_default_level, lock_mode, …};
  // lane_capabilities → {capabilities:[…], provenance}; patch_apply → {nodes, edges, verdicts}.
  function normalize(res) { if (!res) return null; if (typeof res === "string") { try { return JSON.parse(res); } catch (e) { return null; } } if (res.content && res.content[0] && res.content[0].text) { try { return JSON.parse(res.content[0].text); } catch (e) { return null; } } if (res.oversight_default_level != null || res.capabilities || res.verdicts || res.nodes || res.oversight != null || res.verdict || res.ok != null) return res; return res.result ? normalize(res.result) : res; }

  function panel() {
    return { async open(ctx) { var api = makeApi(ctx); render(ctx.host, await api.current(), api); return { close: function () { } }; } };
  }

  if (typeof Patchbay !== "undefined" && Patchbay.register) { Patchbay.register("policybench", panel()); }
  else if (typeof window !== "undefined") { window.__POLICYBENCH__ = panel(); window.__POLICYBENCH_SAMPLE__ = SAMPLE; }
})();
