# SPDX-License-Identifier: AGPL-3.0-only
"""The compat alias must return the SAME module object, not a second copy.

`sys.modules["workspaces"] = rvnd` aliases the top-level name only. Importing
`workspaces.mcp_serving` still drives the import machinery for that dotted name
and builds a second module from rvnd's __path__ — same source, separate globals.

That failed OPEN. A caller on the legacy path setting a request principal wrote
to one ContextVar while the enforcement path read another, so
`get_request_principal()` returned None and the per-principal registry scope
returned the FULL registry to a principal who is a member of nothing. Both
copies "worked"; only their state disagreed, and nothing said so.
"""
from __future__ import annotations

import warnings

import pytest


@pytest.mark.parametrize("name", [
    "mcp_serving", "principal", "workspace_registry", "folder_context",
    "mutation_log", "policy", "lock.core",
])
def test_legacy_path_is_the_same_object(name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy = __import__(f"workspaces.{name}", fromlist=["_"])
        real = __import__(f"rvnd.{name}", fromlist=["_"])
    assert legacy is real, (
        f"workspaces.{name} is a second copy of rvnd.{name}: same code, separate "
        f"module state. Anything stored at module scope — a ContextVar, a cache, "
        f"a registry — silently stops being shared.")


def test_a_principal_set_through_the_legacy_path_reaches_enforcement():
    """The concrete failure, not just object identity.

    This is the property that broke: the scope filter reads through `principal`,
    so a principal set on a *different* copy is invisible and the fail-closed
    filter never engages.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from workspaces.mcp_serving import clear_request_principal, set_request_principal
    from rvnd.principal import get_request_principal

    clear_request_principal()
    set_request_principal("intruder", "intruder", rung="proxy-verified")
    try:
        seen = get_request_principal()
    finally:
        clear_request_principal()
    assert seen is not None and seen.get("principal") == "intruder", (
        "a principal set through the legacy import path was invisible to the "
        "enforcement path — the per-principal scope would fail OPEN")
