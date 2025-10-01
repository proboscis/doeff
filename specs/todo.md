# Implementation Checklist: Doeff CLI Enhancement (Updated)

## Phase 0: Investigation & Prerequisites

### 0.1: Doeff-Indexer Analysis
- [ ] Locate and read `doeff-indexer` source code
- [ ] Determine if docstring parsing exists or needs to be added
- [ ] Check if Python API exists (PyO3 bindings)
- [ ] Document current indexer capabilities
- [ ] Identify gaps that need to be filled

### 0.2: Existing Implementation Review
- [ ] Read `doeff/interpreter.py` to understand `ProgramInterpreter.run()`
- [ ] Find all current uses of `await interpreter.run()` in codebase
- [ ] Read `doeff/effects.py` or equivalent to find `Local` effect
- [ ] Read `doeff/__main__.py` current implementation
- [ ] Understand current `@do` decorator usage patterns

### 0.3: Architecture Approval
- [ ] Review `tmp/architecture.md` with stakeholder
- [ ] Confirm understanding of env accumulation strategy
- [ ] Confirm breaking change for `ProgramInterpreter.run()` is acceptable
- [ ] Get approval to proceed with implementation

## Phase 1: ProgramInterpreter Refactor (BREAKING CHANGE)

### 1.1: Test Preparation
- [ ] Write test for current async `ProgramInterpreter.run()` behavior
- [ ] Write test for new sync `ProgramInterpreter.run()` behavior
- [ ] Test: Verify `asyncio.run()` is called internally
- [ ] Test: Verify return type is `RunResult[Any]` (not coroutine)
- [ ] Test: Verify async programs still execute correctly

### 1.2: Implementation
- [ ] Update `ProgramInterpreter.run()` signature: remove `async`
- [ ] Create internal `_run_async()` method with current logic
- [ ] Call `asyncio.run(self._run_async(program))` from `run()`
- [ ] Run unit tests and fix failures

### 1.3: Update Callers
- [ ] Find all `await interpreter.run()` calls in codebase
- [ ] Update `__main__.py`: Remove `await` from `interpreter.run()` calls
- [ ] Remove `asyncio.run()` calls in `__main__.py` (now internal)
- [ ] Update any test files
- [ ] Run full test suite: `uv run pytest -v`

### 1.4: Validation
- [ ] Verify no `await interpreter.run()` remains
- [ ] Verify all tests pass
- [ ] Run linter and fix any issues
- [ ] Manual smoke test: `uv run python -m doeff run --program ... --interpreter ...`

## Phase 2: Doeff-Indexer Enhancement

### 2.1: Docstring Parsing (if needed)
- [ ] Determine if indexer parses docstrings already
- [ ] If not, write Rust tests for docstring parsing
  - Test: Extract `# doeff: interpreter, default` from docstring
  - Test: Extract `# doeff: default` from variable docstring
  - Test: Handle multi-line docstrings
  - Test: Ignore non-doeff markers
- [ ] Implement docstring parsing in Rust
- [ ] Run `cargo test` in indexer crate and fix

### 2.2: Python API - PyO3 Bindings (TDD)
- [ ] Write Python tests for indexer API
  - Test: `Indexer.for_module("some.module")` creates indexer
  - Test: `find_symbols(tags=["doeff", "interpreter", "default"])` returns functions
  - Test: `find_symbols(tags=["doeff", "default"], symbol_type="variable")` returns vars
  - Test: `get_module_hierarchy()` returns module path list
  - Test: Symbol metadata includes module_path, full_path, tags, line_number
- [ ] Implement PyO3 `SymbolInfo` struct with `#[pyclass]`
- [ ] Implement PyO3 `Indexer` class with `#[pyclass]`
- [ ] Implement `#[pymethods]`: `for_module()`, `find_symbols()`, `get_module_hierarchy()`
- [ ] Configure `maturin` build
- [ ] Build and install: `maturin develop` or `maturin build --release`
- [ ] Run Python tests and fix

### 2.3: Integration Test
- [ ] Create test Python project with nested modules
- [ ] Add functions with `# doeff: interpreter, default` markers
- [ ] Add variables with `# doeff: default` markers
- [ ] Test indexer finds all markers correctly
- [ ] Verify performance (should be < 1 second)

## Phase 3: Local Effect Enhancement

### 3.1: Tests for Local Effect (TDD)
- [ ] Test: `Local(dict, program)` works (existing behavior)
- [ ] Test: `Local(Program.pure(dict), program)` works (new behavior)
- [ ] Test: Merged env correctly with Program[dict]
- [ ] Test: No async/await in user code (composition only)
- [ ] Test: Verify @do composition pattern works

