# Implementation Checklist: Static Effect Dependency Analyzer

> **Status**: 🚧 IN PROGRESS — update this file as milestones complete.

## Phase 0: Planning & Bootstrap
- [x] Confirm crate scaffolding (`packages/doeff-effect-anlyzer`) and maturin config
- [x] Author `README.md` outlining usage, config discovery, and build steps
- [x] Establish default config (`default_effects.toml`) with core effect patterns

## Phase 1: Core Analysis Engine
- [x] Wire tree-sitter-python with initial parsing harness
- [x] Implement `FunctionSummary` builder (yields, calls, combinators)
- [x] Build dotted-path resolver in Rust to locate modules, differentiate `KleisliProgram`
      definitions versus bound `Program` values, and surface symbol metadata
- [ ] Build effect registry + pattern matching pipeline
- [ ] Persist summaries + call graph in arena-backed stores
- [ ] Implement fixed-point resolver with SCC awareness
- [x] Generate flat `EffectSummary` report structure

## Phase 2: Tree Representation & Reporting
- [ ] Implement `EffectTree` generation with recursion guards
- [ ] Provide JSON/MessagePack serializers for summaries and trees
- [ ] Add pretty-printer for CLI display (ASCII tree)
- [ ] Surface ambiguous constructs as warnings in report metadata

## Phase 3: Python Bridge & CLI
- [ ] Expose PyO3 bindings (`analyze_target`, `start_daemon`, etc.)
- [ ] Keep Python shim `doeff.analysis.seda` as a thin facade over the Rust API
- [ ] Add CLI entrypoints (`seda analyze`, `seda deps`, `seda report`)
- [ ] Document usage in `docs/` + sphinx/autodoc integration if needed

## Phase 4: Incremental Daemon & Performance
- [ ] Build watcher-backed daemon (`seda daemon`) with RPC protocol
- [ ] Cache invalidation for edited files + dependent SCCs
- [ ] Record latency metrics, enforce <= 200 ms budget in perf tests
- [ ] Support editor clients (VS Code prototype, basic LSP hooks)

## Phase 5: Validation & CI Integration
- [x] Add Rust unit + integration test suites with sample projects
- [ ] Add Python integration tests covering PyO3 bridge and CLI
- [ ] Create `seda check` command comparing against `effects.toml`
- [ ] Integrate with CI pipeline (GitHub Actions workflow draft)

## Phase 6: Adoption & Future Enhancements
- [ ] Publish migration guide for teams adopting analyzer
- [ ] Evaluate disk-backed caches for cold-start speedups
- [ ] Investigate external effect catalogs (third-party packages)
- [ ] Track stretch goals (multi-language analysis, decorator registry)
