# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
"""Capability resolver — the registration-time dependency closure.

Proves the resolver infers the right capability needs from content (reusing the
content router's signals) and resolves them against a host: a legal vertical
needs the rule ND + a legal pack + currency (substrate-satisfied); a math/code
vertical additionally needs a python sandbox (missing unless the host provides
one); declared NDs and connector requirements are honoured.
"""

from __future__ import annotations

from workspaces.capability.resolver import resolve, missing, Host


LEGAL = ("Providers of high-risk AI systems shall establish a risk management "
         "system. By way of derogation, the obligation does not apply where the "
         "system is used solely for scientific research.")
MATH = ("Theorem 1. For all integers n, the sum 1 + 2 + ... + n equals "
        "n*(n+1)/2. Proof: by induction on n. Base case n=1 gives 1 = 1.")
CODE = ("def solve(x):\n    return sorted(x)[::-1]\n\nfor i in range(10):\n"
        "    print(solve([i, i+1]))\n")


def _ids(manifest):
    return {r.cap_id: r.status for r in manifest}


def test_legal_vertical_needs_rule_nd_pack_currency_all_satisfied():
    m = resolve(sample_text=LEGAL)
    ids = _ids(m)
    assert ids.get("nd:rule") == "satisfied"
    assert ids.get("pack:legal-system") == "satisfied"
    assert ids.get("substrate:currency") == "satisfied"
    assert not missing(m)               # a legal vertical resolves clean on the substrate


def test_math_vertical_needs_python_sandbox_missing_by_default():
    m = resolve(sample_text=MATH)
    ids = _ids(m)
    assert ids.get("nd:math") == "satisfied"           # math ND is substrate-registered
    assert ids.get("runtime:python-sandbox") == "missing"   # sandbox not assumed
    assert any(r.cap_id == "runtime:python-sandbox" for r in missing(m))


def test_python_sandbox_satisfied_when_host_provides_it():
    m = resolve(sample_text=MATH, host=Host(has_python_sandbox=True))
    assert _ids(m).get("runtime:python-sandbox") == "satisfied"
    assert not missing(m)


def test_code_content_requires_a_sandbox():
    m = resolve(sample_text=CODE)
    assert _ids(m).get("runtime:python-sandbox") == "missing"


def test_declared_math_nd_is_inferred_even_without_sample():
    m = resolve(instrument_nds=[{"nd": "nd-math-theorems"}])
    assert _ids(m).get("nd:math") == "satisfied"


def test_unknown_connector_requirement_is_missing():
    m = resolve(sample_text=LEGAL, ir_requires=["connector:slack"],
                host=Host(available_connectors=set()))
    ids = _ids(m)
    assert ids.get("connector:slack") == "missing"
    assert any(r.cap_id == "connector:slack" for r in missing(m))


def test_reasons_are_carried_for_auditability():
    m = resolve(sample_text=LEGAL)
    rule = next(r for r in m if r.cap_id == "nd:rule")
    assert rule.reasons and "normative" in rule.reasons[0]


def test_existing_but_unloaded_nd_is_provisionable_not_missing():
    from workspaces.capability.resolver import provisionable, provision_into
    from workspaces.nd_routing import NDRouter
    # math ND exists on disk (domain_nds.MathND) but this host hasn't loaded it
    host = Host(registered_nds={"nd:rule"}, has_python_sandbox=True)
    m = resolve(sample_text=MATH, host=host)
    assert _ids(m).get("nd:math") == "provisionable"
    assert not missing(m)                       # provisionable never blocks
    assert any(r.cap_id == "nd:math" for r in provisionable(m))
    # and it self-heals: provision_into actually registers the ND
    router = NDRouter(); before = len(router.registered())
    done = provision_into(m, router)
    assert "nd:math" in done and len(router.registered()) > before


def test_truly_external_capability_stays_missing_not_provisionable():
    # no sandbox on this host; a sandbox cannot be auto-provisioned
    m = resolve(sample_text=MATH, host=Host(registered_nds={"nd:rule", "nd:math"}))
    assert _ids(m).get("runtime:python-sandbox") == "missing"
    assert any(r.cap_id == "runtime:python-sandbox" for r in missing(m))
