# Documentation Update Checklist

Last audit: 2026-07-08

## Completed (this audit)

| Area | File(s) | Notes |
| --- | --- | --- |
| OBSOLETE docs removed | `docs/cache.md`, `docs/08-graph-tracking.md`, `docs/22-with-intercept.md` | Entire files deleted — all referenced APIs (`cache`, `Step`, `Annotate`, `Snapshot`, `WithIntercept`) are `_Removed` |
| AGENTS.md project structure | `AGENTS.md` | Fixed references to non-existent `core.py`, `interpreter.py`, `effects/`, `handlers/`, `utils.py`, `types.py`, `cache.py`. Now describes actual layout (`program.py`, `do.py`, `run.py`, `result.py`, `handler_utils.py`, `cli/`) |
| AGENTS.md test example | `AGENTS.md` | `tests/test_cache.py::test_cache_eviction` replaced with existing `tests/test_core_effects.py::test_reader_ask` |
| README build command | `README.md` | `uv sync --reinstall` replaced with `make sync` + warning about stale Rust VM |
| README CLI flags | `README.md` | Deprecated flags (`--interpreter`, `--env`, `--apply`, `--transform`) marked as deprecated, `--hy` promoted |
| SPEC-WITH-INTERCEPT | `docs/specs/SPEC-WITH-INTERCEPT.md` | Added `WithObserve` pointer in existing DEPRECATED notice |
| index.md | `docs/index.md` | Removed links to deleted docs, fixed Quick Example (no `default_handlers`, correct `run()` usage), updated Effect Quick Reference, fixed code examples |
| API Reference | `docs/13-api-reference.md` | Full rewrite to match current API |

## Remaining (STALE — needs future fix)

These files still contain incorrect code examples referencing deleted APIs. Listed by priority:

### High Priority (tutorial/getting-started docs)

| File | Key Issues |
| --- | --- |
| `docs/01-getting-started.md` | `default_handlers`, `RunResult`, `async_run`, `KleisliProgram`, `Program.pure()` |
| `docs/02-core-concepts.md` | `KleisliProgram`, `Delegate`, `async_run`, `WithIntercept`, `Program.pure/map/flat_map` |
| `docs/03-basic-effects.md` | `default_handlers`, `result.value`, `Modify`, `StructuredLog`, `Delay` import path |
| `docs/04-async-effects.md` | `async_run`, `default_async_handlers`, `task.cancel()` (should be `Cancel(task)`) |
| `docs/05-error-handling.md` | `RunResult` entire section, `default_handlers` |

### Medium Priority

| File | Key Issues |
| --- | --- |
| `docs/07-cache-system.md` | `@cache` deleted, `CacheLifecycle.TEMPORARY` (should be `TRANSIENT`), `CacheStorage.DISTRIBUTED` doesn't exist |
| `docs/09-advanced-effects.md` | `Modify`, `WithIntercept`, `task.cancel()`, `Gather` usage (needs `Spawn` first) |
| `docs/11-kleisli-arrows.md` | `KleisliProgram`, `Call` (should be `Expand`), `.map/.flat_map/>>` don't exist |
| `docs/12-patterns.md` | `AtomicGet/AtomicUpdate`, `Step`, `default_handlers` |
| `docs/17-effect-boundaries.md` | `async_run`, `default_handlers` |
| `docs/20-why-effects-over-di.md` | `WithIntercept`, `Pass()` needs arguments |
| `docs/MARKERS.md` | Code examples use `program.run()`, `async_run`, `default_async_handlers` |

### Low Priority (design docs / proposals)

| File | Key Issues |
| --- | --- |
| `docs/program-architecture-overview.md` | `run(..., trace=True)`, `async_run` |
| `docs/cli-run-command-architecture.md` | `RunCommand`, `SymbolResolver` classes don't match actual `run_services.py` |
| `docs/llm_unified_effects.md` | `LLMStructuredOutput` should be `LLMStructuredQuery` |
| `docs/unified_image_effects.md` | `ImageResult` categorized as effect (it's a result type) |
| `docs/MILESTONES.md` | References deleted `_vendor.py` |
| `docs/specs/SPEC-WITHHANDLER-TYPE-FILTER.md` | `doeff/rust_vm.py`, `doeff/types.py` don't exist |
| `docs/proposals/001-doeff-flow-webui.md` | `EffectCallTree`, `.intercept()` — old APIs |
| `docs/proposals/002-run-result-printing-ownership-plan.md` | Final implementation step incomplete |
| `docs/18-effect-combinations.md` | `Log` should be `Tell` |
| `docs/12-mcp-tools.md` | `Sleep` doesn't exist in `doeff_agents.effects` |

## Cross-Cutting Deleted API Reference

For anyone fixing the remaining docs, here are the key replacements:

| Deleted API | Replacement |
| --- | --- |
| `default_handlers()` | Compose handlers individually: `writer()(state()(prog))` |
| `run(prog, handlers=..., env=..., trace=...)` | `run(doexpr)` — single argument, returns raw value |
| `RunResult[T]` / `.value` / `.is_ok()` | `run()` returns the raw value directly |
| `async_run()` | `run(scheduled(prog))` |
| `Modify(key, f)` | `Get(key)` then `Put(key, f(val))` |
| `WithIntercept(f, expr, types=, mode=)` | `WithObserve(observer, body)` |
| `KleisliProgram` | `@do` returns a normal callable |
| `task.cancel()` | `yield Cancel(task)` |
| `Gather(*programs)` | `Spawn` each first, then `Gather(*tasks)` |
| `CacheGet` | `MemoGet` (in `doeff_core_effects.cache_effects`) |
| `Delegate` | `yield effect` to re-perform |
| `Program.pure(x)` | `Pure(x)` |
| `StructuredLog` | `slog(msg, **kwargs)` or `WriterTellEffect` |
| `graph_snapshot` / `graph_to_html` | Removed entirely |
