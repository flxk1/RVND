# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""The decides connection: a decision applied a rule (cross-layer edge)."""

from workspaces.legal_connection import Connection, VOCABULARY, dimension, is_connection
from workspaces.dimensions import Dimension


def test_decides_is_in_the_connection_vocabulary():
    assert Connection.DECIDES.value == "decides"
    assert "decides" in VOCABULARY
    assert is_connection("decides")


def test_decides_dimension_is_causal():
    # the decision determines the rule's application — a consequence, not a purpose
    assert dimension(Connection.DECIDES) == Dimension.CAUSAL
