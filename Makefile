# Doeff Development Makefile
# ===========================
# Centralized commands for development, testing, and linting.

.PHONY: help install sync lint lint-ruff lint-pyright lint-semgrep lint-semgrep-docs lint-doeff lint-packages \
        test test-unit test-e2e test-changed test-packages print-package-extra-test-roots test-hy-floor test-rust test-all test-spec-audit-sa002 bench-smoke format check check-repo-hygiene \
        pre-commit-install hooks-install enforcement-ledger docs-site docs-serve clean

# Default target
help:
	@echo "Doeff Development Commands"
	@echo "=========================="
	@echo ""
	@echo "Setup:"
	@echo "  make install           Install all dependencies (including dev)"
	@echo "  make sync              Install deps + rebuild Rust VM extension"
	@echo "  make pre-commit-install Install pre-commit hooks"
	@echo "  make hooks-install     Install raw git pre-commit hook (semgrep rules and the doeff-linter baseline compare on staged files + enforcement ledger, ADR-DOE-ENFORCE-001 R7)"
	@echo ""
	@echo "Linting (make lint runs all):"
	@echo "  make lint              Run ALL linters (core + packages)"
	@echo "  make lint-ruff         Run ruff linter"
	@echo "  make lint-pyright      Run pyright type checker"
	@echo "  make lint-semgrep      Run semgrep architectural rules"
	@echo "  make lint-semgrep-docs Check docs for deprecated API patterns"
	@echo "  make lint-doeff        Run doeff-linter (Rust-based)"
	@echo "  make lint-packages     Run lint in all subpackages with Makefiles"
	@echo ""
	@echo "Testing:"
	@echo "  make test              Run core tests"
	@echo "  make test-unit         Run unit tests only (exclude e2e)"
	@echo "  make test-e2e          Run e2e tests only"
	@echo "  make test-changed      Run contract tests + changed tests before ai land request (60 s budget)"
	@echo "  make test-packages     Run tests in all subpackages"
	@echo "  make test-hy-floor     Run doeff-hy tests on the declared lower bound of Hy"
	@echo "  make test-rust         Run cargo test in every Rust crate"
	@echo "  make test-all          Run ALL tests (core + packages + Rust crates)"
	@echo "  make test-spec-audit-sa002 Run SA-002 pytest + semgrep checks"
	@echo "  make bench-smoke       Run benchmark smoke checks without performance gating"
	@echo ""
	@echo "Formatting:"
	@echo "  make format            Format code with ruff"
	@echo "  make check             Run format check without modifying files"
	@echo "  make check-repo-hygiene Check generated artifacts are not tracked"
	@echo ""
	@echo "Docs site:"
	@echo "  make docs-site         Build the docs site into site/ (mkdocs build --strict, same as CI)"
	@echo "  make docs-serve        Serve the docs site locally with live reload"
	@echo ""
	@echo "Utilities:"
	@echo "  make clean             Remove build artifacts and caches"

# =============================================================================
# Setup
# =============================================================================

install:
	uv sync --group dev

# Sync dependencies AND rebuild the Rust VM extension.
# ADR-DOE-ENFORCE-001 R4: the VM conformance oracle is in every build and is enabled at run time
# (before, a cargo feature made only the make sync build checked, so the same venv changed speed 15x
# depending on which path built it last). doeff's pytest enables it from the root conftest.py, and
# tests/test_vm_invariant_checks_enabled.py checks it as a hard failure (no skip).
# So make sync and a bare uv sync make the same build. uv calls the build hook when doeff-vm's tool.uv.cache-keys
# (.rs, Cargo.toml, Cargo.lock, including doeff-vm-core) change, and the hook first looks up the wheel store by a key of
# the same files' contents, so it builds once, in a temporary target outside the worktree, only when the Rust source
# changed (ADR-DOE-BUILD-001 — failure case =
# tests/test_land_install_follows_rust_keys.py)。
sync:
	uv sync --group dev

pre-commit-install:
	uv run pre-commit install

# ADR-DOE-ENFORCE-001 R7: authoring-time reconciliation hook for the enforcement ledger (tracked original =
# scripts/git-hooks/pre-commit). It also applies the semgrep rules to the staged files (the items with alias
# semgrep-rules in .pre-commit-config.yaml, run by the uv.lock version of pre-commit). Machines that use the pre-commit
# framework get the same checks from make pre-commit-install (the semgrep items and enforcement-ledger in
# .pre-commit-config.yaml) — this one is a plain git hook that needs no venv. It goes into the hooks dir shared by
# all worktrees (git rev-parse --git-path hooks), so one install covers every worktree.
hooks-install:
	cp scripts/git-hooks/pre-commit "$$(git rev-parse --git-path hooks)/pre-commit"
	chmod +x "$$(git rev-parse --git-path hooks)/pre-commit"

