# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
# Single entry point for the local gates CI also runs (.github/workflows/ci.yml).

PY ?= python3
PYTHONPATH ?= server/src

.PHONY: help venv gates release-gates public-snapshot lint ruff egress-guard licences surface completeness test test-fast test-hardened app-tests serve dependency-artifacts container-inputs patchbay-consumption

help:
	@echo "venv          create .venv and install the pinned deps exactly as CI does"
	@echo "gates         run every fast gate (lint, ruff, egress-guard, licences, surface, lock-boundary)"
	@echo "surface       surface verifier: no new unsurfaced ops/modules vs baseline"
	@echo "completeness  UI render gates (add ARGS=--server for the full server suite)"
	@echo "test          server + plugin test suites (full; includes slow/model-gated tests that can run for minutes)"
	@echo "test-fast     bounded server test subset for CI/local (excludes slow, model, and one uncollectable file)"
	@echo "app-tests     the four app-harness gates CI runs (routing, personas, reachability, UI-walk)"
	@echo "test-hardened security/audit/erasure suites with every opt-in protection ON (enforced allowlist, strict divergence, key pinning)"
	@echo "serve         run the console app locally"
	@echo "dependency-artifacts  validate PIP_REPORT and emit release dependency evidence"
	@echo "container-inputs  verify all container images use immutable digests"

# Reproducible environment: a fresh venv installing exactly the pinned upstreams,
# identical to .github/workflows/ci.yml. Rebuilds from scratch so a drifted local
# venv can never mask what the pins actually resolve to. `make venv && make test`
# is the clean-checkout path to a real test run.
venv:
	$(PY) -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/pip install -e ".[test]"
	@echo "ready: PYTHONPATH=server/src .venv/bin/python -m pytest server/tests -q"

gates: lint ruff egress-guard licences surface lock-boundary container-inputs patchbay-consumption

# Release checks concern this independently publishable repository.  They are
# deterministic in CI and fresh clones and require no second checkout/remote.
release-gates: gates public-snapshot

public-snapshot:
	$(PY) scripts/verify_public_snapshot.py

lint:
	sh scripts/register_lint.sh

ruff:
	$(PY) -m ruff check .

lock-boundary:
	$(PY) scripts/lock_boundary_check.py

container-inputs:
	$(PY) scripts/verify_container_inputs.py

patchbay-consumption:
	$(PY) scripts/verify_patchbay_consumption.py

egress-guard:
	PYTHONPATH="$(PYTHONPATH)" $(PY) scripts/egress_import_guard.py

licences:
	$(PY) scripts/dep_license_gate.py

dependency-artifacts:
	@test -n "$(PIP_REPORT)" || (echo "PIP_REPORT is required"; exit 2)
	@test -n "$(PLATFORM)" || (echo "PLATFORM is required"; exit 2)
	$(PY) scripts/release_dependency_artifacts.py --pip-report "$(PIP_REPORT)" --platform "$(PLATFORM)" --output-dir "$${OUTPUT_DIR:-release-dependencies}"

surface:
	$(PY) scripts/verify_surface.py

completeness:
	$(PY) scripts/verify_completeness.py $(ARGS)

test:
	PYTHONPATH="$(PYTHONPATH)" $(PY) -m pytest server/tests plugin/tests -q

# RV-19: coverage measurement over the bounded suite (branch coverage; config
# in pyproject.toml [tool.coverage]). Report is the artifact — no hard %-floor
# gate yet, that lands once the baseline is real, not guessed.
coverage:
	PYTHONPATH="$(PYTHONPATH)" $(PY) -m pytest server/tests -q -m "not slow and not model" \
		--cov --cov-report=term-missing --cov-report=xml

# Hardened profile (RVND_TEST_HARDENED=1): the security/audit/erasure/seal/lock
# suites re-run with the configuration a security-conscious operator deploys —
# allowlist ENFORCED (no suite-global WORKSPACES_ALLOW_UNREGISTERED), strict
# host divergence, genesis key pinning + strict pinning. The default run keeps
# the permissive test profile; this lane proves the fail-closed properties
# still hold when every opt-in protection is ON at once.
HARDENED_SUITES = \
  server/tests/security \
  server/tests/test_adversarial_mcp.py \
  server/tests/test_url_ingest.py \
  server/tests/test_authorization.py \
  server/tests/test_egress_proxy.py \
  server/tests/test_egress_capability.py \
  server/tests/test_session_admission.py \
  server/tests/test_capability_token_signatures.py \
  server/tests/test_audit_chain_hash.py \
  server/tests/test_ed25519_signing.py \
  server/tests/test_genesis_key_pinning.py \
  server/tests/test_chain_integrity_slice4.py \
  server/tests/test_seal.py \
  server/tests/test_seal_write_refused.py \
  server/tests/test_seal_read_through.py \
  server/tests/test_seal_record.py \
  server/tests/test_erasure_068.py \
  server/tests/test_purge_tombstone_068.py \
  server/tests/test_erase_guard_fail_closed.py \
  server/tests/test_lock_text.py \
  server/tests/test_lock_hardening_067.py \
  server/tests/test_doctor_068.py

test-hardened:
	RVND_TEST_HARDENED=1 PYTHONPATH="$(PYTHONPATH)" $(PY) -m pytest $(HARDENED_SUITES) -q

# The four app-harness gates CI runs in render-board: console routing, persona
# click-throughs, full-surface op reachability, and the UI-walk reconciliation
# (needs jsdom: cd app && npm ci).
app-tests:
	$(PY) app/tests/console_render_test.py
	$(PY) app/tests/persona_test.py
	$(PY) app/tests/coverage_gate.py
	$(PY) app/tests/ui_walk_reconcile_test.py
	$(PY) app/tests/protections_failure_test.py

# Bounded, green subset: excludes tests marked slow/model (see pyproject.toml)
# plus test_capability_coverage.py, whose top-level code drives the full
# capability matrix in a subprocess at COLLECTION time — a marker cannot skip
# that (deselection happens after collection already ran it), so it needs an
# --ignore instead. Everything else in server/tests collects and runs in
# single-digit seconds; this is the target for CI and pre-commit runs.
test-fast:
	@echo "test-fast: excluding server/tests/test_capability_coverage.py (module-level subprocess build blocks at collection, not just at test-run time)"
	PYTHONPATH="$(PYTHONPATH)" $(PY) -m pytest server/tests -q -m "not slow and not model" --ignore=server/tests/test_capability_coverage.py

serve:
	$(PY) app/serve.py
