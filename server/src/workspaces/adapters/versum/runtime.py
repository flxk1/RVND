# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Runtime knowledge-append seam over versum — the only door RVND writes through.

``workspace_remember`` / ``reason`` record runtime knowledge (asserted facts,
derived inferences) into the folder's versum store. Consuming versum's
runtime-append API is confined here, at the ``adapters/versum`` boundary, so no
application module imports ``versum`` directly. The ``versum`` import is lazy —
inside each call — so a versum build without the runtime-append surface raises
at call time (the caller's best-effort guard handles it) rather than at
seam-import time; the knowledge adapter therefore never fails to load on an
older versum pin.
"""
from __future__ import annotations

from typing import Any


def append_fact(store: Any, *, subject: str, predicate: str, object: str,
                dimension: str, actor: str) -> Any:
    """Append an asserted triple as first-class versum knowledge."""
    import versum
    return versum.append_fact(
        str(store), subject=subject, predicate=predicate, object=object,
        dimension=dimension, actor=actor)


def append_inference(store: Any, *, path: list, dimension: str, actor: str) -> Any:
    """Append a derived inference path as first-class versum knowledge."""
    import versum
    return versum.append_inference(
        str(store), path=path, dimension=dimension, actor=actor)
