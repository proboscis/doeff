# Repository Guidelines

## Project Structure & Module Organization
Core runtime code lives in `doeff/`: monadic primitives in `program.py`, execution helpers in `core.py` and `interpreter.py`, and effect definitions under `effects/` with corresponding handlers in `handlers/`. Shared utilities sit beside them in `utils.py`, `types.py`, and `cache.py`. Tests that exercise each capability reside in `tests/`, while runnable samples land in `examples/`. Workspace extensions such as OpenAI, Gemini, and pinjected bridges are published from `packages/`; keep connector-specific assets inside their respective subpackages. Use `docs/` for design notes or long-form guides that support future contributors.

## Build, Test, and Development Commands
Install everything (including dev tools) with `make sync`. Use `uv run pytest` for the full suite, `uv run pytest tests/test_cache.py::test_cache_eviction` to target a single scenario, and `uv run pyright` for static typing. Build distributable artifacts via `uv run python -m build` before publishing packages.

**WARNING — Stale Rust VM builds:** `uv sync --group dev` does NOT reliably rebuild the Rust VM extension (`packages/doeff-vm`). If you edit any `.rs` file under `packages/doeff-vm/src/`, you MUST run `make sync` (or `cd packages/doeff-vm && maturin develop --release`) to rebuild. Failing to do so will run tests against a stale binary, producing phantom failures that look like real regressions but disappear after a clean rebuild. Always use `make sync` instead of bare `uv sync`.

## Linting & Architectural Enforcement
Run `make lint` to execute all linters (ruff, pyright, semgrep, doeff-linter). Individual targets: `make lint-ruff`, `make lint-pyright`, `make lint-semgrep`, `make lint-doeff`. Format code with `make format`. Install semgrep via `uv tool install semgrep`. Build doeff-linter with `cd packages/doeff-linter && cargo install --path .`. The `.semgrep.yaml` rules enforce architectural patterns (layer boundaries, effect system conventions); the Rust-based doeff-linter enforces immutability and code quality patterns. Install pre-commit hooks with `make pre-commit-install`.

## Coding Style & Naming Conventions
Write Python 3.10+ with four-space indentation, rich type hints, and generator-based `@do` functions when composing effects. Follow the 100-character soft limit enforced by Ruff, and rely on Ruff for import ordering. Modules, functions, and variables use `snake_case`; classes and effect types use `PascalCase`. Keep public typing metadata updated in `.pyi` files and ensure `py.typed` remains present for exported packages.

## Testing Guidelines
Pytest with strict asyncio mode powers the suite, so mark coroutines with `@pytest.mark.asyncio` or use async fixtures. Name new tests `test_<feature>.py` and structure coroutine assertions with `await` rather than event loops. Add regression coverage near related tests (for example, extend `tests/test_program_monadic_methods.py` when touching `Program`). If you introduce long-running integrations, guard them with `pytest.mark.e2e` per the configured marker list.

## Task Management
When a request is provided, always use the Task tool (TaskCreate) to break the request into concrete todo items before starting work. Mark each task as `in_progress` when you begin it and `completed` when done. This ensures progress is visible and nothing is missed.

## Orch Run Management (CRITICAL)
When using `orch` to manage agent runs, **NEVER stop a run that is in `wait` or `blocked` state**. These states mean the agent is alive and waiting for user input — use `orch send <RUN_REF> "message"` to communicate with it. Stopping a waiting run kills the session, loses all agent context, and forces a costly restart. Only use `orch stop` on runs that are clearly stale (hours of no progress with `running` state) or confirmed dead (`unknown`/`failed`). When in doubt, use `orch capture` to check the agent's output before deciding.

| Run State | Action |
|-----------|--------|
| `wait` / `blocked` | `orch send` — NEVER `orch stop` |
| `running` | Leave alone or `orch send` if guidance needed |
| `failed` / `unknown` | OK to `orch stop` + `orch continue` |
| `done` / `cancel` | Already terminated |

## TDD + Semgrep Enforcement Strategy

Every issue that adds, removes, or changes an architectural invariant MUST follow this protocol:

### Phase 1: Write Failing Tests (TDD)
1. Write tests that assert the NEW expected behavior FIRST
2. Run them — they MUST fail (proves the test is meaningful)
3. Commit the failing tests to the branch (they belong to the issue)

### Phase 2: Write Semgrep Guard Rules
If the change introduces a ban (removed API, forbidden pattern, layer boundary):
1. Write a semgrep rule in `.semgrep.yaml` that bans the OLD pattern (severity: `ERROR`)
2. Run it — it SHOULD fire on existing code (confirms the rule works)
3. The rule stays permanently as a regression guard after migration

For NEW patterns worth protecting:
1. Identify the invariant: "what would break if someone did X?"
2. Write a semgrep rule banning X
3. Include the "why" in the `message:` field with spec/issue reference

### Phase 3: Implement
1. Make the code changes that satisfy both the tests and the semgrep rules
2. All tests pass, `make lint` clean (including the new semgrep rules)

### Why This Order Matters
- Tests prove the change works (regression guard at runtime)
- Semgrep rules prevent the old pattern from creeping back (regression guard at lint time)
- Writing them first forces precise thinking about what exactly is changing
- Agents that skip this produce unverifiable work

### Issue Template Pattern
Issues SHOULD include:
```
## Failing Tests (commit first)
- test_foo_new_behavior: asserts X
- test_bar_rejects_old_api: asserts Y is gone

## Semgrep Rules (commit with tests or with fix)
- rule-id: ban-old-pattern — bans Z in paths P

## Implementation
- change A in file B
- remove C from file D

## Acceptance Criteria
1. Failing tests now pass
2. Semgrep rules pass (no violations)
3. All existing tests still pass
4. `make lint` clean
```

## Commit & Pull Request Guidelines
Recent history favors concise, imperative summaries (for example, `Fix cache invalidation` or `Add Gemini structured support`). Reference related issues in the body, note behavioral risks, and list validation commands you ran. Pull requests should describe the effect on core `doeff/` APIs versus optional `packages/` integrations, attach screenshots or traces when diagnostics change, and mention follow-up work in a checklist so maintainers can track it.
