// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
// Governance-map panel renderer — a PURE function of the governance_map/v1 contract.
// It renders ONLY what the contract's resolve()/serve() payload provides: the filter chips
// come from payload.facets, the collapsible bars from payload.groups (+ their roll-ups), the
// deep-link target from payload.focus_target. It invents no axis and hardcodes no rule — so
// the panel cannot drift from the schema. assertContract() version-gates: a payload of the
// wrong version is refused, never rendered against a guessed shape.

export const SCHEMA_VERSION = "governance_map/v1";

export function assertContract(payload) {
  if (!payload || typeof payload !== "object") throw new Error("governance_map: empty payload");
  if (payload.version !== SCHEMA_VERSION)
    throw new Error("governance_map: version mismatch — panel is " + SCHEMA_VERSION +
                    ", payload is " + payload.version);
  for (const k of ["summary", "facets", "groups", "grouped_by"])
    if (!(k in payload)) throw new Error("governance_map: payload missing '" + k + "'");
  return true;
}

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function chips(facets, active) {
  // filter chips are built from the FULL-map facet values — never a hardcoded list
  return Object.keys(facets).map((facet) => {
    const on = active[facet];
    const opts = facets[facet].map((v) => {
      const sel = on && (on === v || (Array.isArray(on) && on.includes(v)));
      return `<button class="gm-chip${sel ? " gm-chip-on" : ""}" data-facet="${esc(facet)}" data-value="${esc(v)}">${esc(v)}</button>`;
    }).join("");
    return `<div class="gm-chiprow"><span class="gm-chiplabel">${esc(facet)}</span>${opts}</div>`;
  }).join("");
}

function bar(g, focusGroup) {
  const r = g.group, hot = focusGroup && r.key === focusGroup;
  const badge = (n, cls, lbl) => n ? `<span class="gm-b ${cls}">${n} ${lbl}</span>` : "";
  const rows = g.rules.map((x) =>
    `<div class="gm-row" data-rule="${esc(x.rule_id)}">
       <span class="gm-pin">${esc(x.pinpoint)}</span>
       <span class="gm-inst">${esc(x.instrument)}</span>
       <span class="gm-duty">${esc(x.role || "—")} · ${esc(x.duty)}</span>
       <span class="gm-b">${esc(x.risk_tier || "any")}</span>
       <span class="gm-b">${esc(x.operator)}</span>
       <span class="gm-st gm-st-${esc(x.coverage === "empty" ? "empty" : x.needs_interpreter ? "interp" : "ok")}"></span>
     </div>`).join("");
  return `<div class="gm-group${hot ? " gm-focus" : ""}">
    <button class="gm-bar" data-group="${esc(r.key)}" aria-expanded="false">
      <span class="gm-key">${esc(r.key)}</span>
      <span class="gm-b gm-ok">${r.count} rules</span>
      ${badge(r.empty, "gm-warn", "empty")}${badge(r.interpreter, "gm-mut", "interp")}${badge(r.prohibited, "gm-dang", "prohibit")}
    </button>
    <div class="gm-rows" hidden>${rows}</div>
  </div>`;
}

// Render the whole panel from one contract payload. Returns an HTML string.
export function renderMap(payload) {
  assertContract(payload);
  const s = payload.summary, v = payload.view || {}, ft = payload.focus_target;
  const focusGroup = ft ? ft.group_key : null;
  const stat = (n, lbl) => `<div class="gm-stat"><div class="gm-stat-n">${Number(n) || 0}</div><div class="gm-stat-l">${esc(lbl)}</div></div>`;
  return `<div class="gm-panel" data-version="${esc(payload.version)}" data-grouped="${esc(payload.grouped_by)}">
    <div class="gm-summary">${stat(s.total, "rules")}${stat(s.empty, "empty (gap)")}${stat(s.interpreter, "interpreter")}${stat(s.prohibited, "prohibited")}</div>
    <div class="gm-chips">${chips(payload.facets, v.filters || {})}</div>
    <div class="gm-tree">${payload.groups.map((g) => bar(g, focusGroup)).join("")}</div>
    ${ft && !ft.group_key ? `<div class="gm-note">focused rule ${esc(ft.rule_id)} is outside the current filter</div>` : ""}
  </div>`;
}

export default { SCHEMA_VERSION, assertContract, renderMap };