### 3.2: Implementation
- [ ] Update `Local.__init__()` signature: `env: dict | Program[dict]`
- [ ] Implement `Local.as_program()` method using @do composition
- [ ] Handle both dict and Program[dict] cases
- [ ] Ensure no direct async/await (use yield in @do)
- [ ] Run tests: `uv run pytest tests/test_effects.py`

### 3.3: Backward Compatibility Check
- [ ] Find existing uses of `Local(dict, ...)` in codebase
- [ ] Verify they still work after changes
- [ ] Run full test suite

## Phase 4: Python Discovery Layer

### 4.1: IndexerBasedDiscovery - Interpreter Discovery (TDD)
- [ ] Test: Find single default interpreter
- [ ] Test: Find closest interpreter among multiple
- [ ] Test: Return None when no default found
- [ ] Test: Correct module hierarchy parsing
- [ ] Test: Filter to relevant module path
- [ ] Implement `IndexerBasedDiscovery.find_default_interpreter()`
- [ ] Run tests and fix

### 4.2: IndexerBasedDiscovery - Env Discovery (TDD)
- [ ] Test: Discover env from single module
- [ ] Test: Discover and accumulate envs from multiple modules
- [ ] Test: Correct ordering (root → program)
- [ ] Test: Return empty list when no envs found
- [ ] Test: Handle nested module structures
- [ ] Implement `IndexerBasedDiscovery.discover_default_envs()`
- [ ] Run tests and fix

### 4.3: StandardEnvMerger (TDD)
- [ ] Test: Merge two dicts
- [ ] Test: Merge dict + Program[dict]
- [ ] Test: Merge multiple Program[dict]
- [ ] Test: Left-to-right merge (later overrides)
- [ ] Test: Result is Program[dict]
- [ ] Test: Uses @do composition (no await)
- [ ] Implement `StandardEnvMerger.merge_envs()` using @do
- [ ] Run tests and fix

### 4.4: Integration Tests for Discovery Layer
- [ ] Test: Full interpreter discovery workflow
- [ ] Test: Full env discovery and merging workflow
- [ ] Test: Combined interpreter + env discovery
- [ ] Run `uv run pytest tests/test_discovery.py -v`

## Phase 5: CLI Integration

### 5.1: Argument Parser Updates (TDD)
- [ ] Test: `--interpreter` is now optional
- [ ] Test: `--env` accepts multiple values (action="append")
- [ ] Test: Parse multiple `--env path1 --env path2`
- [ ] Test: Backward compatibility with existing args
- [ ] Update `build_parser()`: Make `--interpreter` required=False
- [ ] Add `--env` argument with `action="append"`
- [ ] Update `RunContext` dataclass: `env_paths: list[str]`
- [ ] Run tests and fix

### 5.2: handle_run() - Discovery Integration (TDD)
- [ ] Test: Discover interpreter when not provided
- [ ] Test: Use provided interpreter (skip discovery)
- [ ] Test: Raise error when no interpreter found
- [ ] Test: Error message is helpful (3 options)
- [ ] Implement interpreter discovery logic in `handle_run()`
- [ ] Implement helpful error message
- [ ] Run tests and fix

### 5.3: handle_run() - Env Integration (TDD)
- [ ] Test: Discover default envs
- [ ] Test: Merge default + explicit envs
- [ ] Test: Handle no envs (skip wrapping)
- [ ] Test: Wrap program with Local effect
- [ ] Test: Multiple `--env` flags merge correctly
- [ ] Implement env discovery in `handle_run()`
- [ ] Implement env merging and wrapping
- [ ] Run tests and fix

### 5.4: Error Handling & Output
- [ ] Implement helpful error for no interpreter found
- [ ] Implement error for invalid env
- [ ] Update JSON output format to include discovery info
- [ ] Test error messages in integration tests
- [ ] Verify error handling with malformed inputs

### 5.5: Integration Test - CLI
- [ ] Test: `doeff run --program X` discovers interpreter
- [ ] Test: `doeff run --program X --interpreter Y` uses Y
- [ ] Test: `doeff run --program X --env E` merges env
- [ ] Test: `doeff run --program X` discovers and accumulates envs
- [ ] Test: Full workflow with all features
- [ ] Run `uv run pytest tests/test_cli.py -v`

## Phase 6: End-to-End Testing

