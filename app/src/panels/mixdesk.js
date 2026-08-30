// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Assisted by Claude (Anthropic); not an author or copyright holder.
/* RVND Mix Desk — LiveGov surface as a Patchbay panel.
 *
 * Drop-in adapter for the RVND console (Seam A). It renders the read-only
 * `governance_live` board as a mixing desk: one channel strip per session,
 * a DISCRETE verdict lamp per strip (honesty contract: lamps not dials, no
 * scores), the signed chain as an activity tail, and the summary as the
 * master bus. Human/reserved sessions expose Approve / Refuse, routed through
 * the one governed write path (approval_decide).
 *
 * To land in RVND: place at app/src/panels/mixdesk.js and add a pack.json entry
 *   { "id":"mixdesk", "entry":"mixdesk.js", "title":"Mix Desk",
 *     "surface":"drawer", "access":"read",
 *     "menu":{ "section":"record", "label":"Mix Desk", "detail":"Live governance, as a desk" } }
 * Reads via ctx.tool("workspace_workflow",{op:"governance_live",...}); decisions
 * post to /govlive/act (op approval_decide) — never a bespoke write route.
 *
 * Runs standalone (preview harness) when Patchbay/ctx.tool are absent: it uses
 * the embedded sample board. Governance stays in the facades; this file only renders.
 */
