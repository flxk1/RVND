"""The only RVND package boundary permitted to import :mod:`versum`.

Versum's pinned adoption-kit loader (``versum.loomground._kit``) imports the
neutral language kit under its pre-rename distribution name
``loomground_language``. That distribution is now published as
``loomground_governance`` with an identical public surface
(``grammar``/``language_card``/``language_version``). Registering the alias
here — eagerly, at the sole boundary permitted to import Versum, before any
Versum call resolves the kit — lets the installed governance package satisfy
Versum's loader without vendoring a source tree or setting ``LOOMGROUND_SOURCE``.
``setdefault`` yields to a real ``loomground_language`` install if one appears.
"""

import sys as _sys

try:
    import loomground_governance as _governance_kit
except ImportError:  # governance kit absent → let Versum raise its own error
    pass
else:
    _sys.modules.setdefault("loomground_language", _governance_kit)

from versum import DimensionedSubgraphSink, load_dimensioned_subgraphs
from versum.store.retrieve import BM25  # the consumed lexical-ranking mechanism

from .knowledge import (VersumKnowledgeStore, VersumSnapshot,
                        versum_language_runtime)
from .solver_source import VersumSolverSource
from .runtime import (append_fact, append_inference, append_record,
                      append_records, erase_record, iter_records)

__all__ = [
    "DimensionedSubgraphSink", "load_dimensioned_subgraphs",
    "VersumKnowledgeStore", "VersumSnapshot", "VersumSolverSource",
    "versum_language_runtime",
    "append_fact", "append_inference", "append_record", "append_records",
    "iter_records", "erase_record", "BM25",
]
