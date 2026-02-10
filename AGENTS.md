# Repository Guidelines

## Project Structure & Module Organization
Core runtime code lives in `doeff/`: monadic primitives in `program.py`, execution helpers in `core.py` and `interpreter.py`, and effect definitions under `effects/` with corresponding handlers in `handlers/`. Shared utilities sit beside them in `utils.py`, `types.py`, and `cache.py`. Tests that exercise each capability reside in `tests/`, while runnable samples land in `examples/`. Workspace extensions such as OpenAI, Gemini, and pinjected bridges are published from `packages/`; keep connector-specific assets inside their respective subpackages. Use `docs/` for design notes or long-form guides that support future contributors.

## Build, Test, and Development Commands
Install everything (including dev tools) with `uv sync --group dev`. Use `uv run pytest` for the full suite, `uv run pytest tests/test_cache.py::test_cache_eviction` to target a single scenario, and `uv run pyright` for static typing. Build distributable artifacts via `uv run python -m build` before publishing packages.

## Linting & Architectural Enforcement
Run `make lint` to execute all linters (ruff, pyright, semgrep, doeff-linter). Individual targets: `make lint-ruff`, `make lint-pyright`, `make lint-semgrep`, `make lint-doeff`. Format code with `make format`. Install semgrep via `uv tool install semgrep`. Build doeff-linter with `cd packages/doeff-linter && cargo install --path .`. The `.semgrep.yaml` rules enforce architectural patterns (layer boundaries, effect system conventions); the Rust-based doeff-linter enforces immutability and code quality patterns. Install pre-commit hooks with `make pre-commit-install`.

## Coding Style & Naming Conventions
Write Python 3.10+ with four-space indentation, rich type hints, and generator-based `@do` functions when composing effects. Follow the 100-character soft limit enforced by Ruff, and rely on Ruff for import ordering. Modules, functions, and variables use `snake_case`; classes and effect types use `PascalCase`. Keep public typing metadata updated in `.pyi` files and ensure `py.typed` remains present for exported packages.

## Testing Guidelines
Pytest with strict asyncio mode powers the suite, so mark coroutines with `@pytest.mark.asyncio` or use async fixtures. Name new tests `test_<feature>.py` and structure coroutine assertions with `await` rather than event loops. Add regression coverage near related tests (for example, extend `tests/test_program_monadic_methods.py` when touching `Program`). If you introduce long-running integrations, guard them with `pytest.mark.e2e` per the configured marker list.

## Task Management
When a request is provided, always use the Task tool (TaskCreate) to break the request into concrete todo items before starting work. Mark each task as `in_progress` when you begin it and `completed` when done. This ensures progress is visible and nothing is missed.

## Commit & Pull Request Guidelines
Recent history favors concise, imperative summaries (for example, `Fix cache invalidation` or `Add Gemini structured support`). Reference related issues in the body, note behavioral risks, and list validation commands you ran. Pull requests should describe the effect on core `doeff/` APIs versus optional `packages/` integrations, attach screenshots or traces when diagnostics change, and mention follow-up work in a checklist so maintainers can track it.