### 6.1: Test Fixtures Setup
- [ ] Create `tests/fixtures/example_project/` structure
- [ ] Create `tests/fixtures/example_project/some/__init__.py` with base interpreter
- [ ] Create `tests/fixtures/example_project/some/module/__init__.py` with closer interpreter
- [ ] Create env files at multiple levels with `# doeff: default`
- [ ] Create test programs at various depths

### 6.2: E2E Test Cases
- [ ] E2E: Interpreter discovery finds closest match
- [ ] E2E: Env accumulation merges from root to program
- [ ] E2E: Multiple `--env` flags override correctly
- [ ] E2E: No interpreter error shows helpful message
- [ ] E2E: Backward compatibility (explicit --interpreter and --env)
- [ ] E2E: JSON output format
- [ ] E2E: Program execution produces correct results
- [ ] Run `uv run pytest tests/test_e2e.py -v`

## Phase 7: Code Quality & Review

### 7.1: SOLID Principles Review
- [ ] Single Responsibility: Each class has one clear purpose
- [ ] Open/Closed: Can extend without modifying
- [ ] Liskov Substitution: Subclasses don't break contracts
- [ ] Interface Segregation: Protocols are minimal
- [ ] Dependency Inversion: Depend on abstractions

### 7.2: Complexity Analysis
- [ ] Run linter: `uv run ruff check doeff/`
- [ ] Check for C901, PLR0912 violations
- [ ] Refactor if complexity is too high
- [ ] Verify all functions are reasonably simple

### 7.3: Test Coverage
- [ ] Run coverage: `uv run pytest --cov=doeff --cov-report=html`
- [ ] Verify >90% coverage for new code
- [ ] Add tests for uncovered branches
- [ ] Review coverage report

### 7.4: Error Handling Audit
- [ ] Verify all errors are raised, not logged and ignored
- [ ] Check no fallback logic unless specified
- [ ] Confirm error messages are helpful and actionable
- [ ] No `# doeff:` bypassing or workarounds

## Phase 8: Documentation & Examples

### 8.1: Code Documentation
- [ ] Add docstrings to all new functions and classes
- [ ] Document `# doeff:` marker syntax
- [ ] Add type hints throughout
- [ ] Document return types and exceptions

### 8.2: User Documentation
- [ ] Update README with new features
- [ ] Document interpreter discovery feature
- [ ] Document env accumulation feature
- [ ] Provide examples of `# doeff: interpreter, default`
- [ ] Provide examples of `# doeff: default` for envs
- [ ] Show `--env` flag usage

### 8.3: Migration Guide
- [ ] Write migration guide for `ProgramInterpreter.run()` change
- [ ] List breaking changes
- [ ] Provide before/after examples
- [ ] Document upgrade path

### 8.4: Examples
- [ ] Create example project with default interpreter
- [ ] Create example project with env accumulation
- [ ] Create example using multiple `--env` flags
- [ ] Test all examples work

## Phase 9: Final Validation

### 9.1: Full Test Suite
- [ ] Run all unit tests: `uv run pytest -v`
- [ ] Run integration tests
- [ ] Run E2E tests
- [ ] Verify 100% pass rate

### 9.2: Linting & Type Checking
- [ ] Run linter: `uv run ruff check doeff/`
- [ ] Fix all linter issues (no bypassing)
- [ ] Run type checker: `uv run mypy doeff/`
- [ ] Fix type errors

### 9.3: Performance Validation
- [ ] Measure interpreter discovery time
- [ ] Measure env discovery time
- [ ] Verify both < 1 second
- [ ] Profile CLI startup time
- [ ] Ensure no regression for existing workflows

### 9.4: Manual Testing
- [ ] Test: `uv run python -m doeff run --program X`
- [ ] Test: `uv run python -m doeff run --program X --env Y`
- [ ] Test: `uv run python -m doeff run --program X --interpreter Z`
- [ ] Test: Error cases produce helpful messages
- [ ] Test: `--format json` works

## Phase 10: Completion

- [ ] Clean up temporary files in `./tmp`
- [ ] Update CHANGELOG.md (if exists)
- [ ] Review all changes one final time
- [ ] Ensure commit message follows conventions
- [ ] Run `uv run python -m pinjected list doeff` if using pinjected
- [ ] Send Slack notification to #all-cryptic-dev about task completion

## Notes

- **TDD CRITICAL**: Write tests BEFORE implementation
- **No async/await**: Use @do composition for all Program operations
- **Breaking Change First**: Phase 1 must complete before others
- **Indexer is Source of Truth**: Don't reimplement indexer logic
- **Helpful Errors**: Always provide actionable error messages
- **No Bypassing**: Fix linter issues, don't ignore them
- **Ask User**: If any design decision is unclear, ask before proceeding
