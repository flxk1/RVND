// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Say — the driver and the grower — over the shared store (units/state.mjs).
// One input, two roles, every effect a governed op through the /tool bridge.
//
//  DRIVER — a command path. The input names an op (bare, or `tool op k=v …`);
//    it resolves against the server's `help` registry, which stamps each op with
//    `mutates`. A read runs and renders structured; a write raises a confirm-card
//    (the op, its params, that it will be recorded) and fires the gated op only
//    on confirm. After a write the store re-reads, so Read re-projects in the
//    same tick. A disposition of `reserved` renders no execute control.
//    Anything that is not a command routes to governance_chat.
//
//  GROWER — the generative loop. A pasted policy (or `ingest …`) goes through
//    governance_chat (intent policy → policy_ingest) and renders the requisite-
//    variety ledger: express (the governor absorbs these, as Loomground
//    declarations) vs host/policy/unmapped (handed back to the human), plus the
//    drafted control patch — what may run and who answers, never a workflow. The
//    patch already round-trips loomground_lang; nothing applies until a human
//    confirms, and the confirm is the governed write (patch_apply on the
//    netlist — the same op the classic console uses, not a new apply path).
//
//  CHAT WITH THE POLICY — free text tries retrieval first: workspace_memory's
//    safe_context_query (the existing chat-with-folder spine) over the
//    ingested corpus, re-scored client-side against the query so its
//    recent()-fallback never masquerades as a real match. A hit renders the
//    matched, lock-scrubbed facts plus an opt-in "Explain with AI" step —
//    offered only when model_capability reports a capable local model, never
//    faked, and grounded on nothing but those already-scrubbed facts.
//
//  THE GRAPH — no retrieval hit, then free text is matched against
//    governance_kg's node labels (instrument/role/room/rule/obligation/gate/
//    artifact) for the current workspace. Two matched nodes plus relational
//    language ("X and Y", "between X and Y", "does X affect Y") compose a
//    reasoning path (governance_kg path mode) — the ordered edges are the
//    auditable "why". One matched node projects its detail neighbourhood
//    instead. No match falls through to governance_chat unchanged. This is
//    text matching against real node labels, never a guess at the graph's
//    shape.

const OP_TOOLS = [
  "workspace_workflow", "workspace_policy", "workspace_audit", "workspace_folder",
  "workspace_workspace", "workspace_contract", "workspace_conformity", "workspace_legal",
  "workspace_matrix", "workspace_memory", "workspace_lens", "workspace_lock",
  "workspace_model", "workspace_grounder", "workspace_mirror", "workspace_erase",
  "workspace_capture", "workspace_ingest", "workspace_session", "workspace_dispatch",
];

// Dispositions the server may attach to a governed act. `reserved` is the one
// the human alone may complete — it renders no execute control.
const RESERVED = new Set(["reserved", "reserved_by_law"]);

// Standalone (non-op) tools have no help/ops entry, so the op registry can't
// reach them. Surface them as explicit command verbs, each scoped to the
// focused workspace, so the command bar reaches the whole tool surface — not
// just the op-based facades. (server_info is a read-only shell affordance, not
// a query, so it stays out of the command grammar.)
const STANDALONE_CMDS = [
  { verb: "ask",         tool: "workspace_ask",         mk: (q, fc) => ({ folder_context: fc, query: q }) },
  { verb: "orchestrate", tool: "workspace_orchestrate", mk: (q, fc) => ({ folder_context: fc, query: q }) },
  { verb: "cross-read",  tool: "cross_workspace_read",  mk: (q, fc) => ({ folder_context: fc, sources: [q] }) },
];

// Words too common to match a graph node on their own (kept short and
// unglamorous — this is deliberately not a stemmer or a stopword corpus).
const STOPWORDS = new Set([
  "does", "what", "how", "why", "who", "when", "where", "which", "should",
  "would", "could", "about", "between", "relate", "relates", "related",
  "relation", "versus", "affect", "affects", "connect", "connects", "and",
  "the", "for", "are", "with", "that", "this", "from", "into",
]);
// Language suggesting the question relates TWO things, not one — the signal
// to attempt a reasoning path instead of a single-node projection.
const RELATIONAL_RX = /\b(relate|relates|related|relation|vs\.?|versus|between|affect|affects|connect|connects|why does|how does)\b/i;
function graphTokens(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9\s]/g, " ").split(/\s+/)
    .filter((w) => w.length >= 4 && !STOPWORDS.has(w));
}