# ADR-DOE-ENFORCE-001 R9: the enforcement ledger (docs/adr/enforcement-ledger.json) is generated — do not edit it by hand; rebuild it with this.
# Items that dropped out of the ledger (a law, test or rule removed from the tree) are reported by name; confirm the removal was intended before staging.
# The count is a stdlib-only script (PEP 723), so it needs no project environment (it does not trigger a doeff sync).
enforcement-ledger:
	uv run --script scripts/check_enforcement_ledger.py --write

# =============================================================================
# Linting - Architectural Enforcement
# =============================================================================

# Run ALL linters (core + packages)
lint: lint-ruff lint-pyright lint-semgrep lint-doeff lint-packages
	@echo ""
	@echo "All linters passed!"

# Ruff: Fast Python linter (style, imports, common issues)
lint-ruff:
	@echo "Running ruff..."
	uv run ruff check doeff/ tests/ packages/

# Pyright: Type checking
lint-pyright:
	@echo "Running pyright..."
	uv run pyright doeff/

# Semgrep: Architectural pattern enforcement (installed by make sync)
lint-semgrep:
	@echo "Running semgrep architectural rules..."
	uv run semgrep --metrics=off --disable-version-check --config .semgrep.yaml doeff/ packages/ --error

# Semgrep: Check docs for deprecated Runtime/Runner API usage
lint-semgrep-docs:
	@echo "Running semgrep on documentation..."
	uv run semgrep --metrics=off --disable-version-check --config .semgrep.yaml docs/ README.md --error

# Doeff-linter: Custom Rust-based linter for doeff patterns
lint-doeff:
	@echo "Running doeff-linter..."
	@if command -v doeff-linter >/dev/null 2>&1; then \
		root=0; doeff-linter --no-log doeff/ packages/ || root=$$?; \
		echo "Running doeff-linter in packages/doeff-cluster (with the package's settings and architecture.hy)..."; \
		cluster=0; sh $(dir $(abspath $(lastword $(MAKEFILE_LIST))))scripts/lint-doeff-cluster.sh || cluster=$$?; \
		[ "$$root" -eq 0 ] && [ "$$cluster" -eq 0 ]; \
	else \
		echo "doeff-linter is not installed, so nothing was checked. This is not treated as success." >&2; \
		echo "Install: make sync puts .venv/bin/doeff-linter in place — run it with \"uv run make lint-doeff\" (uv run puts .venv/bin first on PATH)" >&2; \
		exit 127; \
	fi

