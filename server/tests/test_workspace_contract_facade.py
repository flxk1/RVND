# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Facade tests for the workspace_contract execution ops (ingest / state / tick /
apply / resolve / demo). Calls the op functions directly — the MCP transport
is covered by the host-protocol suite; what matters here is that every op is
error-wrapped (never raises across the boundary) and that the gates hold
through the facade exactly as they do in the registries."""

import pytest

mcp_server = pytest.importorskip("workspaces.mcp_server")

DPA = """DATA PROCESSING AGREEMENT

This Data Processing Agreement is made between Norddata Services GmbH (the
"Processor") and Beispielkunde AG (the "Controller") under Article 28 GDPR.

This Agreement is effective as of 2026-07-01.

1. The Processor shall notify the Controller of a personal data breach no
later than 72 hours after the personal data breach.

2. The Processor must not engage a Sub-processor without the prior written
authorisation of the Controller.
"""


class TestFacadeOps:
    def test_ingest_then_state(self, tmp_path):
        out = mcp_server.contract_ingest(str(tmp_path), DPA, contract_id="dpa-x")
        assert out["ok"] and out["contract"]["ref"] == "dpa-x@1"
        st = mcp_server.contract_state(str(tmp_path))
        assert st["ok"] and len(st["contracts"]) == 1
        assert st["contracts"][0]["instance"]["contract_type"] == "dpa"

    def test_tick_replay_safe_through_facade(self, tmp_path):
        mcp_server.contract_ingest(str(tmp_path), DPA, contract_id="dpa-x")
        first = mcp_server.contract_tick(str(tmp_path), as_of="2026-12-01")
        second = mcp_server.contract_tick(str(tmp_path), as_of="2026-12-01")
        assert first["ok"] and second["ok"]
        assert second["transitions"] == []

    def test_tick_bad_date_is_error_not_raise(self, tmp_path):
        out = mcp_server.contract_tick(str(tmp_path), as_of="soon")
        assert out["ok"] is False and "TemporalError" in out["error"]

    def test_resolve_enforces_gates_through_facade(self, tmp_path):
        mcp_server.contract_ingest(str(tmp_path), DPA, contract_id="dpa-x")
        st = mcp_server.contract_state(str(tmp_path))
        oid = st["obligations"][0]["obligation_id"]
        anon = mcp_server.contract_resolve(str(tmp_path), oid, "satisfied",
                                           actor="system", rationale="done")
        assert anon["ok"] is False and "name the actor" in anon["error"]
        ok = mcp_server.contract_resolve(str(tmp_path), oid, "satisfied",
                                         actor="alex", rationale="confirmed")
        assert ok["ok"] and ok["obligation"]["state"] == "satisfied"

    def test_apply_reports_failures(self, tmp_path):
        mcp_server.contract_ingest(str(tmp_path), DPA, contract_id="dpa-x")
        out = mcp_server.contract_apply(str(tmp_path), [
            {"kind": "record_correction", "contract_ref": "dpa-x@1",
             "field": "language", "corrected": "de", "actor": "alex",
             "rationale": "it is German"},
            {"kind": "record_correction", "contract_ref": "dpa-x@1",
             "field": "x", "corrected": "y", "actor": "", "rationale": "z"},
        ])
        assert out["ok"] is False
        assert len(out["applied"]) == 1 and len(out["failed"]) == 1

    def test_demo_op(self, tmp_path):
        out = mcp_server.contract_demo(str(tmp_path))
        assert out["ok"] and len(out["ingested"]) == 5

    def test_facade_op_map_includes_execution_ops(self):
        import inspect
        src = inspect.getsource(mcp_server.workspace_contract)
        for op in ("ingest", "state", "tick", "apply", "resolve", "demo"):
            assert f'"{op}"' in src