(function () {
  "use strict";

  /* ---- the 6-verdict alphabet (governance_live), least → most restrictive ---- */
  var VRANK = ["unfired", "auto", "human", "reserved", "refused", "prohibited"];
  var VMETA = {
    unfired:    { glyph: "○", cls: "v-unfired",    label: "unfired" },
    auto:       { glyph: "▲", cls: "v-auto",       label: "auto" },
    human:      { glyph: "◆", cls: "v-human",      label: "human" },
    reserved:   { glyph: "❚❚", cls: "v-reserved",  label: "reserved" },
    refused:    { glyph: "✕", cls: "v-refused",    label: "refused" },
    prohibited: { glyph: "⊘", cls: "v-prohibited", label: "prohibited" }
  };
  function vrank(v) { var i = VRANK.indexOf(v); return i < 0 ? 0 : i; }
  function strictest(list) { return list.reduce(function (a, v) { return vrank(v) > vrank(a) ? v : a; }, "unfired"); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }

  /* ---- embedded sample board (fallback for standalone preview only) ---- */
  var SAMPLE = {
    ok: true,
    summary: { sessions_open: 6, admitted: 5, run_leases_held: 2, escalations: 1, unauthorised_effects: 0 },
    sessions: [
      { sid: "s-01", actor: "ingest-bot",   admitted: true,  capability: { expires: "15:12" }, verdict: "auto",       grade: "L4", escalation: false },
      { sid: "s-02", actor: "payout-agent", admitted: true,  capability: { expires: "15:08" }, verdict: "reserved",   grade: "L2", escalation: false },
      { sid: "s-03", actor: "deploy-agent", admitted: true,  capability: { expires: "15:05" }, verdict: "human",      grade: "L1", escalation: true  },
      { sid: "s-04", actor: "export-agent", admitted: true,  capability: { expires: "15:20" }, verdict: "auto",       grade: "L3", escalation: false },
      { sid: "s-05", actor: "refund-agent", admitted: true,  capability: { expires: "15:02" }, verdict: "refused",    grade: "L0", escalation: false },
      { sid: "s-06", actor: "purge-agent",  admitted: false, capability: { expires: "—" },     verdict: "prohibited", grade: "L0", escalation: false }
    ],
    leases: [
      { run_id: "r-77", folder: "/ws", workflow: "payout", holder: "payout-agent", position: 0, ttl_s: 118 },
      { run_id: "r-78", folder: "/ws", workflow: "deploy", holder: "deploy-agent", position: 0, ttl_s: 44 }
    ],
    chain: [
      { seq: 148, actor: "deploy-agent", event: "Reserved",       extra: "code.deploy", hash: "9f2ac1", prev_hash: "a28aac" },
      { seq: 147, actor: "payout-agent", event: "Reserved",       extra: "payout",      hash: "a28aac", prev_hash: "71bd0e" },
      { seq: 146, actor: "refund-agent", event: "Refused",        extra: "refund",      hash: "71bd0e", prev_hash: "5c14aa" },
      { seq: 145, actor: "purge-agent",  event: "Prohibited",     extra: "file.delete", hash: "5c14aa", prev_hash: "0093f1" },
      { seq: 144, actor: "ingest-bot",   event: "AutoReleased",   extra: "data.export", hash: "0093f1", prev_hash: "ff20a7" }
    ],
    certificates: [],
    reconciliation: { status: "ok", unauthorised_rate: 0, matched: 22, authorised_not_observed: 1, observed_not_authorised: 0 }
  };

  /* ---- embedded sample presence roster (standalone preview only) ---- */
  var SAMPLE_AGENTS = [
    { connid: "9af5efc707f6eafb", agent: "author-session", transport: "stdio", pid: 55848, connected_at: Math.floor(Date.now() / 1000) - 120 },
    { connid: "5c112fbe1a3eb5eb", agent: "review-session", transport: "stdio", pid: 53585, connected_at: Math.floor(Date.now() / 1000) - 900 },
    { connid: "574f0da5c0b8499f", agent: "build-session", transport: "stdio", pid: 51604, connected_at: Math.floor(Date.now() / 1000) - 1800 }
  ];

  /* ---- scoped stylesheet (injected once; namespaced .mixdesk) ---- */
  var CSS = '' +
    '.mixdesk{--md-panel:#1a1c20;--md-hi:#26282d;--md-lo:#101216;--md-edge:#000;--md-ink:#e9e7e0;--md-dim:#8b8f98;--md-hair:rgba(255,255,255,.08);--md-orange:#e8621f;' +
      '--md-auto:#5fbf7a;--md-human:#e0a41c;--md-reserved:#d98a20;--md-refused:#e04a34;--md-prohibited:#c0392b;--md-conn:#5b8dea;--md-slot:#000;' +
      'font-family:"IBM Plex Mono",ui-monospace,monospace;color:var(--md-ink);background:linear-gradient(180deg,#0c0d10,#08090b);padding:14px;border-radius:8px}' +
    '.mixdesk *{box-sizing:border-box}' +
    '.md-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:12px}' +
    '.md-title{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:19px;letter-spacing:.02em}' +
    '.md-title b{color:var(--md-orange)}' +
    '.md-sub{font-size:11px;color:var(--md-dim)}' +
    '.md-master{display:flex;gap:10px;flex-wrap:wrap;align-items:stretch;background:var(--md-panel);border:1px solid var(--md-edge);border-radius:6px;padding:12px;margin-bottom:12px}' +
    '.md-verdict{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:22px;letter-spacing:.04em;padding:8px 14px;border-radius:5px;background:var(--md-slot);align-self:center;min-width:8ch;text-align:center}' +
    '.md-verdict.go{color:var(--md-auto)}.md-verdict.cond{color:var(--md-human)}.md-verdict.nogo{color:var(--md-refused)}.md-verdict.idle{color:var(--md-dim)}' +
    '.md-tiles{display:flex;gap:14px;flex-wrap:wrap;align-items:center;flex:1}' +
    '.md-tile{text-align:center}.md-tile .n{font-family:"Space Grotesk";font-weight:700;font-size:24px;line-height:1}' +
    '.md-tile.warn .n{color:var(--md-human)}.md-tile.bad .n{color:var(--md-refused)}' +
    '.md-tile .l{font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--md-dim);margin-top:3px}' +
    '.md-recon{font-size:10px;color:var(--md-dim);align-self:center}' +
    '.md-body{display:grid;grid-template-columns:1fr 260px;gap:12px}' +
    '@media(max-width:720px){.md-body{grid-template-columns:1fr}}' +
    '.md-rack{display:flex;gap:8px;overflow-x:auto;padding:8px;border:1px solid var(--md-hair);border-radius:6px;background:rgba(0,0,0,.2)}' +
    '.md-strip{flex:1;min-width:120px;background:linear-gradient(180deg,var(--md-hi),var(--md-panel) 60%,var(--md-lo));border:1px solid var(--md-edge);border-radius:5px;padding:9px 8px;display:flex;flex-direction:column;align-items:center;gap:7px}' +
    '.md-strip.esc{box-shadow:0 0 0 1px var(--md-refused),0 0 14px -3px var(--md-refused)}' +
    '.md-actor{font-family:"Space Grotesk";font-weight:600;font-size:12px;text-align:center;line-height:1.1}' +
    '.md-sid{font-size:9px;color:var(--md-dim)}' +
    '.md-ladder{display:flex;flex-direction:column-reverse;gap:3px;width:100%;padding:4px 6px}' +
    '.md-step{display:flex;align-items:center;gap:6px;font-size:9px;color:var(--md-dim);opacity:.4}' +
    '.md-step .dot{width:9px;height:9px;border-radius:50%;background:#0006;box-shadow:inset 0 0 0 1px rgba(0,0,0,.5);flex:none}' +
    '.md-step.on{opacity:1;font-weight:700}' +
    '.md-step.on .dot{box-shadow:0 0 8px 1px currentColor}' +
    '.md-step.v-auto{color:var(--md-auto)}.md-step.v-human{color:var(--md-human)}.md-step.v-reserved{color:var(--md-reserved)}' +
    '.md-step.v-refused{color:var(--md-refused)}.md-step.v-prohibited{color:var(--md-prohibited)}.md-step.v-unfired{color:var(--md-dim)}' +
    '.md-step.on .dot{background:currentColor}' +
    '.md-lamp{font-size:15px;line-height:1;font-weight:700;color:var(--md-dim)}' +
    '.md-lamp.v-auto{color:var(--md-auto)}.md-lamp.v-human{color:var(--md-human)}.md-lamp.v-reserved{color:var(--md-reserved)}.md-lamp.v-refused{color:var(--md-refused)}.md-lamp.v-prohibited{color:var(--md-prohibited)}.md-lamp.v-unfired{color:var(--md-dim)}.md-lamp.v-connected{color:var(--md-conn)}' +
    '.md-section{font-family:"Space Grotesk";font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:var(--md-dim);margin:6px 2px 6px}' +
    '.md-conn{border-color:var(--md-conn)}.md-badge.conn{border-color:var(--md-conn);color:var(--md-conn)}' +
    '.md-empty{color:var(--md-dim);font-size:11px;padding:14px;font-style:italic;flex:1}' +
    '.md-appr{background:var(--md-panel);border:1px solid var(--md-edge);border-radius:6px;margin-bottom:12px;overflow:hidden}' +
    '.md-appr h4{margin:0;padding:8px 12px;font-family:"Space Grotesk";font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--md-human);border-bottom:1px solid var(--md-hair)}' +
    '.md-appr-row{display:grid;grid-template-columns:auto 1fr auto auto;gap:12px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--md-hair);font-size:11px}' +
    '.md-appr-row .rq{font-family:"IBM Plex Mono";font-weight:700;color:var(--md-ink)}.md-appr-row .rf{color:var(--md-dim)}.md-appr-row .qn{font-family:"Space Grotesk";font-weight:700;color:var(--md-human)}' +
    '.md-vu{display:flex;flex-direction:column-reverse;gap:2px;width:22px;height:92px;margin:3px 0}' +
    '.md-seg{flex:1;border-radius:2px;background:#0006;box-shadow:inset 0 0 0 1px rgba(0,0,0,.5)}' +
    '.md-seg.v-auto{color:var(--md-auto)}.md-seg.v-human{color:var(--md-human)}.md-seg.v-reserved{color:var(--md-reserved)}.md-seg.v-refused{color:var(--md-refused)}.md-seg.v-prohibited{color:var(--md-prohibited)}' +
    '.md-seg.lit{background:currentColor;box-shadow:0 0 6px 1px currentColor,inset 0 0 0 1px rgba(0,0,0,.3)}' +
    '.md-meta{font-size:9.5px;color:var(--md-dim);text-align:center;line-height:1.4}' +
    '.md-meta b{color:var(--md-ink)}' +
    '.md-badge{font-size:9px;font-weight:700;padding:2px 6px;border-radius:2px;border:1px solid var(--md-dim);color:var(--md-dim)}' +
    '.md-badge.adm{border-color:var(--md-auto);color:var(--md-auto)}' +
    '.md-pads{display:flex;gap:5px;width:100%;margin-top:2px}' +
    '.md-pad{flex:1;font-family:"Space Grotesk";font-weight:600;font-size:11px;padding:6px 4px;border-radius:3px;border:1px solid var(--md-edge);cursor:pointer;background:linear-gradient(180deg,var(--md-hi),var(--md-lo));color:var(--md-ink)}' +
    '.md-pad.appr{border-color:var(--md-auto);color:var(--md-auto)}.md-pad.ref{border-color:var(--md-refused);color:var(--md-refused)}' +
    '.md-pad:active{transform:translateY(1px)}.md-pad:focus-visible{outline:2px solid var(--md-orange);outline-offset:2px}' +
    '.md-tail{border:1px solid var(--md-hair);border-radius:6px;background:rgba(0,0,0,.2);display:flex;flex-direction:column;min-height:0}' +
    '.md-tail h4{margin:0;padding:8px 10px;font-family:"Space Grotesk";font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:var(--md-dim);border-bottom:1px solid var(--md-hair)}' +
    '.md-tail .rows{overflow-y:auto;max-height:340px}' +
    '.md-row{display:grid;grid-template-columns:auto 1fr auto;gap:8px;padding:6px 10px;border-bottom:1px solid var(--md-hair);font-size:10.5px;align-items:baseline}' +
    '.md-row .seq{color:var(--md-dim)}.md-row .ev{font-weight:600}.md-row .ex{color:var(--md-dim);text-align:right}' +
    '.md-foot{font-size:10px;color:var(--md-dim);margin-top:10px;line-height:1.5}' +
    '.md-live{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}';

  function injectCSS(doc) {
    if (doc.getElementById("md-style")) return;
    var st = doc.createElement("style"); st.id = "md-style"; st.textContent = CSS; doc.head.appendChild(st);
  }

  /* ---- render the whole board (idempotent: rebuilds host innerHTML) ---- */
  function since(ts) { var s = Math.max(0, Math.floor(Date.now() / 1000 - (ts || 0))); if (s < 60) return s + "s"; if (s < 3600) return Math.floor(s / 60) + "m"; return Math.floor(s / 3600) + "h" + Math.floor((s % 3600) / 60) + "m"; }

  function render(host, board, agents, act) {
    var doc = host.ownerDocument;
    injectCSS(doc);
    board = board || {};
    var boardBad = (board.ok === false);
    var sessions = board.sessions || [];
    var overall = strictest(sessions.map(function (s) { return s.verdict; }));
    var busCls, busTxt;
    if (["refused", "prohibited"].indexOf(overall) >= 0) { busCls = "nogo"; busTxt = "NO-GO"; }
    else if (["human", "reserved"].indexOf(overall) >= 0) { busCls = "cond"; busTxt = "CONDITIONAL"; }
    else if (overall === "auto") { busCls = "go"; busTxt = "GO"; }
    else { busCls = "idle"; busTxt = "IDLE"; }

    var sm = board.summary || {};
    function tile(n, l, klass) { return '<div class="md-tile ' + (klass || "") + '"><div class="n">' + esc(n) + '</div><div class="l">' + esc(l) + '</div></div>'; }

    var SEGS = ["auto", "human", "reserved", "refused", "prohibited"]; // VU segments, low→high severity
    var strips = sessions.map(function (s, i) {
      var m = VMETA[s.verdict] || VMETA.unfired;
      var rank = vrank(s.verdict); // 0=unfired … 5=prohibited
      var vu = SEGS.map(function (v, k) {
        var vm = VMETA[v];
        return '<div class="md-seg ' + vm.cls + (rank >= (k + 1) ? " lit" : "") + '"></div>';
      }).join(""); // CSS column-reverse puts 'auto' at the bottom
      // Sessions carry no `actor` — governance_live keys them by `sid`
      // ({sid, admitted, capability, verdict, grade, escalation}). Strips are
      // read-only monitor lamps; decisions live in the pending-approvals rail,
      // which acts on a real request_id (the session↔approval join does not exist).
      return '<div class="md-strip' + (s.escalation ? " esc" : "") + '">' +
        '<div class="md-actor">' + esc(s.sid) + '</div>' +
        '<div class="md-vu">' + vu + '</div>' +
        '<div class="md-lamp ' + m.cls + '">' + m.glyph + " " + esc(m.label) + '</div>' +
        '<div class="md-meta"><span class="md-badge ' + (s.admitted ? "adm" : "") + '">' + (s.admitted ? "admitted" : "not admitted") + '</span> ' +
          '<b>' + esc(s.grade) + '</b><br>expires <b>' + esc((s.capability || {}).expires) + '</b>' + (s.escalation ? ' · <b style="color:var(--md-refused)">escalated</b>' : "") + '</div>' +
      '</div>';
    }).join("");

    var apprs = board.approvals || [];
    var apprRail = apprs.length ? (
      '<div class="md-appr"><h4>Pending approvals — decide by request (' + apprs.length + ')</h4>' +
      apprs.map(function (a) {
        var got = (a.approvers && a.approvers.length) || 0, need = (a.needed != null ? a.needed : "?");
        return '<div class="md-appr-row">' +
          '<span class="rq">' + esc(a.request_id || "approval") + '</span>' +
          '<span class="rf">' + esc(a.form || "") + (a.requester ? ' · ' + esc(a.requester) : "") + '</span>' +
          '<span class="qn">' + esc(got) + '/' + esc(need) + ' quorum</span>' +
          '<span class="md-pads"><button class="md-pad appr" data-act="approve" data-rid="' + esc(a.request_id || "") + '">Approve</button>' +
          '<button class="md-pad ref" data-act="refuse" data-rid="' + esc(a.request_id || "") + '">Refuse</button></span>' +
        '</div>';
      }).join("") + '</div>'
    ) : "";

    // Presence layer: the live MCP roster (/agents) — the actual connected
    // sessions. Presence, NOT authority: a blue "connected" lamp, never a
    // governance verdict, and labelled monitored so it is not read as a grant.
    var ags = agents || [];
    var presence = ags.length ? ags.map(function (a) {
      return '<div class="md-strip md-conn">' +
        '<div class="md-actor">' + esc((a.connid || "").slice(0, 8) || "agent") + '</div>' +
        '<div class="md-sid">' + esc(a.agent || "agent") + '</div>' +
        '<div class="md-lamp v-connected">● connected</div>' +
        '<div class="md-meta"><span class="md-badge conn">presence · monitored</span> ' +
          '<b>' + esc(a.transport || "stdio") + '</b> · pid ' + esc(a.pid || "—") + '<br>since <b>' + esc(since(a.connected_at)) + '</b></div>' +
      '</div>';
    }).join("") : '<div class="md-empty">No agents connected right now.</div>';

    var chain = (board.chain || []).map(function (c) {
      return '<div class="md-row"><span class="seq">#' + esc(c.seq) + '</span><span class="ev">' + esc(c.actor) + ' · ' + esc(c.event) + '</span><span class="ex">' + esc(c.extra) + '</span></div>';
    }).join("");

    var rc = board.reconciliation || {};
    var masterBlock = boardBad
      ? '<div class="md-master"><div class="md-verdict idle">MONITOR</div>' +
          '<div class="md-recon" style="flex:1">Governance authority board unavailable' + (board.error ? ' — ' + esc(board.error) : '') + '.<br>Presence above is live; open a registered workspace for the authority board.</div></div>'
      : '<div class="md-master">' +
          '<div class="md-verdict ' + busCls + '">' + busTxt + '</div>' +
          '<div class="md-tiles">' +
            tile(ags.length, "connected") +
            tile(sm.admitted || 0, "admitted") +
            tile(sm.run_leases_held || 0, "leases") +
            tile(sm.escalations || 0, "escalations", (sm.escalations ? "warn" : "")) +
            tile(sm.unauthorised_effects || 0, "unauthorised", (sm.unauthorised_effects ? "bad" : "")) +
          '</div>' +
          '<div class="md-recon">recon <b>' + esc(rc.status || "—") + '</b><br>matched ' + esc(rc.matched || 0) +
            ' · not-observed ' + esc(rc.authorised_not_observed || 0) + '<br>observed-not-auth ' + esc(rc.observed_not_authorised || 0) + '</div>' +
        '</div>';

    host.innerHTML =
      '<div class="mixdesk">' +
        '<div class="md-head"><div><div class="md-title">RVND <b>·</b> Mix Desk</div>' +
          '<div class="md-sub">Live monitoring — connected agents (presence) + governed sessions (authority) · lamps, not scores</div></div></div>' +
        '<div class="md-section">Connected agents — live (' + ags.length + ')</div>' +
        '<div class="md-rack">' + presence + '</div>' +
        masterBlock +
        apprRail +
        '<div class="md-section">Governed sessions — admitted (' + sessions.length + ')</div>' +
        '<div class="md-body">' +
          '<div class="md-rack">' + (strips || '<div class="md-empty">No admitted sessions. The agents above are monitored via the PreToolUse gate — their governed actions land in the signed chain, not here.</div>') + '</div>' +
          '<div class="md-tail"><h4>Signed chain — newest first</h4><div class="rows">' + chain + '</div></div>' +
        '</div>' +
        '<div class="md-foot">Two honest surfaces: <b>presence</b> is the live MCP roster (<b>/agents</b>) — your connected sessions, monitored; <b>authority</b> is <b>governance_live</b> — admitted/leased sessions with verdicts. Claude Code sessions are monitored (PreToolUse), so they show as presence + in the signed chain, not as admitted sessions. Approve / Refuse route through <b>approval_decide</b>.</div>' +
        '<div class="md-live" aria-live="polite" id="md-live"></div>' +
      '</div>';

    // wire decision pads → governed act, acting on the approval's real request_id
    [].forEach.call(host.querySelectorAll(".md-pad"), function (b) {
      b.addEventListener("click", function () {
        act(b.dataset.rid, b.dataset.act === "approve" ? "approve" : "refuse");
      });
    });
  }

  /* ---- data + governed write, via the RVND bridge (or standalone fallback) ---- */
  function unwrap(res) {
    // The RVND bridge hands panels the raw facade dict directly; also tolerate
    // {content:[{text}]} or a JSON string. Real shapes: governance_live →
    // {ok, summary, sessions, chain, …}; approval_list → {ok, approvals:[…]}.
    if (!res) return null;
    if (typeof res === "string") { try { return JSON.parse(res); } catch (e) { return null; } }
    if (res.content && res.content[0] && res.content[0].text) { try { return JSON.parse(res.content[0].text); } catch (e) { return null; } }
    if (res.sessions || res.summary || res.approvals || res.chain || res.ok != null) return res;
    return res.result ? unwrap(res.result) : res;
  }

  function makeAdapter(ctx) {
    var standalone = !(ctx && ctx.tool);
    function fc() { return ctx && ctx.workspace && ctx.workspace.path; }
    function nowS() { return Math.floor(Date.now() / 1000); }
    return {
      async board() {
        if (standalone) return (ctx && ctx.board) ? ctx.board() : SAMPLE;
        try {
          // READ — confirmed: workspace_workflow op=governance_live {folder_context, chain_limit}
          var b = unwrap(await ctx.tool("workspace_workflow", { op: "governance_live", params: { folder_context: fc(), chain_limit: 24 } }));
          if (!b || b.ok === false || (!b.sessions && !b.summary)) return { ok: false, error: (b && b.error) || "governance_live unreadable" };
          // Open approvals are their own decidable objects (by request_id) — the
          // board carries no per-session request_id and sessions have no actor,
          // so there is no honest session↔approval join. approval_list gives
          // {approvals:[{request_id, requester, form, state, approvers, needed}]}.
          try {
            var al = unwrap(await ctx.tool("workspace_workflow", { op: "approval_list", params: { folder_context: fc(), now: nowS() } }));
            var reqs = (al && (al.approvals || al.items || al.rows)) || [];
            b.approvals = reqs.filter(function (r) { return r && r.state === "pending"; });
          } catch (_) { b.approvals = []; }
          return b;
        } catch (e) { return { ok: false, error: (e && e.message) || "governance_live failed" }; } // real error → unavailable, NEVER fake sessions
      },
      async agents() {
        // Presence roster — the live MCP connections (your sessions), via RVND's
        // OWN governed facade op: workspace_workflow / connected_agents (server-level,
        // read-only, no folder) → {ok, count, agents:[{connid, agent, transport, pid,
        // connected_at}]}. Presence, not authority. Same ctx.tool path as every other
        // read here — no side-channel. Empty on any failure.
        if (standalone) return (ctx && ctx.agents) ? ctx.agents() : SAMPLE_AGENTS;
        try {
          var r = unwrap(await ctx.tool("workspace_workflow", { op: "connected_agents", params: {} }));
          return (r && r.agents) || [];
        } catch (e) { return []; }
      },
      async decide(rid, decision) {
        var live = (ctx && ctx.host) ? ctx.host.querySelector("#md-live") : null;
        if (standalone) { if (live) live.textContent = "demo: " + decision + " " + rid; return; }
        if (!rid) { if (live) live.textContent = "no request id for this approval — reload"; return; }
        // Governed write through the panel bridge (tool → /tool facade): approval_decide, then approval_resolve
        // to report whether the vote actually COUNTS (competence-matched quorum) — never shown as a grant otherwise.
        var dec = (decision === "approve" ? "approve" : "deny");
        try {
          await ctx.tool("workspace_workflow", { op: "approval_decide", params: { folder_context: fc(), request_id: rid, decision: dec, now: nowS() } });
          var res = unwrap(await ctx.tool("workspace_workflow", { op: "approval_resolve", params: { folder_context: fc(), request_id: rid, now: nowS() } })) || {};
          var counted = (res.state === "granted" || res.state === "denied");
          if (live) live.textContent = counted ? (decision + " · " + res.state) : (decision + " recorded — " + ((res.approvers && res.approvers.length) || 0) + "/" + (res.needed || "?") + " quorum");
        } catch (e) { if (live) live.textContent = "decide failed: " + ((e && e.message) || e); }
      }
    };
  }

  /* ---- the panel object (Patchbay contract) ---- */
  function panel() {
    return {
      async open(ctx) {
        var ad = makeAdapter(ctx);
        var host = ctx.host;
        var alive = true, timer = null;
        async function refresh() {
          if (!alive) return;
          // Presence (/agents) is independent of the folder authority board, so
          // fetch both and let render show whichever is live — the connected
          // agents surface even when no workspace authority board is available.
          var b = await ad.board();
          var agents = await ad.agents();
          render(host, b, agents, function (rid, d) { ad.decide(rid, d).then(refresh); });
        }
        await refresh();
        // 4s visibility-gated poll, matching govstrip cadence
        var doc = host.ownerDocument;
        function tick() { if (!doc.hidden) refresh(); }
        timer = setInterval(tick, 4000);
        doc.addEventListener("visibilitychange", function () { if (!doc.hidden) refresh(); });
        return { close: function () { alive = false; if (timer) clearInterval(timer); } };
      }
    };
  }

  /* ---- register with RVND Patchbay, or expose for the standalone preview ---- */
  if (typeof Patchbay !== "undefined" && Patchbay.register) {
    Patchbay.register("mixdesk", panel());
  } else if (typeof window !== "undefined") {
    window.__MIXDESK__ = panel();
    window.__MIXDESK_SAMPLE__ = SAMPLE;
  }
})();
