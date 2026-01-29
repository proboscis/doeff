# Doeff Development Makefile
# ===========================
# Centralized commands for development, testing, and linting.

.PHONY: help install lint lint-ruff lint-pyright lint-semgrep lint-doeff \
        test test-unit test-e2e format check pre-commit-install clean

# Default target
help:
	@echo "Doeff Development Commands"
	@echo "=========================="
	@echo ""
	@echo "Setup:"
	@echo "  make install           Install all dependencies (including dev)"
	@echo "  make pre-commit-install Install pre-commit hooks"
	@echo ""
	@echo "Linting (make lint runs all):"
	@echo "  make lint              Run ALL linters (ruff, pyright, semgrep, doeff-linter)"
	@echo "  make lint-ruff         Run ruff linter"
	@echo "  make lint-pyright      Run pyright type checker"
	@echo "  make lint-semgrep      Run semgrep architectural rules"
	@echo "  make lint-doeff        Run doeff-linter (Rust-based)"
	@echo ""
	@echo "Testing:"
	@echo "  make test              Run all tests"
	@echo "  make test-unit         Run unit tests only (exclude e2e)"
	@echo "  make test-e2e          Run e2e tests only"
	@echo ""
	@echo "Formatting:"
	@echo "  make format            Format code with ruff"
	@echo "  make check             Run format check without modifying files"
	@echo ""
	@echo "Utilities:"
	@echo "  make clean             Remove build artifacts and caches"

# =============================================================================
# Setup
# =============================================================================

install:
	uv sync --group dev

pre-commit-install:
	uv run pre-commit install

# =============================================================================
# Linting - Architectural Enforcement
# =============================================================================

# Run ALL linters
lint: lint-ruff lint-pyright lint-semgrep lint-doeff
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

# Semgrep: Architectural pattern enforcement
# Install semgrep with: uv tool install semgrep
lint-semgrep:
	@echo "Running semgrep architectural rules..."
	@if command -v semgrep >/dev/null 2>&1; then \
		semgrep --config .semgrep.yaml doeff/ packages/ --error; \
	else \
		echo "Warning: semgrep not installed. Install with: uv tool install semgrep"; \
		exit 1; \
	fi

# Doeff-linter: Custom Rust-based linter for doeff patterns
lint-doeff:
	@echo "Running doeff-linter..."
	@if command -v doeff-linter >/dev/null 2>&1; then \
		doeff-linter doeff/ packages/; \
	else \
		echo "Warning: doeff-linter not installed."; \
		echo "Build with: cd packages/doeff-linter && cargo install --path ."; \
	fi

# =============================================================================
# Testing
# =============================================================================

test:
	uv run pytest

test-unit:
	uv run pytest -m "not e2e and not slow"

test-e2e:
	uv run pytest -m "e2e"

# =============================================================================
# Formatting
# =============================================================================

format:
	uv run ruff format doeff/ tests/ packages/
	uv run ruff check --fix doeff/ tests/ packages/

check:
	uv run ruff format --check doeff/ tests/ packages/
	uv run ruff check doeff/ tests/ packages/

# =============================================================================
# Utilities
# =============================================================================

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache __pycache__
	rm -rf dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
