# Removed `run_program()` API

`run_program()` and `ProgramRunResult` are not part of doeff's current public API.

This page remains as a migration note for older links and examples.

## Current Entry Points

Use the supported runtime APIs directly:

- `run(program, ...)`
- `async_run(program, ...)`

Use the CLI when you need discovery-oriented behavior:

- `doeff run ...`
- [CLI Auto-Discovery](14-cli-auto-discovery.md)
- [CLI Script Execution](15-cli-script-execution.md)

## Migration Guidance

- Replace `run_program(...)` usage in Python code with direct `run(...)` / `async_run(...)`
  execution.
- Prefer explicit `WithHandler(handler=..., expr=...)` for custom handler composition.
- Treat the `handlers=` argument on `run()` / `async_run()` as a low-level runner input rather than
  the primary way to express custom handler structure.

## See Also

- [API Reference](13-api-reference.md)
- [Effect Boundaries](17-effect-boundaries.md)
- [Program Architecture](program-architecture-overview.md)
