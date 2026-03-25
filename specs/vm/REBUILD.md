# doeff VM Rebuild — OCaml 5 Alignment

Branch: `rebuild/drop-stale-trace`

## Architecture (done)

- VM: 5 registers, OCaml 5 aligned
- Fiber: 3 fields (frames, parent, handler)
- Continuation: head + last_fiber, one-shot, boundary included
- Handler code runs on parent fiber (OCaml 5 semantics)
- Handler returns DoExpr (call_handler → DoCtrl)
- No auto-detection of generators in Callable.call()
- IRStream pyclass for explicit generator→stream conversion

## DoExpr Nodes (done)

| Tag | Name | Description |
|-----|------|-------------|
| 0 | Pure | Return a value |
| 5 | Perform | Perform an effect |
| 6 | Resume | Resume continuation (non-tail) |
| 7 | Transfer | Resume continuation (tail) |
| 8 | Delegate | Forward effect, append handler to k |
| 16 | Apply | Call f(args) |
| 17 | Expand | Eval inner to Stream, push as frame |
| 19 | Pass | Forward effect to outer handler |
| 20 | WithHandler | Install handler, run body |
| 21 | ResumeThrow | Throw into continuation (non-tail) |
| 22 | TransferThrow | Throw into continuation (tail) |
| 23 | GetTraceback | Non-consuming traceback query |
| 24 | WithObserve | Synchronous effect observation |
| 25 | GetExecutionContext | Current execution context (stub) |

## Packages

```
doeff/                           ← 4 files: core framework
  __init__.py, do.py, program.py, run.py

packages/doeff-vm-core/         ← Rust VM core (language-agnostic)
packages/doeff-vm/              ← Rust-Python bridge
packages/doeff-core-effects/    ← Python: reference impl
  doeff_core_effects/
    effects.py                  ← Ask, Get, Put, Tell, Try, Slog/WriterTellEffect,
                                   Local, Listen, Await, CacheGet/Put/Delete/Exists
    handlers.py                 ← reader, state, writer, try_handler, slog_handler,
                                   local_handler, listen_handler, await_handler
    scheduler.py                ← Spawn, Wait, Gather, Race, Cancel, Promise,
                                   ExternalPromise, Semaphore, Priority
    cache.py                    ← @cache decorator, cache_key, presets
    cache_effects.py            ← CacheGet/Put/Delete/Exists effects
    cache_handlers.py           ← cache_handler, memo_rewriters, content_address
    cache_policy.py             ← CachePolicy, CacheLifecycle, CacheStorage
    storage/                    ← DurableStorage, InMemoryStorage, SQLiteStorage
```

## Remaining

- [ ] GetExecutionContext — proper impl (currently stub returning basic traceback)
- [ ] ProgramCallStackEffect — for slog source attribution
- [ ] Traceback rendering — SPEC-TRACE-001 format
- [ ] daemon=True on Spawn — TBD
- [ ] Old test cleanup — tests/core/, tests/cli/ reference dead API
- [ ] Store isolation — per-task state snapshots

## Tests

93 passing (26 Rust + 67 Python)
