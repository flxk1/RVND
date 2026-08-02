// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026 flxk1
//
// Standing facts (legal) panel — the pilot pack entry for
// docs/loomground-proposals/panel-mount-contract.md. Read-only: it lists the
// subject cards signed into the workspace record. Saving a card, recording
// standing facts and running the class-C certify/escalate/refuse pipeline
// are writes and are not exposed here — this bundle calls only card.list.
Patchbay.register("legal", {
  async open(ctx) {
    const { host, tool, ui } = ctx;
    const { esc, escA } = ui;

    const intro = document.createElement("div");
    intro.className = "ro";
    intro.style.cssText = "font-size:11px;color:var(--txt-dim);margin:6px 0";
    intro.innerHTML =
      "Read-only view of the <b>subject cards</b> — one per assessed entity, " +
      "each a set of typed standing facts. Saving a card, recording standing " +
      "facts and running the class-C pipeline are <b>not exposed here</b> " +
      "— this view <b>reads</b> the cards, it never changes them.";
    host.appendChild(intro);

    const out = document.createElement("div");
    out.id = "lgout";
    out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">loading…</div>';
    host.appendChild(out);

    const card = (t, b, k) =>
      '<div class="finding ' + (k || "info") + '" style="margin-bottom:8px">' +
      '<span class="ttl">' + t + "</span>" + b + "</div>";

    if (!ctx.workspace.path) {
      out.innerHTML = '<div class="ro" style="color:var(--txt-dim);font-size:11px">open a folder to read its subject cards</div>';
      return;
    }
    let r;
    try {
      r = await tool("workspace_legal", { op: "card.list", params: { folder_context: ctx.workspace.path } });
    } catch (e) {
      r = { error: (e && e.message) || "failed" };
    }
    let h = "";
    if (r.error || r.ok === false) {
      h += card("Subject cards — could not read", esc(r.error || "unavailable"), "warn");
    } else {
      const cards = r.cards || [];
      if (!cards.length) {
        h += card("Subject cards", "no subject cards in this folder yet", "info");
      } else {
        const rows = cards
          .map((id) => '<div class="path" title="' + escA(esc(String(id))) + '">' + esc(String(id)) + "</div>")
          .join("");
        h += card("Subject cards", esc(cards.length) + " entity card(s) in this workspace" + rows, "info");
      }
    }
    h +=
      '<div class="ro" style="font-size:10px;color:var(--txt-dim);margin-top:2px">' +
      "Saving cards, recording standing facts and running the class-C certify/escalate/refuse " +
      "pipeline are deferred — they are writes. A subject card <b>records</b> standing legal " +
      "facts about an entity; it does <b>not</b> certify legal compliance.</div>";
    out.innerHTML = h;
  },
});