export function createSay(store, call, doc) {
  const d = doc;
  const esc = (s) => { const e = d.createElement("div"); e.textContent = String(s == null ? "" : s); return e.innerHTML; };
  const base = (p) => String(p || "").split("/").pop() || p;

  let helpCache = null;                 // op-name → { tool, entry } (first tool wins)
  let policyText = "";                  // the session's accumulated rules (the ask corpus)

  function out() { return d.getElementById("say-out"); }
  function focus() { return store.getState().activeWorkspace || ""; }

  function show(html) {
    const o = out(); if (!o) return;
    o.innerHTML = html;
    o.style.display = "block";      // the stylesheet default is none; reveal it
  }
  function clear() { const o = out(); if (o) { o.innerHTML = ""; o.style.display = "none"; } }

  // Build the op index once from the `help` registry of every op-tool, so a bare
  // op name resolves to its tool and carries the server-stamped `mutates`.
  async function index() {
    if (helpCache) return helpCache;
    helpCache = {};
    const helps = await Promise.all(OP_TOOLS.map((t) =>
      call(t, { op: "help", params: {} }).then((h) => [t, h]).catch(() => [t, null])));
    for (const [tool, h] of helps) {
      const ops = h && Array.isArray(h.ops) ? h.ops : [];
      for (const e of ops) {
        if (e && typeof e === "object" && e.op && !(e.op in helpCache)) helpCache[e.op] = { tool, entry: e };
      }
    }
    return helpCache;
  }

  // Resolve one help entry. `tool op …` names its tool; a bare op is looked up.
  async function resolve(tool, op) {
    if (tool) {
      const h = await call(tool, { op: "help", params: {} }).catch(() => null);
      const ops = h && Array.isArray(h.ops) ? h.ops : [];
      const entry = ops.find((e) => e && e.op === op) || null;
      return entry ? { tool, entry } : null;
    }
    const idx = await index();
    return idx[op] || null;
  }

  // key=value tokens → params; folder_context defaults to the focused workspace
  // when the op declares it and the caller left it out (commands are scoped to
  // the active workspace, the console's context chip).
  function parseParams(tokens, entry) {
    const params = {};
    for (const t of tokens) {
      const i = t.indexOf("=");
      if (i > 0) params[t.slice(0, i)] = coerce(t.slice(i + 1));
    }
    const wants = new Set([...(entry.required || []), ...(entry.optional || [])]);
    if (wants.has("folder_context") && !("folder_context" in params) && focus()) params.folder_context = focus();
    return params;
  }
  function coerce(v) {
    if (v === "true") return true;
    if (v === "false") return false;
    if (/^-?\d+$/.test(v)) return Number(v);
    return v;
  }

  // --- structured result rendering (never raw JSON) --------------------------

  function rowsOf(r) {
    for (const k of ["parties", "events", "rows", "ops", "buses", "items", "records", "connectors"]) {
      if (Array.isArray(r[k])) return { key: k, rows: r[k] };
    }
    return null;
  }
  function renderResult(op, r) {
    if (r == null) return '<div class="say-err">no response</div>';
    if (r.error) return '<div class="say-err">' + esc(r.error) + "</div>";
    if (r.ok === false) return '<div class="say-err">' + esc(((r.errors || ["failed"])).join("; ")) + "</div>";
    // a reserved disposition completes with a person — no execute control here
    const disp = r.disposition || r.verdict;
    if (disp && RESERVED.has(String(disp))) {
      return '<div class="say-card"><b>' + esc(op) + "</b> · reserved by law — a person must sign off. "
        + "No execute control here.</div>";
    }
    const listed = rowsOf(r);
    if (listed) {
      const cols = columns(listed.rows);
      const head = "<tr>" + cols.map((c) => "<th>" + esc(c) + "</th>").join("") + "</tr>";
      const body = listed.rows.slice(0, 40).map((row) =>
        "<tr>" + cols.map((c) => "<td>" + esc(cell(row, c)) + "</td>").join("") + "</tr>").join("");
      return '<div class="say-card"><div class="say-h">' + esc(op) + " · " + listed.rows.length + " "
        + esc(listed.key) + '</div><div class="say-scroll"><table class="say-tbl">' + head + body + "</table></div></div>";
    }
    // a scalar/verdict result → labelled key/value rows
    const kv = Object.keys(r).filter((k) => typeof r[k] !== "object").slice(0, 12);
    const rows = kv.map((k) => '<div class="say-kv"><span class="k">' + esc(k) + '</span><span class="v">' + esc(r[k]) + "</span></div>").join("");
    return '<div class="say-card"><div class="say-h">' + esc(op) + "</div>" + (rows || '<div class="say-kv">done</div>') + "</div>";
  }
  function columns(rows) {
    const seen = [];
    for (const row of rows.slice(0, 8)) {
      if (row && typeof row === "object") for (const k of Object.keys(row)) if (!seen.includes(k) && typeof row[k] !== "object") seen.push(k);
    }
    return seen.slice(0, 6);
  }
  function cell(row, c) {
    if (row == null || typeof row !== "object") return String(row);
    const v = row[c];
    return v == null ? "" : (typeof v === "object" ? JSON.stringify(v) : v);
  }

  // --- the confirm-card (writes) ---------------------------------------------

  function renderConfirm(tool, op, params, entry) {
    const plist = Object.keys(params).map((k) =>
      '<div class="say-kv"><span class="k">' + esc(k) + '</span><span class="v">' + esc(params[k]) + "</span></div>").join("")
      || '<div class="say-kv">(no params)</div>';
    let h = '<div class="say-card say-confirm"><div class="say-h">Confirm — ' + esc(op) + "</div>"
      + '<div class="say-sub">a governed write via <code>' + esc(tool) + "</code>. "
      + "The server decides, and it will be recorded.</div>" + plist;
    if (isReservedEntry(entry)) {
      h += '<div class="say-sub say-reserved">Reserved by law — a person must sign off. No execute control here.</div>'
        + '<div class="say-actions"><button class="say-btn" data-say="cancel">Close</button></div>';
    } else {
      h += '<div class="say-actions">'
        + '<button class="say-btn say-go" data-say="run">Confirm &amp; run</button>'
        + '<button class="say-btn" data-say="cancel">Cancel</button></div>';
    }
    h += "</div>";
    show(h);
    const o = out();
    const cancel = o.querySelector('[data-say="cancel"]'); if (cancel) cancel.addEventListener("click", clear);
    const run = o.querySelector('[data-say="run"]');
    if (run) run.addEventListener("click", async () => {
      run.disabled = true; run.textContent = "running…";
      const r = await call(tool, { op, params }).catch((e) => ({ error: String(e && e.message || e) }));
      show(renderResult(op, r));
      // the write landed — re-read so Read (and env) re-project in the same tick,
      // and bring Build forward so the graph is what's on screen right after you talk
      if (!(r && (r.error || r.ok === false))) { store.setState({ centreView: "build" }); await store.hydrate(); }
    });
  }
  // An op the server declares reserved-by-law renders no execute control. Read
  // from the help entry, never guessed from the op name.
  function isReservedEntry(entry) {
    if (!entry) return false;
    if (entry.reserved === true || RESERVED.has(String(entry.disposition || ""))) return true;
    return /\breserved by law\b/i.test(entry.note || "");
  }

  // --- the grower: the variety ledger + the drafted control patch ------------

  async function grow(text, forceIntent) {
    show('<div class="say-card"><div class="say-h">drafting…</div></div>');
    const params = { folder_context: focus(), text, policy_text: policyText };
    if (forceIntent) params.intent = forceIntent;
    const r = await call("workspace_workflow", { op: "governance_chat", params }).catch((e) => ({ error: String(e && e.message || e) }));
    if (!r || r.error) { show('<div class="say-err">' + esc((r && r.error) || "chat failed") + "</div>"); return; }
    if (r.kind === "twin") {
      if (r.intent === "policy") policyText = (policyText ? policyText + "\n" : "") + text;
      await renderLedger(r.result);
      return;
    }
    // ask / intake render structured, not raw JSON
    if (r.kind === "map") { show('<div class="say-card"><div class="say-h">answer</div><div class="say-sub">' + esc(summariseMap(r.result)) + "</div></div>"); return; }
    if (r.kind === "card") { const c = r.result || {}; show('<div class="say-card"><div class="say-h">use case captured</div><div class="say-sub">' + esc(c.description || "") + " · " + Math.round((c.completeness || 0) * 100) + "% complete</div></div>"); return; }
    show(renderResult(r.echo || r.intent || "chat", r.result || {}));
  }
  function summariseMap(m) {
    if (!m || typeof m !== "object") return "—";
    if (typeof m.summary === "string") return m.summary;
    if (typeof m.answer === "string") return m.answer;
    return "answered from the map";
  }

  async function renderLedger(twin) {
    store.setState({ chatContext: Object.assign({}, store.getState().chatContext, { ledger: twin }) });
    if (!twin || twin.ok === false) {
      show('<div class="say-err">could not draft a twin: ' + esc(((twin && twin.errors) || ["unknown"]).join("; ")) + "</div>");
      return;
    }
    if (twin.quarantined) {
      show('<div class="say-card"><div class="say-h">routed to the interpreter</div><div class="say-sub">'
        + esc(twin.note || "a court decision interprets norms; it does not enact them — no patch drafted") + "</div></div>");
      return;
    }
    const c = twin.classification || {};
    const li = (a) => (a && a.length) ? a.map((x) => "<li>" + esc(x) + "</li>").join("") : '<li class="none">none</li>';
    const patch = twin.patch || {};
    const nN = (patch.nodes || []).length, nC = (patch.cords || []).length;
    const nR = (patch.reservations || []).length, nP = (patch.prohibitions || []).length;

    // belt-and-braces: re-validate the drafted patch through loomground_lang so
    // the confirm is offered only against a patch the server calls valid
    let valid = twin.ok === true && !!twin.netlist;
    if (focus()) {
      const v = await call("workspace_workflow", { op: "patch_validate", params: { folder_context: focus(), netlist: twin.netlist } }).catch(() => null);
      if (v && typeof v.ok === "boolean") valid = v.ok;
    }

    const fc = focus();
    let h = '<div class="say-card say-ledger">';
    h += '<div class="say-h">Draft control patch — what may run, who answers. Not a workflow.</div>';
    h += '<div class="say-sub">' + esc(twin.note || "declares governance; does not certify. Applies nothing until you confirm.") + "</div>";
    // the requisite-variety ledger
    h += '<div class="say-led-grp"><label>Express — the governor absorbs these, as Loomground declarations ('
      + ((c.express || []).length) + ")</label><ul class=\"say-ul express\">" + li(c.express) + "</ul></div>";
    h += '<div class="say-led-grp"><div class="say-handback">Handed back to you:</div>';
    h += '<label>Host — the runtime must do these (' + ((c.host || []).length) + ')</label><ul class="say-ul">' + li(c.host) + "</ul>";
    h += '<label>Policy — values to confirm (' + ((c.policy || []).length) + ')</label><ul class="say-ul">' + li(c.policy) + "</ul>";
    if ((c.unmapped || []).length) h += '<label>Unmapped — review (' + c.unmapped.length + ')</label><ul class="say-ul">' + li(c.unmapped) + "</ul>";
    h += "</div>";
    // the drafted patch, stated as control not execution
    h += '<div class="say-sub say-patch">Patch: ' + nN + " node" + (nN === 1 ? "" : "s") + ", " + nC + " authority cord" + (nC === 1 ? "" : "s")
      + ", " + nR + " reservation" + (nR === 1 ? "" : "s") + (nP ? ", " + nP + " prohibition" + (nP === 1 ? "" : "s") : "")
      + " · " + (valid ? "Loomground-valid ✓" : "not valid — cannot apply") + "</div>";
    h += '<details class="say-lg"><summary>.lg netlist</summary><pre>' + esc(twin.netlist || "") + "</pre></details>";
    // applied stays false until the human confirms; the confirm is the write
    const canApply = valid && !!fc;
    h += '<div class="say-actions"><button class="say-btn say-go" data-say="apply"' + (canApply ? "" : ' disabled title="' + (fc ? "patch not valid" : "open a workspace to apply") + '"')
      + ">Confirm &amp; apply to the chain</button>"
      + '<button class="say-btn" data-say="cancel">Close</button></div>';
    h += "</div>";
    show(h);
    const o = out();
    const cancel = o.querySelector('[data-say="cancel"]'); if (cancel) cancel.addEventListener("click", clear);
    const apply = o.querySelector('[data-say="apply"]');
    if (apply && canApply) apply.addEventListener("click", () => applyTwin(twin));
  }

  // Reuse the classic console's apply path exactly: patch_apply on the netlist.
  async function applyTwin(twin) {
    const fc = focus(); if (!twin || twin.ok === false || !fc) return;
    const o = out(); const apply = o && o.querySelector('[data-say="apply"]');
    if (apply) { apply.disabled = true; apply.textContent = "applying…"; }
    const res = await call("workspace_workflow", { op: "patch_apply", params: { folder_context: fc, actor: "app-user", netlist: twin.netlist } })
      .catch((e) => ({ ok: false, errors: [String(e && e.message || e)] }));
    if (!res || res.ok === false) {
      show('<div class="say-err">apply failed — nothing written: ' + esc(((res && res.errors) || ["unknown"]).join("; ")) + "</div>");
      return;
    }
    show('<div class="say-card"><div class="say-h">applied to the chain</div><div class="say-sub">the control patch is now governing this workspace.</div></div>');
    // the write landed — bring Build forward so the graph is what's on screen
    // right after you talk to it, then re-read so every surface re-projects
    store.setState({ chatContext: Object.assign({}, store.getState().chatContext, { ledger: null }), centreView: "build" });
    await store.hydrate();
  }

  // --- the graph: match free text against governance_kg node labels ---------

  // Try to answer from the rule graph. governance_kg is a stateless projection
  // over whatever policy_text/provisions it's handed in THIS call — same
  // corpus governance_map's "ask" path already draws on (the session's
  // accumulated policyText, grown by every "ingest …"). Returns false (never
  // throws) when nothing matches, so the caller falls through to
  // governance_chat unchanged — this only ever adds a route, never removes one.
  async function tryGraph(text) {
    const fc = focus(); if (!fc || !policyText) return false;
    const kg = await call("workspace_workflow", { op: "governance_kg", params: { folder_context: fc, level: "detail", policy_text: policyText } }).catch(() => null);
    if (!kg || !Array.isArray(kg.nodes) || !kg.nodes.length) return false;
    const qWords = graphTokens(text);
    if (!qWords.length) return false;

    const scored = [];
    for (const n of kg.nodes) {
      const score = graphTokens(n.label).filter((w) => qWords.includes(w)).length;
      if (score > 0) scored.push({ n, score });
    }
    if (!scored.length) return false;
    scored.sort((a, b) => b.score - a.score);
    const distinct = [];
    for (const s of scored) { if (!distinct.some((d) => d.id === s.n.id)) distinct.push(s.n); if (distinct.length >= 2) break; }

    if (RELATIONAL_RX.test(text) && distinct.length >= 2) {
      const [a, b] = distinct;
      const p = await call("workspace_workflow", { op: "governance_kg", params: { folder_context: fc, policy_text: policyText, from: a.id, to: b.id } }).catch(() => null);
      if (p) { showGraphPath(a, b, p); return true; }
    }
    const proj = await call("workspace_workflow", { op: "governance_kg", params: { folder_context: fc, level: "detail", policy_text: policyText, focus: distinct[0].id } }).catch(() => null);
    if (!proj) return false;
    showGraphFocus(distinct[0], proj);
    return true;
  }

  function showGraphFocus(node, proj) {
    const nodes = proj.nodes || [];
    const rel = (proj.edges || []).filter((e) => e.source === node.id || e.target === node.id).map((e) => {
      const otherId = e.source === node.id ? e.target : e.source;
      const other = nodes.find((n) => n.id === otherId);
      return { dim: e.dimension, edgeLabel: e.label, label: (other && other.label) || otherId, kind: (other && other.kind) || "" };
    });
    let h = '<div class="say-card"><div class="say-h">the graph — ' + esc(node.label) + "</div>";
    h += '<div class="say-sub">' + esc(node.kind) + (node.risk ? " · risk " + esc(node.risk) : "") + "</div>";
    h += '<div class="say-sub" style="margin-top:6px">why — connected via</div>';
    h += rel.length
      ? rel.map((r) => '<div class="say-kv"><span class="k">' + esc(r.dim) + (r.edgeLabel ? " · " + esc(r.edgeLabel) : "") + '</span><span class="v">' + esc(r.label) + (r.kind ? " (" + esc(r.kind) + ")" : "") + "</span></div>").join("")
      : '<div class="say-kv">no recorded edges</div>';
    h += "</div>";
    show(h);
  }

  function showGraphPath(a, b, p) {
    let h = '<div class="say-card"><div class="say-h">the graph — ' + esc(a.label) + " → " + esc(b.label) + "</div>";
    if (!p.hops) {
      h += '<div class="say-sub">no recorded path between these in this workspace’s rule graph — they may not be related here</div></div>';
      show(h); return;
    }
    h += '<div class="say-sub">why — ' + p.hops + " hop" + (p.hops === 1 ? "" : "s") + ", composed dimension <b>" + esc(p.overall_dimension || "") + "</b></div>";
    h += (p.edges || []).map((e) => '<div class="say-kv"><span class="k">' + esc(e.dimension) + '</span><span class="v">' + esc(e.source) + " → " + esc(e.target) + (e.label ? " · " + esc(e.label) : "") + "</span></div>").join("");
    h += "</div>";
    show(h);
  }

  // --- intake: a dropped file, a browsed URL, or an LLM connection — one "+"
  // menu, one output surface, the same pipeline a pasted policy uses --------

  function openIntakeMenu() {
    const h = '<div class="say-card"><div class="say-h">Add to this workspace</div>'
      + '<div class="say-actions" style="flex-direction:column;align-items:stretch;gap:6px">'
      + '<button class="say-btn" id="say-menu-file" style="text-align:left">📄 Add context — a file <span style="color:var(--txt-dim);font-weight:400">→ Versum, privacy-locked</span></button>'
      + '<button class="say-btn" id="say-menu-url" style="text-align:left">🌐 Add context — a web page <span style="color:var(--txt-dim);font-weight:400">→ Versum, privacy-locked</span></button>'
      + '<button class="say-btn" id="say-menu-cmds" style="text-align:left">⌘ Commands <span style="color:var(--txt-dim);font-weight:400">— every op this can run</span></button>'
      + '<button class="say-btn" id="say-menu-llm" style="text-align:left">🤖 Connect an LLM</button>'
      + "</div>"
      + '<div class="say-sub" style="margin-top:8px">To add a <b>policy</b> instead — one that becomes an operational rule (a prohibition, a reservation) — paste it here, or type <code>ingest …</code>. Policy ingest validates the result through RVND’s pinned Loomground Governance and Deontic language adapters before any confirmed apply.</div>'
      + '<div class="say-actions"><button class="say-btn" data-say="cancel">Close</button></div></div>';
    show(h);
    const o = out();
    const cancel = o.querySelector('[data-say="cancel"]'); if (cancel) cancel.addEventListener("click", clear);
    const fb = o.querySelector("#say-menu-file"); if (fb) fb.addEventListener("click", () => { const fi = doc.getElementById("say-file"); if (fi) fi.click(); });
    const ub = o.querySelector("#say-menu-url"); if (ub) ub.addEventListener("click", openUrlForm);
    const cb = o.querySelector("#say-menu-cmds"); if (cb) cb.addEventListener("click", () => palette(""));
    const lb = o.querySelector("#say-menu-llm"); if (lb) lb.addEventListener("click", openLlmSetup);
  }

  // Connect an LLM — status only, never a key field. RVND routes to any
  // OpenAI-compatible endpoint via env vars read once at process start
  // (local_llm.py); this panel shows whether one is reachable and how to set
  // it up from a terminal, never through this app.
  async function openLlmSetup() {
    show('<div class="say-card"><div class="say-h">Connect an LLM</div><div class="say-sub">checking the configured endpoint…</div></div>');
    const st = await call("workspace_model", { op: "status", params: { probe_endpoint: true } }).catch(() => null);
    const ep = st && st.endpoint;
    const connected = !!(ep && ep.reachable);
    let h = '<div class="say-card"><div class="say-h">Connect an LLM</div>';
    h += '<div class="say-sub">' + (connected
      ? "connected — <b>" + esc(ep.endpoint || "") + "</b>" + ((ep.models || []).length ? " · " + ep.models.length + " model" + (ep.models.length === 1 ? "" : "s") + " available" : "")
      : "not connected" + (ep && ep.error ? " — " + esc(ep.error) : "")) + "</div>";
    h += '<div class="say-sub" style="margin-top:8px">This app never asks for a key here. RVND routes to any OpenAI-compatible endpoint — Ollama, LM Studio, llama.cpp, vLLM, or a hosted API — through environment variables read once when the server starts. Set them in a terminal, then restart <code>app/serve.py</code>:</div>';
    h += '<pre style="white-space:pre-wrap;font-size:10.5px;color:var(--txt);background:var(--panel-2);border:1px solid var(--line);border-radius:6px;padding:8px;margin-top:6px">'
      + "export WORKSPACE_LOCAL_LLM_URL=\"http://localhost:1234/v1\"\n"
      + "export WORKSPACE_LOCAL_LLM_MODEL=\"your-model-name\"\n"
      + "# only if the endpoint needs one — leave unset for a pure-local model:\n"
      + "export WORKSPACE_LOCAL_LLM_API_KEY=\"…\"</pre>";
    h += '<div class="say-sub" style="margin-top:8px">Keep a real key out of shell history entirely — store it in the OS keychain once, then read it back into the shell that starts the server (macOS):</div>';
    h += '<pre style="white-space:pre-wrap;font-size:10.5px;color:var(--txt);background:var(--panel-2);border:1px solid var(--line);border-radius:6px;padding:8px;margin-top:6px">'
      + "read -s -p \"API key: \" KEY \\\n  && security add-generic-password -a \"$USER\" -s rvnd-llm-api-key -w \"$KEY\" -U \\\n  && unset KEY\n\n"
      + "export WORKSPACE_LOCAL_LLM_API_KEY=\"$(security find-generic-password -a \"$USER\" -s rvnd-llm-api-key -w)\"</pre>";
    h += '<div class="say-actions"><button class="say-btn" id="say-llm-recheck">Re-check</button><button class="say-btn" data-say="cancel">Close</button></div></div>';
    show(h);
    const o = out();
    const cancel = o.querySelector('[data-say="cancel"]'); if (cancel) cancel.addEventListener("click", clear);
    const rc = o.querySelector("#say-llm-recheck"); if (rc) rc.addEventListener("click", openLlmSetup);
  }


  // Upload — write_file_to_folder's contract (via workspace_folder write_file)
  // is UTF-8 text; a File's bytes are read as text client-side, so this is
  // scoped to text-ish documents (.txt/.md/.csv/.json/.lg), not binary/PDF —
  // that needs a base64 path this facade doesn't expose yet.
  async function uploadFile(file) {
    const fc = focus();
    if (!fc) { show('<div class="say-err">open a workspace first</div>'); return; }
    if (!file) return;
    show('<div class="say-card"><div class="say-h">reading ' + esc(file.name) + "…</div></div>");
    let text;
    try { text = await file.text(); } catch (e) { show('<div class="say-err">could not read “' + esc(file.name) + '” as text: ' + esc(e && e.message || e) + "</div>"); return; }
    const w = await call("workspace_folder", { op: "write_file", params: { folder_context: fc, relative_path: file.name, content: text, actor: "app-user" } }).catch((e) => ({ error: String(e && e.message || e) }));
    if (!w || w.error) { show('<div class="say-err">upload failed: ' + esc((w && w.error) || "unknown") + "</div>"); return; }
    show('<div class="say-card"><div class="say-h">ingesting ' + esc(file.name) + "…</div></div>");
    const r = await call("workspace_ingest", { op: "path", params: { folder_context: fc, file_path: w.path } }).catch((e) => ({ error: String(e && e.message || e) }));
    if (!r || r.error) { show('<div class="say-err">ingest failed: ' + esc((r && r.error) || "unknown") + "</div>"); return; }
    show('<div class="say-card"><div class="say-h">ingested ' + esc(file.name) + "</div>"
      + '<div class="say-sub">' + (r.idempotent_noop ? "already ingested — no new facts" : (r.count || 0) + " fact" + (r.count === 1 ? "" : "s") + " extracted") + "</div></div>");
    await store.hydrate();
  }

  // Browser connector — the server fetches a user-chosen URL (robots-permitting)
  // and ingests it exactly like a dropped file (workspace_ingest op url).
  function openUrlForm() {
    const h = '<div class="say-card"><div class="say-h">Fetch a URL</div>'
      + '<div class="say-sub">the server fetches it (robots-permitting) and ingests the content, same as a dropped file.</div>'
      + '<div style="display:flex;gap:8px;margin-top:8px"><input id="say-url-in" type="text" placeholder="https://…" style="flex:1;background:var(--panel-2);border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:6px 8px;font-size:12px">'
      + '<button class="say-btn say-go" id="say-url-go">Fetch</button><button class="say-btn" data-say="cancel">Cancel</button></div></div>';
    show(h);
    const o = out();
    const cancel = o.querySelector('[data-say="cancel"]'); if (cancel) cancel.addEventListener("click", clear);
    const inp = o.querySelector("#say-url-in"); if (inp) inp.focus();
    const submitUrl = () => fetchUrl(inp ? inp.value : "");
    const go = o.querySelector("#say-url-go"); if (go) go.addEventListener("click", submitUrl);
    if (inp) inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); submitUrl(); } });
  }
  async function fetchUrl(url) {
    const fc = focus();
    if (!fc) { show('<div class="say-err">open a workspace first</div>'); return; }
    const u = String(url || "").trim(); if (!u) return;
    show('<div class="say-card"><div class="say-h">fetching ' + esc(u) + "…</div></div>");
    const r = await call("workspace_ingest", { op: "url", params: { folder_context: fc, url: u, actor: "app-user" } }).catch((e) => ({ error: String(e && e.message || e) }));
    if (!r || r.error) { show('<div class="say-err">fetch failed: ' + esc((r && r.error) || "unknown") + "</div>"); return; }
    const STATE_MSG = {
      fetched: "fetched and ingested", unchanged: "unchanged since last fetch",
      robots_blocked: "blocked by robots.txt — not fetched",
      tdm_reserved: "the source opts out of AI/ML use (TDM reservation) — not fetched",
      fetch_error: "could not be fetched",
    };
    show('<div class="say-card"><div class="say-h">' + esc(u) + "</div>"
      + '<div class="say-sub">' + esc(STATE_MSG[r.state] || r.state || "unknown") + (r.http_status ? " · HTTP " + esc(r.http_status) : "")
      + ((r.pair_ids || []).length ? " · " + r.pair_ids.length + " fact" + (r.pair_ids.length === 1 ? "" : "s") : "") + "</div></div>");
    await store.hydrate();
  }

  // --- chat with the policy: retrieval over the ingested corpus -------------

  // workspace_memory.safe_context_query is the existing "chat-with-folder"
  // spine — keyword search + the same lock-scrubbed safe view local_llm
  // prompts already use. It falls back to recent() when the search itself
  // finds nothing, so count > 0 alone doesn't mean "actually relevant" — the
  // returned triples are re-scored against the query the same way tryGraph
  // scores node labels, and a miss there is treated as no match, never shown
  // as a false hit.
  async function retrieveContext(text) {
    const fc = focus(); if (!fc) return false;
    const r = await call("workspace_memory", { op: "safe_context_query", params: { folder_context: fc, query: text, k: 6 } }).catch(() => null);
    if (!r || r.error || !Array.isArray(r.views) || !r.views.length) return false;
    const qWords = graphTokens(text);
    if (!qWords.length) return false;
    const hits = r.views.filter((v) => {
      const words = graphTokens((v.triples || []).map((t) => t.join(" ")).join(" ") + " " + JSON.stringify(v.fingerprint || {}));
      return words.some((w) => qWords.includes(w));
    });
    if (!hits.length) return false;
    await showRetrieval(text, hits);
    return true;
  }

  async function showRetrieval(query, views) {
    let h = '<div class="say-card"><div class="say-h">the policy — matches for “' + esc(query) + '”</div>'
      + '<div class="say-sub">' + views.length + " matching fact" + (views.length === 1 ? "" : "s") + " from what’s been ingested here.</div>";
    views.forEach((v) => {
      const fp = v.fingerprint || {};
      const src = fp.source_document || fp.source_urn || fp.issue_type || "";
      if (src) h += '<div class="say-sub" style="margin-top:8px;color:var(--txt)"><b>' + esc(base(String(src))) + "</b></div>";
      (v.triples || []).slice(0, 4).forEach((t) => {
        h += '<div class="say-kv"><span class="k">' + esc(t[1] || "") + '</span><span class="v">' + esc(t[0] || "") + " → " + esc(t[2] || "") + "</span></div>";
      });
    });
    h += '<div class="say-actions" id="say-explain-row"></div></div>';
    show(h);
    // the AI-synthesis step is opt-in and only offered when a capable local
    // model is actually registered — never faked, never a silent cloud call
    const cap = await call("workspace_workflow", { op: "model_capability", params: { task: "completion" } }).catch(() => null);
    const o = out(); const row = o && o.querySelector("#say-explain-row");
    if (!row) return;
    const ready = !!(cap && cap.action === "run_local");
    row.innerHTML = '<button class="say-btn' + (ready ? " say-go" : "") + '" id="say-explain"'
      + (ready ? "" : ' disabled title="' + esc((cap && cap.reason) || "no capable local model registered — showing matched facts only") + '"')
      + ">✨ Explain with AI (local model, opt-in)</button>";
    const btn = row.querySelector("#say-explain");
    if (btn && ready) btn.addEventListener("click", () => explainWithAI(query, views));
  }

  // Synthesizes an answer from ONLY the already-retrieved, already-scrubbed
  // triples — never re-reads the source documents, never leaves this folder's
  // local model endpoint. The prompt carries facts, not raw document text.
  async function explainWithAI(query, views) {
    const fc = focus();
    const facts = views.flatMap((v) => (v.triples || []).slice(0, 4).map((t) => "- " + t[0] + " " + t[1] + " " + t[2])).slice(0, 20).join("\n");
    const prompt = "Answer the question using ONLY the facts below. If the facts do not answer it, say so plainly.\n\nFacts:\n" + facts + "\n\nQuestion: " + query + "\nAnswer:";
    show('<div class="say-card"><div class="say-h">asking the local model…</div></div>');
    const r = await call("workspace_model", { op: "complete", params: { prompt, folder_context: fc, max_tokens: 300 } }).catch((e) => ({ ok: false, error: String(e && e.message || e) }));
    if (!r || r.ok === false) { show('<div class="say-err">could not complete: ' + esc((r && r.error) || "unknown") + "</div>"); return; }
    show('<div class="say-card"><div class="say-h">' + esc(query) + "</div>"
      + '<div class="say-sub">' + esc(r.response || "") + "</div>"
      + '<div class="say-sub" style="margin-top:6px;color:var(--txt-dim)">via ' + esc(r.model_used || "local model") + (r.captured ? " · recorded" : "") + "</div></div>");
  }

  // --- the op palette (`?`) --------------------------------------------------

  async function palette(filter) {
    const idx = await index();
    const names = Object.keys(idx).filter((n) => !filter || n.indexOf(filter) >= 0).sort();
    const rows = names.slice(0, 60).map((n) => {
      const e = idx[n];
      return '<tr><td>' + esc(n) + '</td><td>' + esc(base(e.tool).replace("workspace_", "")) + "</td><td>"
        + (e.entry.mutates ? "writes" : "reads") + "</td></tr>";
    }).join("");
    // the standalone query verbs are not in the op registry — list them first so
    // they are as discoverable as any op.
    const sc = STANDALONE_CMDS.filter((c) => !filter || c.verb.indexOf(filter) >= 0);
    const scRows = sc.map((c) =>
      '<tr><td>' + esc(c.verb) + " …</td><td>" + esc(c.tool.replace("workspace_", "").replace("cross_workspace_read", "cross")) + "</td><td>query</td></tr>").join("");
    show('<div class="say-card"><div class="say-h">ops (' + (names.length + sc.length) + ") — type an op name to run it</div>"
      + '<div class="say-scroll"><table class="say-tbl"><tr><th>op</th><th>tool</th><th></th></tr>' + scRows + rows + "</table></div></div>");
  }

  // --- the one entry point ---------------------------------------------------

  async function submit(raw) {
    const text = String(raw == null ? "" : raw).trim();
    if (!text) return;
    const hist = store.getState().chatContext.history || [];
    store.setState({ chatContext: Object.assign({}, store.getState().chatContext, { history: hist.concat([text]) }) });

    if (text === "?" || text.startsWith("? ")) { await palette(text.slice(1).trim()); return; }

    // explicit ingest command forces the grower's policy intent
    const ing = /^ingest\s+([\s\S]+)/i.exec(text);
    if (ing) { await grow(ing[1].trim(), "policy"); return; }

    // standalone (non-op) query verbs — run against the focused workspace and
    // render the governed result like any read.
    const sv = /^([a-z-]+)\s+([\s\S]+)$/.exec(text);
    const scmd = sv && STANDALONE_CMDS.find((c) => c.verb === sv[1]);
    if (scmd) {
      if (!focus()) { show('<div class="say-err">Select a workspace first.</div>'); return; }
      show('<div class="say-card"><div class="say-h">' + esc(scmd.verb) + " · running…</div></div>");
      const r = await call(scmd.tool, scmd.mk(sv[2].trim(), focus())).catch((e) => ({ error: String(e && e.message || e) }));
      show(renderResult(scmd.verb, r));
      return;
    }

    // command grammar: `tool op …` or a bare op resolved through the registry
    const tokens = text.split(/\s+/);
    let tool = null, op = null, rest = [];
    if (OP_TOOLS.includes(tokens[0]) && tokens[1]) { tool = tokens[0]; op = tokens[1]; rest = tokens.slice(2); }
    else { op = tokens[0]; rest = tokens.slice(1); }

    const hit = await resolve(tool, op).catch(() => null);
    if (!hit) {
      // not a command — try the ingested corpus first (the literal facts a
      // policy/document actually states), then the rule graph (structural
      // questions about rules/instruments/roles), else the intent router
      // (governance_chat)
      if (await retrieveContext(text).catch(() => false)) return;
      if (await tryGraph(text).catch(() => false)) return;
      await grow(text, null);
      return;
    }
    const params = parseParams(rest, hit.entry);
    if (hit.entry.mutates) { renderConfirm(hit.tool, op, params, hit.entry); return; }
    // a read runs and renders structured
    show('<div class="say-card"><div class="say-h">' + esc(op) + " · running…</div></div>");
    const r = await call(hit.tool, { op, params }).catch((e) => ({ error: String(e && e.message || e) }));
    show(renderResult(op, r));
  }

  return { submit, clear, grow, palette, resolve, _index: index, uploadFile, openUrlForm, fetchUrl, retrieveContext, openIntakeMenu, openLlmSetup };
}