# Run lint in all subpackages that have Makefiles
lint-packages:
	@echo "Running lint in subpackages..."
	@for dir in packages/*/; do \
		if [ -f "$$dir/Makefile" ]; then \
			echo ""; \
			echo "=== Linting $$(basename $$dir) ==="; \
			$(MAKE) -C "$$dir" lint || exit 1; \
		fi; \
	done

# =============================================================================
# Testing
# =============================================================================

PYTEST_MEM_GUARD_MB ?= 8192
PYTEST_MEM_GUARD_POLL_INTERVAL ?= 1.0
# The guard settings are passed on the command line as tests/conftest.py options (conftest does not read the environment — #2896).
PYTEST_MEMORY_OPTIONS := --mem-guard-mb=$(PYTEST_MEM_GUARD_MB) \
	--mem-guard-poll-interval=$(PYTEST_MEM_GUARD_POLL_INTERVAL)

# ADR-DOE-ENFORCE-001 R4: Rust-side tests of the VM conformance oracle (invariant-checks enabled).
test-vm-invariants:
	cd packages/doeff-vm-core && cargo test --features "invariant-checks python_bridge"

test: bench-smoke
	uv run pytest $(PYTEST_MEMORY_OPTIONS)

test-unit:
	uv run pytest $(PYTEST_MEMORY_OPTIONS) -m "not e2e and not slow"

test-e2e:
	uv run pytest $(PYTEST_MEMORY_OPTIONS) -m "e2e"

# Tests of the changed parts, run locally before registering (ai land request). From the fork point (default git merge-base HEAD origin/main)
# to the worktree, it runs first the contract test sets that the changed files hit (the root pyproject.toml's [[tool.doeff.contract-tests]]),
# then the changed test files and their reverse-dependent tests, in a separate pytest per session boundary (repo root, each package's tests,
# PACKAGE_EXTRA_TEST_ROOTS — the same as test-packages below), and stops after 60 seconds in total. Files that did not finish are named as
# "not measured"; only failures give rc 1. Not run from the commit hook (#1122, #794). To change the fork point: make test-changed TEST_CHANGED_ARGS="--base <rev>".
TEST_CHANGED_ARGS ?=
test-changed:
	uv run --script scripts/run_changed_tests.py $(TEST_CHANGED_ARGS)

# Run tests in all subpackages that have tests/ directories
# - Packages with no Python test under tests/ (doeff-indexer / doeff-linter — their tests are Rust cargo test, and the
#   test_*.py under tests/fixtures are fixtures) are not passed to pytest. A test is test_*.py or test_*.hy (a Hy deftest or a
#   script run top to bottom — collected by the doeff-adr plugin via doeff_hy_test_files in the root ini). Passing them stops the loop
#   with rc 5 ("0 collected") and none of the later packages run (measured 2026-09-24).
# - e2e tests that hit a real API / real CLI are excluded (-m "not e2e"), as in the daily gate (.agents/land-queue.toml gate.full).
# - PACKAGE_UV_RUN: the daily gate calls this with `uv run --no-sync` right after make sync (so the implicit re-sync of a bare uv run
#   does not rebuild the VM just built).
# - Do not cd into the package dir; call `pytest packages/<p>/tests` from the repo root. The FAILED lines in pytest's summary are
#   relative to cwd, so running from the package dir gives `tests/test_cli.py::…`, and failure names collide between packages that
#   have test files of the same name (the daily tool passes failure names to pytest as-is from the repo root).
#   Sessions stay independent per package (folding them into one session makes same-named test files collide).
# - Do not stop at one package's failure: run every package, then name the failed packages and exit non-zero (same shape as
#   test-rust). Stopping at a failure leaves the later packages unrun and invisible in the daily run (ADR-DOE-ENFORCE-001 R8,
#   tests/test_daily_test_population.py).
# - PACKAGE_PYTEST_DURATIONS: at the end of each package session, print every test that took 10 seconds or more, longest first, with no
#   count limit (keeps per-test seconds in the daily log). In the 2026-10-09 20:00 daily run doeff-cluster grew from 814 to 1,740 seconds
#   and the packages stage timed out, but neither the log nor the ledger had per-test seconds, so the slow tests could not be found.
PACKAGE_UV_RUN ?= uv run
#   2026-10-11: removed the 20-test limit (--durations=0) — with more than 20 tests over 30 seconds in a package, the rest did not reach the log.
#   The root stage's pytest command (.agents/land-queue.toml) has the same two options.
PACKAGE_PYTEST_DURATIONS ?= --durations=0 --durations-min=10
# Test roots outside a package's tests/ (each root runs in its own session, like each package's tests/).
# - packages/doeff-cluster/src/doeff_cluster/_sim: deftests under the simulated environment (tests that run each service's entry
#   assembly with a set of simulated handlers — doeff-linter's DOEFF136 requires these tests to be under this dir; the fixtures are in the
#   package root's conftest.py (source does not import pytest — a place outside src that is an ancestor of the test dir)).
PACKAGE_EXTRA_TEST_ROOTS = packages/doeff-cluster/src/doeff_cluster/_sim
# This variable is the single definition of the population roots. The placement test (tests/test_daily_test_population.py) and the
# session boundaries of the pre-registration entry (read_session_roots in scripts/run_changed_tests.py) read it through the target below
# (do not write a second list on the test side — if you do, adding a root leaves one of them stale).
print-package-extra-test-roots:
	@echo $(PACKAGE_EXTRA_TEST_ROOTS)
# - PACKAGE_JOBS: at 2 or more, run up to that many package sessions at once (scripts/run_package_sessions.sh; output is buffered per
#   package and printed in the original order). The default 1 keeps the serial loop below. The daily gate passes it on the packages line of
#   .agents/land-queue.toml. Package sessions are independent and only share the repo root's cache. Heavy packages (PACKAGE_HEAVY_FIRST) start
#   first, to avoid the whole run being decided by one long session left at the end.
PACKAGE_JOBS ?= 1
PACKAGE_HEAVY_FIRST = doeff-cluster doeff-adr doeff-hy-pytest doeff-hy
test-packages:
	@echo "Running tests in subpackages..."
	@if [ "$(PACKAGE_JOBS)" -gt 1 ]; then \
		specs=""; \
		for name in $(PACKAGE_HEAVY_FIRST) $$(ls packages); do \
			case " $$seen " in *" $$name "*) continue;; esac; seen="$$seen $$name"; \
			dir="packages/$$name/"; \
			[ -d "$$dir/tests" ] || continue; \
			if [ -z "$$(find "$$dir/tests" \( -name 'test_*.py' -o -name 'test_*.hy' \) -not -path '*/fixtures/*' | head -1)" ]; then continue; fi; \
			specs="$$specs $$name=$${dir}tests"; \
		done; \
		for root in $(PACKAGE_EXTRA_TEST_ROOTS); do \
			if [ -d "$$root" ]; then specs="$$specs $$root=$$root"; fi; \
		done; \
		PACKAGE_SESSION_CMD='$(PACKAGE_UV_RUN) pytest' PACKAGE_SESSION_ARGS='-m "not e2e" $(PACKAGE_PYTEST_DURATIONS)' \
			sh scripts/run_package_sessions.sh $(PACKAGE_JOBS) $$specs; \
		exit $$?; \
	fi; \
	failed=""; \
	for dir in packages/*/; do \
		if [ -d "$$dir/tests" ]; then \
			if [ -z "$$(find "$$dir/tests" \( -name 'test_*.py' -o -name 'test_*.hy' \) -not -path '*/fixtures/*' | head -1)" ]; then \
				echo ""; \
				echo "=== Skipping $$(basename $$dir) (no Python tests — Rust tests run under cargo) ==="; \
				continue; \
			fi; \
			echo ""; \
			echo "=== Testing $$(basename $$dir) ==="; \
			$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$(basename $$dir)"; \
		fi; \
	done; \
	for root in $(PACKAGE_EXTRA_TEST_ROOTS); do \
		if [ ! -d "$$root" ]; then echo ""; echo "=== Skipping $$root (no such directory) ==="; continue; fi; \
		echo ""; \
		echo "=== Testing $$root ==="; \
		$(PACKAGE_UV_RUN) pytest "$$root" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$root"; \
	done; \
	if [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; exit 1; fi

# Run doeff-hy's tests with the lower bound of the Hy version doeff-hy declares (daily stage hy-floor, ADR-DOE-ENFORCE-001 R8).
# - The lower bound is read from the `"hy>=<lower>,<=<upper>"` line of packages/doeff-hy/pyproject.toml (no copy of the version here). To
#   check that it bites, run `make test-hy-floor HY_FLOOR=1.2.0` with the bound one step lower (with 1.2.0, 10 require tests fail).
# - The environment's Hy stays at the lock version; `uv run --with hy==<lower>` overlays the lower version (the lock and environment are not
#   rewritten). The R6 test reads uv.lock, so it does not fail with the overlaid Hy.
# - test_bytecode_guard.py is excluded: it measures the wrapping with the uv.lock version of Hy, and its fixture fails when the environment's
#   Hy is not the lock version (6 tests always fail with the overlaid Hy). The packages stage measures this file with the lock version.
# - Takes about 360–600 seconds (added to the daily run's ~5500 seconds). The caller passes PACKAGE_UV_RUN's `--no-sync` (the daily gate calls this right after make sync).
HY_FLOOR ?= $(shell sed -n 's/^[[:space:]]*"hy>=\([0-9][0-9.]*\),.*/\1/p' packages/doeff-hy/pyproject.toml | head -1)
test-hy-floor:
	@test -n "$(HY_FLOOR)" || { echo "test-hy-floor: cannot read the hy lower bound from packages/doeff-hy/pyproject.toml"; exit 1; }
	@echo "=== Running doeff-hy's tests with Hy $(HY_FLOOR) (the declared lower bound) ==="
	$(PACKAGE_UV_RUN) --with "hy==$(HY_FLOOR)" pytest packages/doeff-hy/tests --ignore=packages/doeff-hy/tests/test_bytecode_guard.py -m "not e2e" $(PACKAGE_PYTEST_DURATIONS)

# Run cargo test in every Rust crate under packages/ (first red does not hide the rest — every crate
# runs, the target fails at the end if any crate failed). doeff-vm / doeff-vm-core embed CPython in
# their tests (pyo3 dev-dependency feature auto-initialize), so the test binary links libpython of the
# uv environment: PYO3_PYTHON names that interpreter and the loader path names its LIBDIR (the uv
# python is not installed system-wide — without it the binaries die with "libpython3.14t.so: cannot
# open shared object file", 2026-09-24 zeus). doeff-vm-core additionally runs its invariant-checks
# conformance oracle (test-vm-invariants) because dev builds always enable it (ADR-DOE-ENFORCE-001 R4).
RUST_TEST_PYTHON = $$(uv run --no-sync python -c 'import sys; print(sys.executable)')
RUST_TEST_LIBDIR = $$(uv run --no-sync python -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')
test-rust:
	@py="$(RUST_TEST_PYTHON)"; libdir="$(RUST_TEST_LIBDIR)"; failed=""; \
	for manifest in packages/*/Cargo.toml; do \
		dir=$$(dirname "$$manifest"); \
		echo ""; \
		echo "=== cargo test $$(basename $$dir) ==="; \
		(cd "$$dir" && PYO3_PYTHON="$$py" LD_LIBRARY_PATH="$$libdir$${LD_LIBRARY_PATH:+:$$LD_LIBRARY_PATH}" \
			DYLD_FALLBACK_LIBRARY_PATH="$$libdir$${DYLD_FALLBACK_LIBRARY_PATH:+:$$DYLD_FALLBACK_LIBRARY_PATH}" \
			cargo test) || failed="$$failed $$(basename $$dir)"; \
	done; \
	echo ""; \
	echo "=== cargo test doeff-vm-core --features invariant-checks python_bridge ==="; \
	(cd packages/doeff-vm-core && PYO3_PYTHON="$$py" LD_LIBRARY_PATH="$$libdir$${LD_LIBRARY_PATH:+:$$LD_LIBRARY_PATH}" \
		DYLD_FALLBACK_LIBRARY_PATH="$$libdir$${DYLD_FALLBACK_LIBRARY_PATH:+:$$DYLD_FALLBACK_LIBRARY_PATH}" \
		cargo test --features "invariant-checks python_bridge") || failed="$$failed doeff-vm-core[invariant-checks]"; \
	if [ -n "$$failed" ]; then echo "cargo test failed:$$failed"; exit 1; fi

# Run ALL tests: core + all subpackages + Rust crates
test-all: test test-packages test-rust
	@echo ""
	@echo "All tests passed!"

test-spec-audit-sa002:
	uv run pytest tests/core/test_sa002_spec_gaps.py
	uv run semgrep --metrics=off --disable-version-check --config tests/core/sa002_semgrep_rules.yml doeff/ packages/

bench-smoke:
	uv run python benchmarks/benchmark_runner.py --smoke --no-output
	cd packages/doeff-vm-core && PYO3_PYTHON="$$(cd ../.. && uv run python -c 'import sys; print(sys.executable)')" cargo bench --features python_bridge --bench dispatch -- --test

# =============================================================================
# Formatting
# =============================================================================

format:
	uv run ruff format doeff/ tests/ packages/
	uv run ruff check --fix doeff/ tests/ packages/

check: check-repo-hygiene
	uv run ruff format --check doeff/ tests/ packages/
	uv run ruff check doeff/ tests/ packages/

check-repo-hygiene:
	bash scripts/check-repo-hygiene.sh

# =============================================================================
# Docs site
# =============================================================================

# Build the docs site (mkdocs.yml) with pinned mkdocs and Material (tools/docs_site/requirements.txt). The workspace venv is not
# needed. docs-site is the same --strict build as CI (.github/workflows/docs-site.yml); the output goes to DOCS_SITE_DIR (default site/).
# After the build it checks that the theme's CSS and JS exist — the --strict build does not fail when exclude_docs drops the theme's files.
DOCS_SITE_RUN = uv run --no-project --python 3.12 --with-requirements tools/docs_site/requirements.txt
DOCS_SITE_DIR ?= site

docs-site:
	$(DOCS_SITE_RUN) mkdocs build --strict --site-dir $(DOCS_SITE_DIR)
	ls $(DOCS_SITE_DIR)/assets/stylesheets/main.*.min.css $(DOCS_SITE_DIR)/assets/javascripts/bundle.*.min.js

docs-serve:
	$(DOCS_SITE_RUN) mkdocs serve

# =============================================================================
# Utilities
# =============================================================================

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache __pycache__
	rm -rf dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
