# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Coverage fixtures for operations promoted from deferred to mcp-supported.

Each operation here is driven through the MCP facade to a schema-conforming
success (with a tightening ``check``) and a refused invalid, verified by
execution. No mocks. Kept separate from the base fixtures so the promotion set
is reviewable as one block; merged into FIXTURES at the end of
coverage_fixtures.py.
"""
from __future__ import annotations

from pathlib import Path

try:
    from .coverage_harness import WS, Fixture, empty_result, with_parties
    from .coverage_fixtures import ACTOR, _p, _bundle, _gf
except ImportError:  # top-level (subprocess runner)
    from coverage_harness import WS, Fixture, empty_result, with_parties
    from coverage_fixtures import ACTOR, _p, _bundle, _gf


# =========================================================================
# legal + ingest + contract
# =========================================================================

DPA_TEXT = (
    "DATA PROCESSING AGREEMENT\n\n"
    "This Data Processing Agreement is made between Norddata Services GmbH (the\n"
    '"Processor") and Beispielkunde AG (the "Controller") under Article 28 GDPR.\n\n'
    "This Agreement is effective as of 2026-07-01.\n\n"
    "1. The Processor shall notify the Controller of a personal data breach no\n"
    "later than 72 hours after the personal data breach.\n\n"
    "2. The Processor must not engage a Sub-processor without the prior written\n"
    "authorisation of the Controller.\n")

_GOOD_ATOMS = [
    {"role": "norm", "ref": "Art.9", "source": "CELEX:32024R1689", "authority_tier": 2},
    {"role": "tatbestand", "ref": "tb", "source": "CELEX:32024R1689", "authority_tier": 2},
    {"role": "subsumtion", "ref": "sub", "source": "CELEX:32024R1689", "authority_tier": 2},
    {"role": "ergebnis", "ref": "erg", "source": "CELEX:32024R1689", "authority_tier": 2},
]
_PIPE_CORPUS = [{"id": "d1", "text": "Der Anbieter muss ein Risikomanagementsystem einrichten."},
                {"id": "d2", "text": "Allgemeine Hinweise zur Anwendung."}]
_CONF_PAIR = {"id": "c1", "problem": {"id": "c1-p", "type": "rule", "facets": {
                  "modal": "muss", "has_exception": False,
                  "applicability": {"role": "provider"}, "jurisdiction": ["EU"]}},
              "solution": {"id": "c1", "authority_tier": 1, "confidence": 0.95,
                  "source": "CELEX:32024R1689 Art. 9",
                  "temporal": {"status": "in-force", "date_source": "registry"}},
              "edges": []}


def _card_saved(ws: WS) -> dict:
    with_parties(ws)
    import workspaces.mcp_server as M
    r = M.workspace_legal("card.save", {
        "folder_context": ws.folder, "log_root": ws.log_root,
        "card": {"domain": "invoice", "subject_id": "acme",
                 "facets": {"vat_status": "reverse-charge"}}})
    return {"subject_id": r.get("subject_id", "acme")}


def _ingestable_folder(ws: WS) -> dict:
    with_parties(ws)
    from workspaces.workspace_registry import add_known_workspace
    add_known_workspace(ws.folder)
    fp = Path(ws.folder) / "note.md"
    fp.write_text("GDPR Article 28 applies to this processing.", encoding="utf-8")
    return {"file_path": str(fp)}


def _stem_ingested(ws: WS) -> dict:
    with_parties(ws)
    import workspaces.mcp_server as M
    fp = Path(ws.folder) / "kick.wav"
    fp.write_text("RIFF....audio....", encoding="utf-8")
    r = M.workspace_ingest("stem", {"folder_context": ws.folder, "file_path": str(fp),
                                    "origin": "played", "log_root": ws.log_root})
    return {"stem_hash": r.get("stem_hash", "")}


def _plugin_root(ws: WS) -> dict:
    with_parties(ws)
    d = Path(ws.folder) / "plugin" / "skills" / "greet"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: greet\ndescription: Greet the user warmly when asked to say hello.\n"
        "version: 1.0.0\n---\n\n# Greet\n\nSay hello to the user in a friendly way.\n",
        encoding="utf-8")
    return {"plugin_root": str(Path(ws.folder) / "plugin")}


def _dpa_contract(ws: WS) -> dict:
    with_parties(ws)
    import workspaces.mcp_server as M
    M.workspace_contract("ingest", {"folder_context": ws.folder, "text": DPA_TEXT,
                                    "contract_id": "dpa-x", "actor": ACTOR})
    return {"contract_ref": "dpa-x@1"}


def _dpa_obligation(ws: WS) -> dict:
    import workspaces.mcp_server as M
    _dpa_contract(ws)
    st = M.workspace_contract("state", {"folder_context": ws.folder})
    obs = st.get("obligations", [])
    return {"obligation_id": obs[0]["obligation_id"] if obs else ""}


def _wav(ws: WS, name: str, body: str) -> str:
    p = Path(ws.folder) / name
    p.write_text(body, encoding="utf-8")
    return str(p)


# =========================================================================
# session + mirror
# =========================================================================

def _second_folder(ws: WS) -> Path:
    p = Path(ws.root) / "ws2"
    p.mkdir(exist_ok=True)
    try:
        from workspaces.workspace_registry import add_known_workspace
        add_known_workspace(p, log_root=Path(ws.log_root))
    except Exception:
        pass
    return p


def _session_saved(ws: WS) -> dict:
    with_parties(ws)
    import workspaces.mcp_server as M
    path = str(Path(ws.root) / "sess.rvnd")
    M.workspace_session("save", {
        "workspaces": [{"folder_context": ws.folder, "id": "ws1", "name": "ws1"}],
        "rail": {}, "path": path, "name": "sess"})
    return {"path": path, "workspace_id": "ws1"}


def _session_import(ws: WS) -> dict:
    with_parties(ws)
    import workspaces.mcp_server as M
    f2 = _second_folder(ws)
    env_path = str(Path(ws.root) / "env.rvnd")
    track_path = str(Path(ws.root) / "track.rvnd")
    M.workspace_session("save", {
        "workspaces": [{"folder_context": ws.folder, "id": "ws1", "name": "ws1"}],
        "rail": {}, "path": env_path, "name": "env"})
    M.workspace_session("save", {
        "workspaces": [{"folder_context": str(f2), "id": "ws2", "name": "ws2"}],
        "rail": {}, "path": track_path, "name": "track"})
    return {"env_path": env_path, "track_path": track_path,
            "out_path": str(Path(ws.root) / "merged.rvnd")}


def _session_template(ws: WS) -> dict:
    from workspaces import session_templates
    tpls = session_templates.list_templates()
    return {"template_id": tpls[0]["id"] if tpls else ""}


def _mk_src(ws: WS, name: str, body: str) -> str:
    p = Path(ws.folder) / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def _mirror_gen(ws: WS) -> dict:
    with_parties(ws)
    import workspaces.mcp_server as M
    src = _mk_src(ws, "note.md", "Contact jane.doe\x40example.com or call (555) 123-4567.\n")
    r = M.workspace_mirror("generate", {
        "folder_context": ws.folder, "source_path": src, "actor": ACTOR})
    return {"mirror_path": r["mirror_path"], "source_path": src}


def _mirror_open(ws: WS) -> dict:
    ctx = _mirror_gen(ws)
    from workspaces import mirror_editor
    rd = mirror_editor.open_revision(ws.folder, ctx["mirror_path"],
                                     actor="system:editor", log_root=Path(ws.log_root))
    spans = rd.spans or []
    ctx["span_id"] = spans[0].get("span_id", "") if spans else ""
    return ctx


def _mirror_locked(ws: WS) -> dict:
    ctx = _mirror_gen(ws)
    import workspaces.mcp_server as M
    M.workspace_mirror("lock_acquire", {
        "folder_context": ws.folder, "mirror_path": ctx["mirror_path"], "actor": ACTOR})
    return ctx


# =========================================================================
# matrix + grounder + lens + capture + model
# =========================================================================

def _work_with_creator(ws: WS) -> dict:
    with_parties(ws)
    import workspaces.mcp_server as M
    M.workspace_grounder("work.register", {
        "folder_context": ws.folder, "title": "A traced work",
        "creators": [{"name": "Ada Lovelace"}]})
    return {"subject": "Ada Lovelace"}


# =========================================================================
# erase + folder + lock + policy + workspace
# =========================================================================

def _erase_request(ws: WS) -> dict:
    with_parties(ws)
    import workspaces.mcp_server as M
    r = M.workspace_erase("request", {
        "folder_context": ws.folder, "subject": "Jane Roe",
        "requester_ref": "DSAR-1", "reason": "consent withdrawn"})
    return {"request_id": r.get("request_id")}


def _sealed(ws: WS) -> dict:
    import workspaces.seal as _seal
    with_parties(ws)
    _seal.seal_folder(ws.folder, passphrase="cov-pass", log_root=Path(ws.log_root))
    return {"passphrase": "cov-pass"}


def _registered(ws: WS) -> dict:
    """Register the disposable folder in the (tmp) default registry so the
    allowlist gate admits it — without relaxing the principal gate."""
    with_parties(ws)
    from workspaces.workspace_registry import add_known_workspace
    add_known_workspace(ws.folder)
    return {}


def _tmp_file(ws: WS) -> dict:
    with_parties(ws)
    from workspaces.workspace_registry import add_known_workspace
    add_known_workspace(ws.folder)
    p = Path(ws.folder) / "cov_doc.txt"
    p.write_text("Party A shall deliver the report by 2026-08-01.\n", encoding="utf-8")
    return {"file_path": str(p)}


def _added_ws(ws: WS) -> dict:
    with_parties(ws)
    import workspaces.mcp_server as M
    extra = str(Path(ws.root) / "ws_extra"); Path(extra).mkdir(exist_ok=True)
    M.workspace_workspace("add", {"folder_context": extra})
    return {"extra": extra}


def _fresh_child(ws: WS) -> dict:
    with_parties(ws)
    return {"child": str(Path(ws.root) / "child_ws")}


def _bootstrap_targets(ws: WS) -> dict:
    with_parties(ws)
    afile = Path(ws.root) / "afile"; afile.write_text("x", encoding="utf-8")
    return {"target": str(Path(ws.root) / "defws"), "bad_target": str(afile / "sub")}


def _lock_setup_paths(ws: WS) -> dict:
    from workspaces.lock.onboarding.config import load_config, save_config
    prior = Path(ws.root) / "lock_prior.json"
    c = load_config(prior)
    c.setup_completed_at = "2026-01-01T00:00:00Z"
    c.backend_spec = "ollama:llama3"
    save_config(c, path=prior)
    return {"fresh_cfg": str(Path(ws.root) / "lock_fresh.json"), "prior_cfg": str(prior)}


EXTRA: dict[tuple, Fixture] = {
    # ---- workspace_legal ----
    ("workspace_legal", "card.list"): Fixture(setup=_card_saved,
        valid=lambda ws, c: {"folder_context": ws.folder},
        invalid=lambda ws, c: {}, check=lambda r: isinstance(r.get("cards"), list)),
    ("workspace_legal", "card.save"): Fixture(setup=lambda ws: with_parties(ws) and {},
        valid=lambda ws, c: {"folder_context": ws.folder, "log_root": ws.log_root,
            "card": {"domain": "invoice", "subject_id": "acme", "facets": {"vat_status": "reverse-charge"}}},
        invalid=lambda ws, c: {}, check=lambda r: bool(r.get("audit_id")), mutating=True),
    ("workspace_legal", "card.load"): Fixture(setup=_card_saved,
        valid=lambda ws, c: {"folder_context": ws.folder, "subject_id": c["subject_id"]},
        invalid=lambda ws, c: {"folder_context": ws.folder},
        check=lambda r: r.get("found") is True and bool(r.get("card"))),
    ("workspace_legal", "facts.form"): Fixture(
        valid=lambda ws, c: {"needs": [{"key": "vat", "prompt": "VAT?", "scope": "standing"},
                                       {"key": "po", "prompt": "PO?", "scope": "per_case"}],
                             "standing": {"vat": "reverse-charge"}},
        invalid=lambda ws, c: {}, check=lambda r: r.get("prefilled", {}).get("vat") == "reverse-charge"),
    ("workspace_legal", "facts.record"): Fixture(
        valid=lambda ws, c: {"needs": [{"key": "vat", "prompt": "VAT?", "scope": "standing"}],
                             "answers": {"vat": "reverse-charge"}},
        invalid=lambda ws, c: {"needs": [{"key": "vat", "prompt": "VAT?"}]},
        check=lambda r: r.get("standing", {}).get("vat") == "reverse-charge"),
    ("workspace_legal", "select.context"): Fixture(
        valid=lambda ws, c: {"entity": "acme", "legal_system": "DE", "clause_needs": ["Zahlung Tagen"],
            "corpus": [{"id": "acme:msa:pay", "text": "Zahlung innerhalb 30 Tagen."},
                       {"id": "globex:msa:pay", "text": "Zahlung binnen 14 Tagen."}]},
        invalid=lambda ws, c: {},
        check=lambda r: {cl["doc_id"] for cl in r.get("clauses", [])} == {"acme:msa:pay"}),
    ("workspace_legal", "select.context_step"): Fixture(setup=lambda ws: with_parties(ws) and {},
        valid=lambda ws, c: {"folder_context": ws.folder, "log_root": ws.log_root, "run_id": "r1", "step_index": 0,
            "step_params": {"entity": "acme", "legal_system": "DE",
                "corpus": [{"id": "acme:msa:pay", "text": "Zahlung innerhalb 30 Tagen."}],
                "clause_needs": ["Zahlung Tagen"]}},
        invalid=lambda ws, c: {"folder_context": ws.folder}, check=lambda r: bool(r.get("audit_id")), mutating=True),
    ("workspace_legal", "subsumption.validate"): Fixture(
        valid=lambda ws, c: {"atoms": _GOOD_ATOMS, "legal_system": "EU"},
        invalid=lambda ws, c: {}, check=lambda r: "validation" in r and "subsumption" in r),
    ("workspace_legal", "pipeline.run_class_c"): Fixture(
        valid=lambda ws, c: {"declared_docs": ["d1", "d2"], "processed_docs": ["d1", "d2"],
            "query": "Pflichten des Anbieters", "corpus": _PIPE_CORPUS, "atoms": _GOOD_ATOMS,
            "pairs": [_CONF_PAIR], "legal_system": "EU"},
        invalid=None, check=lambda r: r.get("verdict") in ("CERTIFIED", "ESCALATED", "REFUSED")),

    # ---- workspace_ingest ----
    ("workspace_ingest", "path"): Fixture(setup=_ingestable_folder,
        valid=lambda ws, c: {"folder_context": ws.folder, "file_path": c["file_path"]},
        invalid=lambda ws, c: {"folder_context": ws.folder, "file_path": str(Path(ws.folder) / "nope.md")},
        check=lambda r: r.get("ingested") is True, mutating=True),
    ("workspace_ingest", "list_urls"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: "urls" in r and "count" in r),
    ("workspace_ingest", "skill"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "source_format": "prose", "author": "tester",
            "source": "# Sort Numbers\n\nGiven a list of numbers, return them sorted ascending. "
                      "Use this when the user asks to sort."},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True, mutating=True),
    ("workspace_ingest", "import_plugin"): Fixture(setup=_plugin_root,
        valid=lambda ws, c: {"folder_context": ws.folder, "source": c["plugin_root"]},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True, mutating=True),
    ("workspace_ingest", "stem"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "origin": "played", "log_root": ws.log_root,
            "file_path": _wav(ws, "snare.wav", "RIFF...snare...")},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True and bool(r.get("stem_hash")), mutating=True),
    ("workspace_ingest", "assemble_work"): Fixture(setup=_stem_ingested,
        valid=lambda ws, c: {"folder_context": ws.folder, "work_id": "w1",
                             "stem_hashes": [c["stem_hash"]], "log_root": ws.log_root},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True and r.get("work_id") == "w1", mutating=True),

    # ---- workspace_contract execution stack ----
    ("workspace_contract", "ingest"): Fixture(setup=lambda ws: with_parties(ws) and {},
        valid=lambda ws, c: {"folder_context": ws.folder, "text": DPA_TEXT, "contract_id": "dpa-x", "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder},
        check=lambda r: r.get("ok") is True and r.get("contract", {}).get("ref") == "dpa-x@1", mutating=True),
    ("workspace_contract", "state"): Fixture(setup=_dpa_contract,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and len(r.get("contracts", [])) == 1),
    ("workspace_contract", "obligations"): Fixture(setup=_dpa_contract,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and "buckets" in r),
    ("workspace_contract", "tick"): Fixture(setup=_dpa_contract,
        valid=lambda ws, c: {"folder_context": ws.folder, "as_of": "2026-12-01"},
        invalid=lambda ws, c: {"folder_context": ws.folder, "as_of": "soon"},
        check=lambda r: r.get("ok") is True and "transitions" in r, mutating=True),
    ("workspace_contract", "apply"): Fixture(setup=_dpa_contract,
        valid=lambda ws, c: {"folder_context": ws.folder, "actions": [
            {"kind": "record_correction", "contract_ref": "dpa-x@1", "field": "language",
             "corrected": "de", "actor": "alex", "rationale": "it is German"}]},
        invalid=lambda ws, c: {"folder_context": ws.folder, "actions": [
            {"kind": "record_correction", "contract_ref": "dpa-x@1", "field": "x",
             "corrected": "y", "actor": "", "rationale": "z"}]},
        check=lambda r: r.get("ok") is True and len(r.get("applied", [])) == 1, mutating=True),
    ("workspace_contract", "resolve"): Fixture(setup=_dpa_obligation,
        valid=lambda ws, c: {"folder_context": ws.folder, "obligation_id": c["obligation_id"],
                             "choice": "satisfied", "actor": "alex", "rationale": "confirmed"},
        invalid=lambda ws, c: {"folder_context": ws.folder, "obligation_id": c["obligation_id"],
                               "choice": "satisfied", "actor": "system", "rationale": "done"},
        check=lambda r: r.get("ok") is True and r.get("obligation", {}).get("state") == "satisfied", mutating=True),

    # ---- workspace_session ----
    ("workspace_session", "build"): Fixture(setup=_p,
        valid=lambda ws, c: {"workspaces": [{"folder_context": ws.folder, "id": "ws1", "name": "ws1"}], "rail": {}, "name": "sess"},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True and isinstance(r.get("bundle"), dict)),
    ("workspace_session", "save"): Fixture(setup=_p,
        valid=lambda ws, c: {"workspaces": [{"folder_context": ws.folder, "id": "ws1", "name": "ws1"}], "rail": {},
                             "path": str(Path(ws.root) / "s.rvnd"), "name": "sess"},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "path" in r),
    ("workspace_session", "verify"): Fixture(setup=_session_saved,
        valid=lambda ws, c: {"path": c["path"]}, invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True and "report" in r),
    ("workspace_session", "forensic"): Fixture(setup=_session_saved,
        valid=lambda ws, c: {"path": c["path"]}, invalid=lambda ws, c: {}, check=lambda r: isinstance(r, dict) and not r.get("error")),
    ("workspace_session", "restore"): Fixture(setup=_session_saved,
        valid=lambda ws, c: {"path": c["path"], "dest_root": str(Path(ws.root) / "dest")},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "folders" in r),
    ("workspace_session", "export"): Fixture(setup=_session_saved,
        valid=lambda ws, c: {"path": c["path"], "workspace_id": "ws1", "out_path": str(Path(ws.root) / "track.rvnd")},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "path" in r),
    ("workspace_session", "import"): Fixture(setup=_session_import,
        valid=lambda ws, c: {"env_path": c["env_path"], "track_path": c["track_path"], "workspace_id": "ws2", "out_path": c["out_path"]},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "path" in r),
    ("workspace_session", "restore_bytes"): Fixture(setup=_bundle,
        valid=lambda ws, c: {"bundle": c["bundle"], "dest_root": str(Path(ws.root) / "dest")},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "folders" in r),
    ("workspace_session", "adopt"): Fixture(setup=_bundle,
        valid=lambda ws, c: {"bundle": c["bundle"], "dest_root": str(Path(ws.root) / "dest")},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "adopted" in r),
    ("workspace_session", "template_list"): Fixture(
        valid=lambda ws, c: {}, invalid=None, check=lambda r: r.get("ok") is True and isinstance(r.get("templates"), list)),
    ("workspace_session", "template_new"): Fixture(setup=_session_template,
        valid=lambda ws, c: {"template_id": c["template_id"], "dest_root": str(Path(ws.root) / "tpl"), "mode": "none"},
        invalid=lambda ws, c: {"template_id": "__nope__", "dest_root": str(Path(ws.root) / "tpl2")},
        mutating=True, check=lambda r: r.get("ok") is True),

    # ---- workspace_mirror ----
    ("workspace_mirror", "generate"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "source_path": _mk_src(ws, "src.md", "Email jane.doe\x40example.com now.\n"), "actor": ACTOR},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "mirror_path" in r),
    ("workspace_mirror", "list"): Fixture(setup=_mirror_gen,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and isinstance(r.get("mirrors"), list) and r.get("count", 0) >= 1),
    ("workspace_mirror", "approve"): Fixture(setup=_mirror_gen,
        valid=lambda ws, c: {"folder_context": ws.folder, "mirror_path": c["mirror_path"], "approver": "alex"},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "mirror_path" in r),
    ("workspace_mirror", "edit"): Fixture(setup=_mirror_open,
        valid=lambda ws, c: {"folder_context": ws.folder, "mirror_path": c["mirror_path"], "span_id": c["span_id"],
                             "operation": "change_replacement", "kwargs": {"new_replacement": "[HIDDEN]"}},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "revision" in r),
    ("workspace_mirror", "history"): Fixture(setup=_mirror_open,
        valid=lambda ws, c: {"folder_context": ws.folder, "mirror_path": c["mirror_path"]},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True and isinstance(r.get("revisions"), list)),
    ("workspace_mirror", "diff"): Fixture(setup=_mirror_open,
        valid=lambda ws, c: {"folder_context": ws.folder, "mirror_path": c["mirror_path"], "from_rev": 0},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True and "diff" in r),
    ("workspace_mirror", "discard"): Fixture(setup=_mirror_open,
        valid=lambda ws, c: {"folder_context": ws.folder, "mirror_path": c["mirror_path"]},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "audit_id" in r),
    ("workspace_mirror", "lock_acquire"): Fixture(setup=_mirror_gen,
        valid=lambda ws, c: {"folder_context": ws.folder, "mirror_path": c["mirror_path"], "actor": ACTOR},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True and "lock" in r),
    ("workspace_mirror", "lock_release"): Fixture(setup=_mirror_locked,
        valid=lambda ws, c: {"folder_context": ws.folder, "mirror_path": c["mirror_path"], "actor": ACTOR},
        invalid=lambda ws, c: {}, mutating=True, check=lambda r: r.get("ok") is True),

    # ---- workspace_matrix ----
    ("workspace_matrix", "show"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: isinstance(r, dict) and "matrix" in r and "grades" in r),
    ("workspace_matrix", "explain"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "grade": "L2", "oversight": "review"},
        invalid=lambda ws, c: {}),
    ("workspace_matrix", "set"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "grade": "L2", "oversight": "review", "light": "ask", "actor": ACTOR},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True and r.get("inherits") is False, mutating=True),
    ("workspace_matrix", "set_row"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "oversight": "review", "light": "block", "actor": ACTOR},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True, mutating=True),
    ("workspace_matrix", "set_col"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "grade": "L3", "light": "block", "actor": ACTOR},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ok") is True, mutating=True),
    ("workspace_matrix", "reset"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "actor": ACTOR}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and r.get("inherits") is True, mutating=True),

    # ---- workspace_lens ----
    ("workspace_lens", "classify"): Fixture(setup=_p,
        valid=lambda ws, c: {"cls": "public_doc", "content_hash": "sha256:abc", "scope": {"allow": ["public_doc"]},
                             "source_actor": ACTOR, "known_teachers": [ACTOR], "confidence": 0.95},
        invalid=lambda ws, c: {"cls": "x", "content_hash": "y", "magnitude": "not-a-number"},
        check=lambda r: isinstance(r, dict) and r.get("admission") in ("admit", "hold", "reject") and "content_hash" in r),
    ("workspace_lens", "select_precedent"): Fixture(setup=_p,
        valid=lambda ws, c: {"features": {"topic": "erasure"},
                             "candidates": [{"id": "prec-1", "query_features": {"topic": "erasure"}, "chosen_option": "split",
                                             "rationale": "human origination", "actor": ACTOR, "learnable": True,
                                             "similarity_threshold": 0.5, "similarity": 0.9}]},
        invalid=lambda ws, c: {"candidates": [{"id": "x", "similarity": "not-a-number"}]},
        check=lambda r: isinstance(r, dict) and r.get("selected") is not None),
    ("workspace_lens", "budget"): Fixture(setup=_p,
        valid=lambda ws, c: {"cap": 5.0, "admitted": [{"magnitude": 1.0}, {"magnitude": 2.0}]},
        invalid=lambda ws, c: {}, check=lambda r: "spent" in r and "remaining" in r and r.get("cap") == 5.0),
    ("workspace_lens", "budget_cap_get"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: isinstance(r, dict) and "cap" in r and "folder" in r),
    ("workspace_lens", "log"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: isinstance(r, dict) and "events" in r and "count" in r),

    # ---- workspace_capture ----
    ("workspace_capture", "web"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "query": "eu ai act", "engine": "test-engine",
                             "results": [{"url": "https://example.org", "title": "T", "snippet": "s", "rank": 1}], "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder}, mutating=True),
    ("workspace_capture", "read"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder},
        invalid=lambda ws, c: {}, check=lambda r: isinstance(r.get("captures"), list)),

    # ---- workspace_model ----
    ("workspace_model", "status"): Fixture(
        valid=lambda ws, c: {"probe_endpoint": False}, invalid=None,
        check=lambda r: r.get("ok") is True and "readiness" in r and "tier_c" in r),

    # ---- workspace_grounder ----
    ("workspace_grounder", "oversight.feed"): Fixture(setup=_p, valid=_gf, invalid=_gf,
        invalid_unmapped=True, invalid_check=empty_result),
    ("workspace_grounder", "source.ingest"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder,
                             "content": "<html><head><title>Sample Page</title>"
                                        "<meta name='author' content='Jane Roe'></head>"
                                        "<body>See doi:10.1000/xyz for details.</body></html>",
                             "url": "https://example.org/page", "title": "Sample Page", "use_model": False},
        invalid=lambda ws, c: {"folder_context": ws.folder},
        check=lambda r: r.get("ok") is True and (r.get("work") or {}).get("id"), mutating=True),
    ("workspace_grounder", "subject.forget"): Fixture(setup=_work_with_creator,
        valid=lambda ws, c: {"folder_context": ws.folder, "name": c["subject"]},
        invalid=lambda ws, c: {"folder_context": ws.folder, "name": "   "},
        check=lambda r: r.get("ok") is True and r.get("works_touched"), mutating=True),

    # ---- workspace_erase ----
    ("workspace_erase", "request"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "subject": "Jane Roe", "requester_ref": "DSAR-1", "reason": "consent withdrawn"},
        invalid=lambda ws, c: {"folder_context": ws.folder}, check=lambda r: bool(r.get("request_id")), mutating=True),
    ("workspace_erase", "status"): Fixture(setup=_erase_request,
        valid=lambda ws, c: {"folder_context": ws.folder, "request_id": c["request_id"]},
        invalid=lambda ws, c: {"folder_context": ws.folder},
        check=lambda r: r.get("ok") is True and (r.get("manifest") or {}).get("requested") is not None),
    ("workspace_erase", "sweep"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "subject": "Jane Roe"},
        invalid=lambda ws, c: {"folder_context": ws.folder}, check=lambda r: r.get("ok") is True and "sweep" in r),
    ("workspace_erase", "subject"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "subject": "Jane Roe", "legal_basis": "art_17_1_b",
                             "requester_ref": "DSAR-1", "reason": "consent withdrawn"},
        invalid=lambda ws, c: {"folder_context": ws.folder, "subject": "Jane Roe"},
        check=lambda r: r.get("ok") is True and "report" in r, mutating=True),

    # ---- workspace_folder ----
    ("workspace_folder", "create"): Fixture(setup=_fresh_child,
        valid=lambda ws, c: {"path": c["child"]}, invalid=lambda ws, c: {},
        check=lambda r: bool(r.get("path")) and not r.get("error"), mutating=True),
    ("workspace_folder", "scan"): Fixture(setup=_registered,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and "count" in r),
    ("workspace_folder", "reextract"): Fixture(setup=_registered,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and "count" in r),
    ("workspace_folder", "ingest"): Fixture(setup=_tmp_file,
        valid=lambda ws, c: {"path": c["file_path"], "folder_context": ws.folder},
        invalid=lambda ws, c: {}, check=lambda r: r.get("ingested") is True, mutating=True),

    # ---- workspace_lock ----
    ("workspace_lock", "reclassify"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and "pairs_total" in r, mutating=True),
    ("workspace_lock", "seal"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and "wall" in r),
    ("workspace_lock", "unseal"): Fixture(setup=_sealed,
        valid=lambda ws, c: {"folder_context": ws.folder, "passphrase": c["passphrase"]},
        invalid=lambda ws, c: {"folder_context": ws.folder, "passphrase": "wrong-pass"},
        check=lambda r: r.get("ok") is True and r.get("unlocked") is True),
    ("workspace_lock", "setup"): Fixture(setup=_lock_setup_paths,
        valid=lambda ws, c: {"config_path": c["fresh_cfg"], "skip_smoke_test": True},
        invalid=lambda ws, c: {"config_path": c["prior_cfg"], "backend_spec": "mock", "skip_smoke_test": True},
        check=lambda r: r.get("ok") is True and "backend_spec" in r, mutating=True),

    # ---- workspace_policy ----
    ("workspace_policy", "enable"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "dial": "lock", "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder}, check=lambda r: r.get("ok") is True, mutating=True),
    ("workspace_policy", "disable"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "dial": "lock", "accepted_by": ACTOR,
                             "reason": "documented downgrade", "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder, "dial": "lock"}, check=lambda r: r.get("ok") is True, mutating=True),
    ("workspace_policy", "set_access_control"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder, "enabled": True, "actor": ACTOR},
        invalid=lambda ws, c: {"folder_context": ws.folder},
        check=lambda r: r.get("ok") is True and "access_control_enabled" in r, mutating=True),
    ("workspace_policy", "actor_stamps"): Fixture(setup=_p,
        valid=lambda ws, c: {"folder_context": ws.folder}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and "total" in r),

    # ---- workspace_workspace ----
    ("workspace_workspace", "add"): Fixture(setup=_fresh_child,
        valid=lambda ws, c: {"folder_context": c["child"]}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and "total" in r, mutating=True),
    ("workspace_workspace", "remove"): Fixture(setup=_added_ws,
        valid=lambda ws, c: {"folder_context": c["extra"]}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and r.get("removed") is True, mutating=True),
    ("workspace_workspace", "bootstrap"): Fixture(setup=_bootstrap_targets,
        valid=lambda ws, c: {"target": c["target"]}, invalid=lambda ws, c: {"target": c["bad_target"]},
        check=lambda r: r.get("ok") is True and r.get("created") is True, mutating=True),
    ("workspace_workspace", "route"): Fixture(setup=_p,
        valid=lambda ws, c: {"query": "contract delivery deadline"}, invalid=lambda ws, c: {},
        check=lambda r: r.get("ok") is True and "candidates" in r),
}
