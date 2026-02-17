# Revision Log

This file records historical and migration notes that are intentionally kept out of
architecture chapters.

## DA-001 (2026-02-17)

`docs/02-core-concepts.md` was rewritten to present only current architecture.
The following historical topics were moved out of the core chapter:

- legacy `Program` dataclass wrapper representation
- legacy inheritance discussions around `ProgramBase` / `EffectBase(ProgramBase)`
- legacy writer-effect references that used `Log` examples
- legacy KPC-as-effect discussion (superseded by call-time macro model)
- legacy runtime naming references (`ProgramInterpreter`, `ExecutionContext`, `CESKRuntime`)

Current docs should describe the active model directly:

- `Program[T]` as `DoExpr[T]`
- explicit `Perform(effect)` dispatch boundary
- binary `classify_yielded` architecture and current `run` / `async_run` semantics
