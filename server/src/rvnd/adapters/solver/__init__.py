"""RVND host adapters for the universal Solver ports; internal by design.

This package is also the sanctioned seam for RVND's direct dependency on
``loomground_solver``: the submodules alongside this file (``dimensions``,
``predicate``, ``reasoning``, ``temporal``, ``norm_contract``, ``phases``,
``topology``, ``contract``, ``loomground``) each import one upstream module
and re-export its public names. Nothing else under ``workspaces`` imports
``loomground_solver`` directly — everything reaches it through here, or
through a top-level compatibility facade (``rvnd.dimensions``,
``rvnd.temporal``, ...) that itself imports from here.
"""

from .governance import RvndGovernance, check_with_rvnd_governance
from .norm_source import RvndNormSource

__all__ = [
    "RvndGovernance",
    "RvndNormSource",
    "check_with_rvnd_governance",
]
