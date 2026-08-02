// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Sources & gaps (grounder) panel — the second pack entry behind
// docs/loomground-proposals/panel-mount-contract.md. Read-only: it traces
// where claims come from and where attribution is missing. Gaps render as
// discrete counts, never a 0-1 completeness dial; this bundle calls only
// workspace_grounder's read ops (coverage, bibliography, swarm.frontier,
// oversight.feed).
Patchbay.register("grounder", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc } = ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Read-only. Traces <b>where claims come from</b> and where attribution " +
      "is missing. It records provenance — it makes <b>no citation and no " +
      "claim of truth.</b>";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "grout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    if (!ctx.workspace.path) {
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to trace its attribution</div>';
      return;
    }
    const card = (t, b, k) =>
      '<div class="finding ' + (k || "info") + '" style="margin-bottom:8px">' +
      '<span class="ttl">' + t + "</span>" + b + "</div>";
    const get = async (op) => {
      try {
        return await tool("workspace_grounder", { op, params: { folder_context: ctx.workspace.path } });
      } catch (e) {
        return { error: (e && e.message) || "failed" };
      }
    };
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">tracing…</div>';
    const [cov, bib, fr, feed] = await Promise.all([
      get("coverage"), get("bibliography"), get("swarm.frontier"), get("oversight.feed"),
    ]);
    let h = "";
    if (cov.error) {
      h += card("Coverage — could not trace", esc(cov.error), "warn");
    } else {
      const works = cov.works || 0, claims = cov.claims || 0;
      h += card("What is grounded",
        esc(works) + " work(s) · " + esc(claims) + " claim(s)" +
        (cov.ok ? ' · <b style="color:#92c4ac">no gaps</b>' : ""), "info");
      // gaps shown as discrete counts (NOT a 0-1 completeness dial)
      const gaps = [
        ["works missing creators", (cov.works_missing_creators || []).length],
        ["works missing source link", (cov.works_missing_link || []).length],
        ["works missing date", (cov.works_missing_date || []).length],
        ["untraced works", (cov.untraced_works || []).length],
        ["claims without evidence", (cov.claims_without_evidence || []).length],
        ["verified-but-no-evidence", (cov.verified_without_evidence || []).length],
        ["support failures", (cov.support_failures || []).length],
        ["overlong quotes", (cov.overlong_quotes || []).length],
        ["web works missing fixity", (cov.web_works_missing_fixity || []).length],
        ["disputed residuals", (cov.disputed_residuals || []).length],
      ].filter((g) => g[1] > 0);
      if (gaps.length) h += card("⚠ Attribution gaps", gaps.map((g) => esc(g[1]) + " " + esc(g[0])).join(" · "), "warn");
      else if (works || claims) h += card("✓ No attribution gaps", "every work has creators, a source link, a date and a trace", "ok");
      if (cov.claims_by_status && Object.keys(cov.claims_by_status).length)
        h += card("Claims by status", Object.entries(cov.claims_by_status).map(([k, v]) => esc(k) + ": " + esc(v)).join(" · "), "info");
    }
    if (!bib.error) h += card("Bibliography", esc(bib.count || 0) + " entr" + ((bib.count || 0) === 1 ? "y" : "ies") + (bib.style ? " · " + esc(bib.style) + " style" : ""), "info");
    if (!fr.error) h += card("Research frontier", esc(fr.count || 0) + " open lead(s) of " + esc(fr.total_works || 0) + " work(s)", "info");
    if (!feed.error) h += card("Oversight feed", (feed.flagged ? '<b style="color:#e6b483">' + esc(feed.flagged) + " flagged</b> of " : "") + esc(feed.count || 0) + " grounding event(s)", feed.flagged ? "warn" : "info");
    h += '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:2px">Per-work trace and per-claim status open from a work id (drill-down). This view attributes provenance; it does not assert any claim is true.</div>';
    out.innerHTML = h;
  },
});
