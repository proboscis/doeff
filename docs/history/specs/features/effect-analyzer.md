# Static Effect Dependency Analyzer (SEDA)

> **Status**: 🚧 PLANNING — implementation tracked in `specs/effect-analyzer/todo.md`.

## Summary
- Build a Rust-based static analyzer (`doeff-effect-analyzer`) with PyO3 bindings that reports all
  effects reachable from a module-qualified doeff program or Kleisli entrypoint.
- Deliver instantaneous developer feedback (<= 200 ms per incremental update) by reusing a
  persistent incremental parsing and fixed-point propagation engine.
- Produce machine-consumable and human-friendly results, including a tree-shaped view of effect
  dependencies that mirrors the program call graph.

## Terminology
- **Effect key**: Canonical identifier for an effect usage (e.g., `ask:db`, `state:set`, `emit`).
- **Summary**: Per-function static snapshot with local yields, outgoing calls, and combinator hints.
- **Effect tree**: Ordered tree describing how effects flow through nested calls; root is the
  queried symbol, interior nodes are call edges, leaves are concrete yields.

## Goals
1. Statically infer the full transitive closure of effects (`E*(f)`) for any analyzed function.
2. Accept analysis targets as fully-qualified module strings (e.g., `pkg.module.program_func`),
   whether the symbol names a `KleisliProgram` definition or a bound `Program` value created by
   calling that definition.
3. Track all configured effect kinds (ask, emit, log, state, io, custom) without runtime execution.
4. Support incremental updates in <= 200 ms per touched file (95th percentile) via a long-lived
   daemon.
5. Export structured reports for CI, editor integrations, and CLI diagnostics.

## Non-Goals
- Dynamic runtime tracing of effects (analysis stays static only).
- Auto-remediation or editing of Python sources.
- Whole-project dependency installation; analyzer assumes import graph is syntactically valid.

## Functional Requirements
- Parse Python 3.10+ sources, including generator-based `@do` functions, plain callables, bound
  `Program` instances, and higher-order helpers like `partial`, `compose`, or `pipe`.
- Resolve intra-project calls across module boundaries using import statements and attribute access
  within the repository root.
- Recognize both `yield effect(...)` and `yield from effect_program()` forms.
- Treat effect recognition as configurable patterns defined in user-provided TOML or JSON.
- Emit two synchronized artifacts per query:
  1. Flat summary (`EffectSummary`) listing transitive effect keys with provenance metadata.
  2. Tree representation (`EffectTree`) capturing call-stack hierarchy; nodes include source span,
     effect aggregation, and unresolved markers.
- Provide warnings for ambiguous constructs (dynamic keys, getattr, unknown combinators).

## Architecture Overview
### Rust Crate: `doeff-effect-analyzer`
- **Parser Layer**: Tree-sitter Python with incremental parsing, rope-backed source storage, and
  file fingerprinting (content hash + mtime).
- **Summarizer**: Walks AST to build `FunctionSummary` structs containing local effects, call edges,
  assignments of callables (including `partial`), and return propagation hints.
- **Effect Registry**: Loads recognition rules from config (default rules bundled) and exposes
  matching helpers to the summarizer.
- **Call Graph Store**: Maintains arena-allocated nodes for functions, supporting fast SCC queries
  and reverse edges for invalidation.
- **Fixed-Point Resolver**: Iterates over SCCs, merging effect sets until convergence. Employs small
  bit-set representations for canonical effect IDs.
- **Tree Builder**: After computing `E*(f)`, materializes an `EffectTree` by traversing call edges
  and embedding local yield nodes; includes heuristics to prevent infinite recursion (track
  visited edges per path).
- **PyO3 Interface**: Exposes functions/classes for Python:
  - `analyze_target(dotted_path: &str, config: Option<&PyAny>) -> PyResult<Report>` – resolves the
    dotted symbol to either a Kleisli definition or a bound Program instance entirely inside Rust.
  - `start_daemon(project_root: PathBuf, options: DaemonOptions)` for long-lived mode.

### Python Integration (`doeff/analysis/seda.py`)
- Remain a thin facade: forward dotted program strings directly to the PyO3 binding (no filesystem
  crawling or AST parsing in Python).
