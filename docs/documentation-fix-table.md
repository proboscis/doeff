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

## Fixed (Phase 3 — this audit)

All previously listed STALE files have been fixed:

### High Priority (tutorial/getting-started docs) — DONE

| File | Fixes Applied |
| --- | --- |
| `docs/01-getting-started.md` | Removed `default_handlers`, `RunResult`, `async_run`, `KleisliProgram`, `Program.pure()` |
| `docs/02-core-concepts.md` | Removed `KleisliProgram`, `Delegate`, `async_run`, `WithIntercept`; fixed `Call` → `Expand` |
| `docs/03-basic-effects.md` | Removed `default_handlers`, `result.value`, `Modify` → `Get+Put`, `StructuredLog` → `slog` |
| `docs/04-async-effects.md` | Removed `async_run`, `default_async_handlers`; `task.cancel()` → `Cancel(task)` |
| `docs/05-error-handling.md` | Removed `RunResult` section; `isinstance(result, Ok)` for checking |

### Medium Priority — DONE

| File | Fixes Applied |
| --- | --- |
| `docs/07-cache-system.md` | Removed `@cache`; `TEMPORARY` → `TRANSIENT`; removed `DISTRIBUTED` |
| `docs/09-advanced-effects.md` | `Modify` → `Get+Put`; `WithIntercept` → `WithObserve`; `task.cancel()` → `Cancel(task)` |
| `docs/11-kleisli-arrows.md` | `KleisliProgram` → callable; `Call` → `Expand`; removed `.map/.flat_map/>>` |
| `docs/12-patterns.md` | Removed `AtomicGet/AtomicUpdate`, `Step`; fixed handler composition |
| `docs/17-effect-boundaries.md` | Removed `async_run`, `default_handlers`; fixed comparison table |
| `docs/18-effect-combinations.md` | `Log` → `Tell`; `WithIntercept` → `WithObserve` |
| `docs/20-why-effects-over-di.md` | `WithIntercept` → `WithObserve`; `Pass()` → `Pass(effect, k)` |
| `docs/MARKERS.md` | `program.run()` → `run(program)`; removed `async_run` |

### Low Priority (design docs / proposals) — DONE

| File | Fixes Applied |
| --- | --- |
| `docs/program-architecture-overview.md` | Removed `run(..., trace=True)`, `async_run`; updated to `WithObserve` |
| `docs/cli-run-command-architecture.md` | Updated to `RunContext`/`ResolvedRunContext`/`resolve_context()` |
| `docs/llm_unified_effects.md` | `LLMStructuredOutput` → `LLMStructuredQuery` |
| `docs/unified_image_effects.md` | `ImageResult` reclassified as result type |
| `docs/MILESTONES.md` | Updated `_vendor.py` references; noted API removals |
| `docs/specs/SPEC-WITHHANDLER-TYPE-FILTER.md` | `doeff/rust_vm.py` → `doeff/program.py`; fixed `EffectBase` import |
| `docs/proposals/001-doeff-flow-webui.md` | Added deprecation header; fixed code examples |
| `docs/proposals/002-run-result-printing-ownership-plan.md` | Marked superseded |
| `docs/12-mcp-tools.md` | `Sleep` → `ClockSleep` |

### Additional fixes (discovered during verification)

| File | Fixes Applied |
| --- | --- |
| `docs/06-io-effects.md` | Removed `default_handlers` reference |
| `docs/14-cli-auto-discovery.md` | 13 instances of `run(prog, handlers=default_handlers()).value` fixed |
| `docs/15-cli-script-execution.md` | Removed `RunResult`/`default_handlers` from variable table |
| `docs/gemini_cost_hook.md` | Removed `*default_handlers()` |
| `docs/gemini_client_setup.md` | `async_run` → `run(scheduled(...))` |
| `docs/seedream.md` | `async_run` → `run(scheduled(...))`; `Log` → `Tell` |
| `packages/doeff-llm/README.md` | `LLMStructuredOutput` → `LLMStructuredQuery` |

## Cross-Cutting Deleted API Reference

For anyone fixing the remaining docs, here are the key replacements:

| Deleted API | Replacement |
| --- | --- |
| `default_handlers()` | Compose handlers individually: `writer(state()(prog))` |
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
