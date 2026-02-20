# doeff-agentic Development Makefile
# ===================================
# Centralized commands for development, testing, and linting.

.PHONY: help install lint lint-ruff lint-pyright lint-semgrep test format check clean

# Default target
help:
	@echo "doeff-agentic Development Commands"
	@echo "==================================="
	@echo ""
	@echo "Linting (make lint runs all):"
	@echo "  make lint              Run ALL linters"
	@echo "  make lint-ruff         Run ruff linter"
	@echo "  make lint-pyright      Run pyright type checker"
	@echo "  make lint-semgrep      Run semgrep architectural rules"
	@echo ""
	@echo "Testing:"
	@echo "  make test              Run all tests"
	@echo ""
	@echo "Formatting:"
	@echo "  make format            Format code with ruff"
	@echo "  make check             Run format check without modifying files"

# =============================================================================
# Linting - Architectural Enforcement
# =============================================================================

# Run ALL linters
lint: lint-semgrep
	@echo ""
	@echo "All linters passed!"

# Ruff: Fast Python linter (style, imports, common issues)
lint-ruff:
	@echo "Running ruff..."
	uv run ruff check src/ tests/ examples/

# Pyright: Type checking
lint-pyright:
	@echo "Running pyright..."
	uv run pyright src/

# Semgrep: Architectural pattern enforcement
# Install semgrep with: uv tool install semgrep
lint-semgrep:
	@echo "Running semgrep architectural rules..."
	@if command -v semgrep >/dev/null 2>&1; then \
		semgrep --config .semgrep.yaml src/ examples/ --error; \
	else \
		echo "Warning: semgrep not installed. Install with: uv tool install semgrep"; \
		exit 1; \
	fi

# =============================================================================
# Testing
# =============================================================================

test:
	uv run pytest tests/

# =============================================================================
# Formatting
# =============================================================================

format:
	uv run ruff format src/ tests/ examples/
	uv run ruff check --fix src/ tests/ examples/

check:
	uv run ruff format --check src/ tests/ examples/
	uv run ruff check src/ tests/ examples/

# =============================================================================
# Utilities
# =============================================================================

clean:
	rm -rf .pytest_cache .ruff_cache __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