- Deserialize reports into dataclasses (`EffectSummary`, `EffectTreeNode`) for ergonomic use.
- Provide CLI helpers (`uv run python -m doeff.analysis.seda analyze module.symbol`).

### CLI & Daemon
- Rust binary sidecar (`seda`): wraps library functionality for standalone use, supporting full
  project scans, single-symbol queries, and a daemon subcommand.
- Daemon (phase 3) uses file watching (notify/watchexec) and MessagePack RPC for editor clients.

## Data Model
```text
Report
├─ summary: EffectSummary
│  ├─ qualified_name: str
│  ├─ module: str
│  ├─ effects: [EffectUsage]
│  ├─ unresolved: [UnresolvedReference]
│  └─ statistics: SummaryMetrics
└─ tree: EffectTree
   ├─ root: TreeNode
   └─ metadata: TreeMetadata
```

`TreeNode` fields:
- `kind`: `root | function | effect | unresolved`
- `label`: human-readable identifier (`train()`, `yield ask("db")`)
- `effects`: list of canonical effect keys contributed by this node
- `span`: {file, line, column}
- `children`: ordered list of `TreeNode`

## Incremental Pipeline
1. **Change Intake**: Daemon receives file edits (path + new contents) from watcher or RPC.
2. **Incremental Parse**: Tree-sitter updates syntax tree; store parse cost metrics.
3. **Summary Rebuild**: Only functions impacted by edits (plus dependents) get recomputed.
4. **Propagation Update**: Re-run fixed-point on affected SCCs; reuse cached closures elsewhere.
5. **Tree Refresh**: Regenerate tree nodes for impacted roots; reuse JSON for untouched branches.
6. **Emit Result**: Return new report (JSON/MessagePack) including latency stats.

## Tree Output Constraints
- Depth-first ordering matches lexical nesting.
- Shared callees appear once per path; repeated visits annotated via `(recursive)` marker.
- Supports serialization to JSON, MessagePack, and DOT (for visualization) without losing
  parent-child relationships.
- Python wrapper exposes `.to_dict()` and `.pretty()` helpers for CLI usage.

## Configuration
- Default config shipped at `packages/doeff-effect-analyzer/default_effects.toml`.
- User overrides searched in project root: `seda.toml` or `seda.json`.
- Config sections:
  - `[effects.<name>]` with pattern descriptors (call name, arg positions, literal keys).
  - `[combinators.<name>]` specifying propagation strategy (`union`, `map_args`, `noop`, etc.).
  - `[limits]` for iteration caps (e.g., max recursion depth in tree output).

## Performance & Telemetry
- Use `parking_lot::RwLock` and `dashmap` for concurrent access in daemon mode.
- Metrics exposed via optional `--metrics` flag (Prometheus endpoint) or logged JSON.
- Measure:
  - Parse time, summary time, propagation time, tree build time.
  - Cache hit ratios and SCC sizes.

## Testing Strategy
- **Rust unit tests**: AST walkers, effect pattern matcher, tree builder corner cases.
- **Rust integration tests**: Sample project fixtures with known outputs; compare JSON snapshots.
- **Python tests** (`tests/test_effect_analyzer.py`): Validate PyO3 bindings, Python shim behavior,
  and CLI output.
- **Performance tests**: micro-bench ensure incremental update budget (< 200 ms) using `criterion`
  benchmarks and stress harnesses.

## Rollout Plan
1. Build static one-shot analyzer callable from Python (`analyze_symbol`).
2. Add CLI surface (`seda analyze`, `seda deps`).
3. Introduce incremental daemon, watcher, and editor integration hooks.
4. Wire CI command (`uv run seda check --against effects.toml`).

## Risks & Mitigations
- **Dynamic Python patterns**: warn + allow manual annotations; document best practices.
- **Tree explosion**: enforce recursion guards and configurable depth limits.
- **Config drift**: ship versioned defaults and validate user overrides against schema.
- **PyO3 packaging complexity**: mirror processes from `doeff-indexer`, reuse maturin config.

## Open Questions
- How should we represent third-party effects (outside repo)? Option: treat as external nodes with
  effect hints pulled from stubs.
- Should we store analyzer cache artifacts on disk for faster cold starts? (Punt to phase 2.)
