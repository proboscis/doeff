# Doeff Development Makefile
# ===========================
# Centralized commands for development, testing, and linting.

.PHONY: help install sync lint lint-ruff lint-pyright lint-semgrep lint-semgrep-docs lint-doeff lint-packages \
        test test-unit test-e2e test-packages test-rust test-all test-spec-audit-sa002 bench-smoke format check check-repo-hygiene \
        pre-commit-install hooks-install clean install-opencode-spec-gap-tdd

# Default target
help:
	@echo "Doeff Development Commands"
	@echo "=========================="
	@echo ""
	@echo "Setup:"
	@echo "  make install           Install all dependencies (including dev)"
	@echo "  make sync              Install deps + rebuild Rust VM extension"
	@echo "  make pre-commit-install Install pre-commit hooks"
	@echo "  make hooks-install     Install raw git pre-commit hook (enforcement ledger, ADR-DOE-ENFORCE-001 R7)"
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
	@echo "  make test-packages     Run tests in all subpackages"
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
	@echo "Utilities:"
	@echo "  make clean             Remove build artifacts and caches"
	@echo "  make install-opencode-spec-gap-tdd Install OpenCode spec-gap-tdd symlinks"

# =============================================================================
# Setup
# =============================================================================

install:
	uv sync --group dev

# Sync dependencies AND rebuild the Rust VM extension.
# ALWAYS use this instead of bare `uv sync` when Rust sources changed.
# ADR-DOE-ENFORCE-001 R4 (B3 裁定 2026-07-14): dev ビルドは VM conformance oracle
# (invariant-checks) を常時有効にする。tests/test_vm_invariant_checks_enabled.py が
# フラグを hard-fail で検査する(skip 禁止)。
sync:
	uv sync --group dev
	cd packages/doeff-vm && maturin develop --release --features invariant-checks

pre-commit-install:
	uv run pre-commit install

# ADR-DOE-ENFORCE-001 R7: enforcement 台帳の著述時突合 hook(tracked 原本 =
# scripts/git-hooks/pre-commit)。pre-commit framework を使う機体は
# make pre-commit-install 側でも同じ検査が入る(.pre-commit-config.yaml の
# enforcement-ledger)— こちらは venv 不要の素の git hook。worktree 共通の
# hooks dir(git rev-parse --git-path hooks)へ入るので、一度の導入で全 worktree に効く。
hooks-install:
	cp scripts/git-hooks/pre-commit "$$(git rev-parse --git-path hooks)/pre-commit"
	chmod +x "$$(git rev-parse --git-path hooks)/pre-commit"

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
		doeff-linter --no-log doeff/ packages/; \
	else \
		echo "doeff-linter が未導入のため検査できません。成功として扱いません。" >&2; \
		echo "導入: cd packages/doeff-linter && cargo install --path ." >&2; \
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
PYTEST_MEMORY_ENV := PYTEST_MEM_GUARD_MB=$(PYTEST_MEM_GUARD_MB) \
	PYTEST_MEM_GUARD_POLL_INTERVAL=$(PYTEST_MEM_GUARD_POLL_INTERVAL)

# ADR-DOE-ENFORCE-001 R4: VM conformance oracle の Rust 側テスト(invariant-checks 有効)。
test-vm-invariants:
	cd packages/doeff-vm-core && cargo test --features "invariant-checks python_bridge"

test: bench-smoke
	$(PYTEST_MEMORY_ENV) uv run pytest

test-unit:
	$(PYTEST_MEMORY_ENV) uv run pytest -m "not e2e and not slow"

test-e2e:
	$(PYTEST_MEMORY_ENV) uv run pytest -m "e2e"

# Run tests in all subpackages that have tests/ directories
# - tests/ に Python の検が 1 本も無い package(doeff-indexer / doeff-linter — 検は Rust の cargo test、
#   tests/fixtures の test_*.py は検体)は pytest に渡さない。渡すと「収集 0 件」の rc 5 で loop が止まり、
#   後ろの package が 1 本も走らない(2026-09-24 実測)。
# - 実 API / 実 CLI を撃つ e2e は日次の門(.agents/land-queue.toml gate.full)と同じく除く(-m "not e2e")。
# - PACKAGE_UV_RUN: 日次の門は make sync の直後に `uv run --no-sync` で呼ぶ(素の uv run の暗黙の再 sync が
#   invariant-checks の build を上書きしないように — gate.full の頭注と同じ理由)。
# - package の dir へ cd せず、repo の根から `pytest packages/<p>/tests` を呼ぶ。pytest の要約の FAILED 行は
#   cwd からの相対なので、package の dir から走らせると `tests/test_cli.py::…` の形になり、同名の test file を
#   持つ package どうしで失敗名が衝突する(日次の道具は失敗名を repo の根から pytest へそのまま渡す)。
#   session は package ごとに独立のまま(1 session に畳むと同名の test file が衝突する)。
# - 1 package の赤で止めず全 package を走らせ、最後に失敗の package を名指して 0 以外で終わる(test-rust と
#   同じ形)。赤で止めると後ろの package が 1 本も走らないまま日次に見えない(ADR-DOE-ENFORCE-001 R8・
#   tests/test_daily_test_population.py・card acp:kanban-issue:ki-08ec2d7c901f)。
PACKAGE_UV_RUN ?= uv run
test-packages:
	@echo "Running tests in subpackages..."
	@failed=""; \
	for dir in packages/*/; do \
		if [ -d "$$dir/tests" ]; then \
			if [ -z "$$(find "$$dir/tests" -name 'test_*.py' -not -path '*/fixtures/*' | head -1)" ]; then \
				echo ""; \
				echo "=== Skipping $$(basename $$dir) (no Python tests — Rust tests run under cargo) ==="; \
				continue; \
			fi; \
			echo ""; \
			echo "=== Testing $$(basename $$dir) ==="; \
			$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" || failed="$$failed $$(basename $$dir)"; \
		fi; \
	done; \
	if [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; exit 1; fi

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
	uv run semgrep --metrics=off --disable-version-check --config specs/audits/SA-002/semgrep/rules.yml doeff/ packages/

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
# Utilities
# =============================================================================

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache __pycache__
	rm -rf dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

install-opencode-spec-gap-tdd:
	bash scripts/install-opencode-spec-gap-tdd.sh
