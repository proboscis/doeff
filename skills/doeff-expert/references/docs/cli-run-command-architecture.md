# doeff run Command Architecture

This note captures the refactored structure of the `doeff run` command introduced with the class-based CLI pipeline. Use it as a map when navigating or extending the workflow.

## Execution Flow

- **RunContext**
  - Collects the raw CLI arguments into a single data object.
- **RunCommand**
  - Orchestrates the entire run lifecycle.
  - Lazily initializes shared services (discovery, mergers) and the helper classes below.
- **SymbolResolver**
  - Centralises cached imports and wraps validation helpers for programs, Kleisli transforms, and program transformers.
- **ProgramBuilder**
  - Loads the target program.
  - Merges environments and injects them with `Local`.
  - Applies optional Kleisli and transformer stages sequentially.
- **RunServices**
  - Hosts CLI discovery dependencies (`IndexerBasedDiscovery`, `StandardEnvMerger`, `StandardSymbolLoader`).
- **Interpreter Execution**
  - Runs the interpreter, coercing callables into `RunResult` values when necessary.
- **_render_run_output**
  - Emits user-facing output in text or JSON while handling optional reports and call-tree visuals.

## Mermaid Overview

```mermaid
flowchart TD
    A[CLI Args] --> B[RunContext]
    B --> C[RunCommand]
    C --> D[SymbolResolver]
    C --> E[RunServices]
    E --> F[Discovery]
    C --> G[ProgramBuilder]
    F --> H[ResolvedRunContext]
    G --> I[Program w/ Env & Effects]
    C --> J[Interpreter Execution]
    J --> K[RunExecutionResult]
    K --> L[_render_run_output]
```

## Communication Diagram

```mermaid
sequenceDiagram
    participant CLI as CLI Entry
    participant RC as RunCommand
    participant SR as SymbolResolver
    participant RS as RunServices
    participant PB as ProgramBuilder
    participant INT as Interpreter
    participant OUT as Output Renderer

    CLI->>RC: Build RunContext
    RC->>RS: Initialize discovery services (lazy)
    RC->>RS: find_default_interpreter()
    RS-->>RC: interpreter path
    RC->>RS: discover_default_envs()
    RS-->>RC: env path list
    RC->>PB: load(program_path)
    PB->>SR: resolve(program symbol)
    SR-->>PB: Program
    RC->>PB: inject_envs(env sources)
    PB->>RS: merge_envs(sources)
    PB-->>RC: Program with env
    RC->>PB: apply kleisli/transformers
    PB->>SR: resolve(kleisli/transformer)
    SR-->>PB: callable
    PB-->>RC: Final Program
    RC->>SR: resolve(interpreter symbol)
    SR-->>RC: Interpreter callable/instance
    RC->>INT: run(program)
    INT-->>RC: RunResult / value
    RC->>OUT: Render with context & execution result
    OUT-->>CLI: Text/JSON output
```

## Extension Guidance

- Add new pre-run manipulations inside `ProgramBuilder` to keep run composition coherent.
- Extend auto-discovery logic via `RunServices` so dependent subsystems remain injectable.
- New output formats should be funneled through `_render_run_output` to share reporting logic.
- Maintain reusability: `SymbolResolver` should remain the single importer to avoid redundant module loads.
