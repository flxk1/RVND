# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Shared pytest fixtures for the workspaces test suite.

Hermeticity: the lock Tier C backend defaults to whatever
``AGENT_TOOL_LOCK_LLM_BACKEND`` points at. A developer who has exported that
to a real local model (e.g. ``llama_cpp:/path/to/phi-3.5.gguf`` while testing
manually) would otherwise leak a multi-second model into the test session —
breaking timeout-sensitive integration tests (e.g. the egress-proxy tests,
which use a 2-second client timeout and assume the fast deterministic mock).

This autouse fixture pins the backend to ``mock`` for every test and resets
the cached backend instance so the change takes effect. Tests that explicitly
want a different backend can monkeypatch it in their own body (which runs after
this fixture) or instantiate a backend class directly.

HOME is redirected to a per-session scratch dir before the first workspaces
import: chain roots, key dirs and model registries default to Path.home()-
derived constants baked at import time, so tests that never pass an explicit
log_root would otherwise read and write the real ``~/.workspace``. That both
litters the operator's home with test chains and poisons count-asserting
tests — ``folder_hash`` keys a chain by its path string, and pytest's
numbered tmp dirs (``pytest-N``) repeat once the OS purges TMPDIR, so a run
can replay a stale chain recorded by an earlier session under the same path.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

# realpath: macOS TMPDIR lives behind a /var symlink, and the folder-hash
# tests hash str(Path.home()) expecting an already-resolved path.
_TEST_HOME = os.path.realpath(tempfile.mkdtemp(prefix="workspaces-test-home-"))
os.environ["HOME"] = _TEST_HOME
os.environ["USERPROFILE"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

import pytest

# A6: the suite operates on ad-hoc scratch folders not registered in the
# known-workspaces allowlist. Production defaults to ENFORCE (an explicit
# folder_context must resolve into a registered workspace or a descendant);
# the test harness opts out so existing tests keep working. The A6 mitigation
# test deletes this env var in its own body to prove enforcement refuses
# unregistered paths.
#
# HARDENED PROFILE (`make test-hardened` / RVND_TEST_HARDENED=1): the opt-out
# above means the default run exercises a deliberately weakened configuration
# — every fail-closed property is proven individually, but no run exercises
# the profile a security-conscious operator actually deploys (allowlist
# enforced, strict host divergence, genesis key pinning) as a whole. Under
# the hardened profile the permissive default is NOT applied and the opt-in
# protections are switched on; the target runs the suites whose fixtures
# survive enforcement (see Makefile `test-hardened`).
if os.environ.get("RVND_TEST_HARDENED") == "1":
    os.environ.pop("WORKSPACES_ALLOW_UNREGISTERED", None)
    os.environ["WORKSPACE_STRICT_HOST_DIVERGENCE"] = "1"
    os.environ["WORKSPACE_KEY_PINNING"] = "1"
    os.environ["WORKSPACE_STRICT_KEY_PINNING"] = "1"
else:
    os.environ.setdefault("WORKSPACES_ALLOW_UNREGISTERED", "1")


@pytest.fixture(autouse=True)
def _hardened_lawful_workspace(request, tmp_path):
    """Hardened profile only: the allowlist is ENFORCED (no suite-global
    opt-out), so give each test a lawful footing by registering its scratch
    root — descendants pass under the asymmetric folder rule, so fixtures
    creating workspace folders under ``tmp_path`` need no changes.

    ``tests/security/`` is excluded: the attack tests construct their own
    registered/unregistered conditions, and registering their scratch root
    would silently defeat the A6 refusal assertions.
    """
    if os.environ.get("RVND_TEST_HARDENED") == "1":
        test_dir = Path(str(request.node.fspath)).parent
        if test_dir.name != "security":
            from rvnd.workspace_registry import add_known_workspace
            add_known_workspace(tmp_path, label="hardened-test-scratch")
    yield


@pytest.fixture(autouse=True)
def _hermetic_lock_backend(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_LOCK_LLM_BACKEND", "mock")
    try:
        from rvnd.lock.tier_c import reset_backend_cache
        reset_backend_cache()
    except Exception:
        pass
    yield
    try:
        from rvnd.lock.tier_c import reset_backend_cache
        reset_backend_cache()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_session_admission(request, monkeypatch):
    """Old operation unit tests exercise dispositions, not authentication.

    Production has no bypass. Only the test process replaces the verifier;
    tests marked ``live_session_admission`` exercise the signed gate intact.
    """
    if request.node.get_closest_marker("live_session_admission") is None:
        from rvnd import session_admission
        monkeypatch.setattr(
            session_admission,
            "verify_operation_session",
            lambda *args, **kwargs: object(),
        )
    yield


@pytest.fixture(autouse=True)
def _isolate_egress_capability(request, monkeypatch):
    """Existing proxy tests isolate prompt/credential behavior from admission."""
    if request.node.get_closest_marker("live_egress_capability") is None:
        from types import SimpleNamespace
        from rvnd.lock import host_deps

        # Wire the real factories FIRST, then override. EgressProxy.__init__
        # calls host_deps.ensure_wired(), which on its first-ever call imports
        # lock_wiring and OVERWRITES capability_verifier_factory with the real
        # one. If that first call happens inside the proxy constructor (i.e. no
        # earlier test wired), it clobbers the override below and the tokenless
        # unit request is refused with a real 403 — so these tests passed only
        # when some earlier test had already wired. Forcing the wire here makes
        # every egress test order-independent.
        host_deps.ensure_wired()

        class _UnitVerifier:
            def verify(self, token, *, expected_folder=None, **kwargs):
                return SimpleNamespace(folder=expected_folder or "/unit-test")

        monkeypatch.setattr(
            host_deps,
            "capability_verifier_factory",
            lambda: _UnitVerifier(),
        )
    yield
