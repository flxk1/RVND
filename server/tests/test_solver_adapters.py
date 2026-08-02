"""Permanent RVND adapters conform to Solver's host protocols."""

from loomground_solver.ports import Governance, NormSource

from workspaces.adapters.solver import (
    RvndGovernance,
    RvndNormSource,
    check_with_rvnd_governance,
)


class _Registry:
    def workspace_items(self):
        return [
            {"kind": "norm", "pinpoint": "Art. 1", "anchors": [{"entity": "x"}]},
            {"kind": "note", "pinpoint": "ignored", "anchors": []},
        ]


class _Policy:
    oversight_default_level = "review"
    oversight_is_active = True


class _Log:
    def __init__(self):
        self.events = []

    def append_raw(self, **event):
        self.events.append(event)


def test_norm_source_conforms_and_filters():
    source = RvndNormSource(_Registry())
    assert isinstance(source, NormSource)
    assert source.norm_spans_for({"x"})[0]["pinpoint"] == "Art. 1"
    assert source.held_pinpoints() == {"Art. 1"}


def test_governance_conforms_and_records():
    log = _Log()
    governance = RvndGovernance(
        "/folder", policy_loader=lambda _: _Policy(),
        lock_fn=lambda _: {"findings": ["one"]}, log_sink=log,
    )
    assert isinstance(governance, Governance)
    assert governance.oversight_level() == "review"
    assert governance.oversight_active()
    assert governance.classify("text")["findings"] == 1
    governance.record({"op": "solver", "verdict": "escalate"})
    assert log.events == [{"op": "solver", "verdict": "escalate"}]


def test_folder_contract_uses_live_governance_adapter(tmp_path, monkeypatch):
    from workspaces import reasoning_contract
    from workspaces.adapters.solver import governance as adapter

    seen = {}

    def _check(case, *, governance, **kwargs):
        seen["governance"] = governance
        seen["kwargs"] = kwargs
        return "checked"

    monkeypatch.setattr(adapter, "_solver_check", _check)
    result = reasoning_contract.check_folder_case(
        {"resolution": {"type": "open"}},
        tmp_path,
        stake=True,
    )
    assert result == "checked"
    assert isinstance(seen["governance"], RvndGovernance)
    assert seen["kwargs"]["stake"] is True
