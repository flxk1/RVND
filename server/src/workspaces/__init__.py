# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Compatibility alias — ``workspaces`` is now :mod:`rvnd`; internal by design.

The import package was named after the thing it operates on. A *workspace* is a
user folder external to this repo, and the engine runs perfectly well with none.
Naming the engine after its subject invited reading process-global facts (the
enforcement posture is read from ``os.environ``) as per-workspace ones.

ONLY the Python import package moved. ``workspaces`` remains a live identifier
elsewhere and is deliberately untouched: the ``workspace_*`` MCP tool names, the
``workspaces`` parameter of the session operations, the ``workspaces`` key in a
saved session bundle, the ``workspaces`` console command, and
``WORKSPACES_ALLOW_UNREGISTERED``. Those are contracts with hosts, with operators
and with sessions already on disk.

Replacing this module's entry in :data:`sys.modules` makes submodules resolve
through ``rvnd``'s ``__path__``, so ``from workspaces.foo import bar`` keeps
working for consumers outside this repo.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import warnings

import rvnd

warnings.warn(
    "`workspaces` is deprecated; import `rvnd` instead. The engine no longer "
    "carries the name of the folders it governs.",
    DeprecationWarning,
    stacklevel=2,
)

# Aliasing the top-level name is NOT enough. `sys.modules["workspaces"] = rvnd`
# makes `import workspaces` return rvnd, but `import workspaces.mcp_serving`
# still runs the import machinery for that dotted name and builds a SECOND
# module object from rvnd's __path__ — same source, separate globals.
#
# That is not cosmetic. Module state stops being shared: a caller on the legacy
# path calling `workspaces.mcp_serving.set_request_principal(...)` writes to one
# ContextVar while the enforcement path reads `rvnd.principal`'s, so
# `get_request_principal()` returns None and the per-principal registry scope
# hands back the FULL registry. The alias would fail OPEN, silently, for exactly
# the consumers it exists to keep working.
#
# So submodules are aliased too: every `workspaces.X` resolves to the very same
# object as `rvnd.X`, and there is only one copy of any module's state.
class _SubmoduleAlias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    _PREFIX = "workspaces."

    def find_spec(self, name, path=None, target=None):
        if not name.startswith(self._PREFIX):
            return None
        return importlib.util.spec_from_loader(name, self)

    def create_module(self, spec):
        real = importlib.import_module("rvnd." + spec.name[len(self._PREFIX):])
        sys.modules[spec.name] = real      # so later lookups get the same object
        return real

    def exec_module(self, module):
        pass                                # already executed as rvnd.<name>


# Returning `rvnd.X` under the name `workspaces.X` is exactly what makes the two
# names one object, and it is also what CPython's import machinery notices: it
# compares the module's __package__ ("rvnd") with the requested __spec__.parent
# ("workspaces") and warns. That is the aliasing working, not a defect, and it
# fired ~155k times across the suite. Silenced narrowly — this one message, from
# the import machinery only — so a real DeprecationWarning still surfaces.
warnings.filterwarnings(
    "ignore",
    message=r"__package__ != __spec__\.parent",
    category=DeprecationWarning,
)

sys.meta_path.insert(0, _SubmoduleAlias())
sys.modules[__name__] = rvnd
