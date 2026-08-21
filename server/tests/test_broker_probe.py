# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The egress board's enforcement attestation — probe_broker asks the running
proxy whether it holds THIS folder's plug, fail-closed on every miss
(unreachable, unbound, bound elsewhere), and egress_board carries the result
as the board-level ``llm_broker`` word the client must render, never assume."""
from __future__ import annotations

import socket

from rvnd import connectors
from rvnd.lock import OversightLevel
from rvnd.lock.broker_probe import probe_broker
from rvnd.lock.egress_proxy import EgressProxy, autonomous_callback


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _proxy(**kw) -> EgressProxy:
    p = EgressProxy(port=_free_port(), oversight=OversightLevel.AUTONOMOUS,
                    approval_callback=autonomous_callback, **kw)
    p.start()
    return p


def test_probe_unreachable_is_not_bound():
    out = probe_broker("/some/folder", proxy_url=f"http://127.0.0.1:{_free_port()}",
                       timeout=0.3)
    assert out == {"reachable": False, "bound_here": False}


def test_probe_unbound_proxy_is_reachable_not_bound(tmp_path):
    p = _proxy()
    try:
        out = probe_broker(str(tmp_path), proxy_url=f"http://127.0.0.1:{p.port}")
        assert out == {"reachable": True, "bound_here": False}
    finally:
        p.stop()


def test_probe_bound_to_this_folder(tmp_path):
    folder = tmp_path / "ws"
    folder.mkdir()
    p = _proxy(track_folder=str(folder))
    try:
        out = probe_broker(str(folder), proxy_url=f"http://127.0.0.1:{p.port}")
        assert out == {"reachable": True, "bound_here": True}
        # the same environment through a relative-ish spelling still matches
        out2 = probe_broker(str(tmp_path / "." / "ws"),
                            proxy_url=f"http://127.0.0.1:{p.port}")
        assert out2["bound_here"] is True
    finally:
        p.stop()


def test_probe_bound_elsewhere_is_not_bound_here(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    p = _proxy(track_folder=str(tmp_path / "a"))
    try:
        out = probe_broker(str(tmp_path / "b"), proxy_url=f"http://127.0.0.1:{p.port}")
        assert out == {"reachable": True, "bound_here": False}
    finally:
        p.stop()


# ---- egress_board carries the attestation --------------------------------------

def test_board_defaults_to_no_attestation(tmp_path):
    f = str(tmp_path); lr = str(tmp_path / "l")
    connectors.register_connector(f, connector_id="out", role="egress",
                                  channel="api", log_root=lr)
    board = connectors.egress_board(f, log_root=lr)
    assert board["llm_broker"] == {"reachable": False, "bound_here": False}
    assert board["tracks"][0]["mode"] == "attested"


def test_board_passes_probe_result_through(tmp_path):
    f = str(tmp_path); lr = str(tmp_path / "l")
    connectors.register_connector(f, connector_id="out", role="egress",
                                  channel="api", log_root=lr)
    board = connectors.egress_board(
        f, log_root=lr, llm_broker={"reachable": True, "bound_here": True})
    assert board["llm_broker"] == {"reachable": True, "bound_here": True}
    # per-track mode stays attested: the general (tool) cable is host-invoked;
    # the enforced word is board-level for the LLM destination class
    assert board["tracks"][0]["mode"] == "attested"
