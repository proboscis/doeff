---
name: DoeffExpert
description: >
  Expert guidance on writing Python programs using the doeff library (free monad,
  do-notation, effects), including CLI auto-discovery, markers, and transforms.
---

# DoeffExpert

## Load Full Topic References

- Use the references below as the canonical, full-text docs for each topic.
- Read `references/docs/index.md` to locate the right chapter quickly.
- Load only the files needed for the current task to keep context tight.

### Foundations

- Read `references/docs/01-getting-started.md` for install/setup and first program.
- Read `references/docs/02-core-concepts.md` for Program, Effect, and interpretation.
- Read `references/docs/03-basic-effects.md` for Reader/State/Writer basics.
- Read `references/docs/04-async-effects.md` for concurrency and async effects.
- Read `references/docs/05-error-handling.md` for failure, retry, and recovery.
- Read `references/docs/06-io-effects.md` for IO and user interaction effects.
- Read `references/docs/07-cache-system.md` for caching primitives and usage.
- Read `references/docs/08-graph-tracking.md` for graph tracking and snapshots.
- Read `references/docs/09-advanced-effects.md` for advanced effect types.
- Read `references/docs/12-patterns.md` for composition and reuse patterns.

### Composition and API Surface

- Read `references/docs/11-kleisli-arrows.md` for Kleisli composition and `@do` rules.
- Read `references/docs/13-api-reference.md` for the full API surface.
- Read `references/docs/16-run-program-api.md` for program execution APIs.

### CLI, Markers, and Tooling

- Read `references/docs/14-cli-auto-discovery.md` for interpreter/env discovery.
- Read `references/docs/15-cli-script-execution.md` for CLI scripting hooks.
- Read `references/docs/MARKERS.md` for marker syntax and CLI discovery tags.
- Read `references/packages/doeff-indexer/SPECIFICATION.md` for indexer rules and
  transform/Kleisli discovery semantics.
- Read `references/docs/ide-plugins.md` for IDE integration behavior.

### Architecture and Internals

- Read `references/docs/program-architecture-overview.md` for runtime flow and CLI
  execution stages.
- Read `references/docs/cli-run-command-architecture.md` for CLI run command design.
- Read `references/docs/filesystem-effect-architecture.md` for filesystem effect details.
- Read `references/docs/cache.md` for cache architecture notes.
- Read `references/docs/abstraction_concern.md` for design tradeoffs and rationale.

### Integrations and Add-ons

- Read `references/docs/10-pinjected-integration.md` for DI integration patterns.
- Read `references/docs/gemini_client_setup.md` for Gemini client setup guidance.
- Read `references/docs/gemini_cost_hook.md` for Gemini cost hook configuration.
- Read `references/docs/seedream.md` for Seedream integration notes.

### Analysis and Troubleshooting

- Read `references/docs/effect-analyzer/architecture.md` for effect analyzer design.
- Read `references/docs/effect-analyzer/test-scenarios.md` for analyzer scenarios.
- Read `references/docs/troubleshooting/case_pycharm_indexer_not_finding_symbols.md`
  for PyCharm indexer troubleshooting.

## Explain Pipeline Oriented Programming

- Treat each `@do` function as a pipeline step (a Kleisli arrow returning `Program[T]`).
- Compose steps with `>>` or `.and_then_k` and keep effects explicit via `yield`.
- Isolate cross-cutting changes in transforms (`Program[T] -> Program[U]`) instead of editing
  business steps.
- Keep environment dependencies in `Program[dict]` values and read them via `Ask`/`Local`.

## Use Core doeff Building Blocks

- Use `@do` to define generator-based programs; `yield` effects and `return` values.
- Treat `Program[T]` as the monadic container and interpret only at the boundary.
- Choose effect types (Ask, Log, Put, Fail, etc.) to model context, state, IO, and errors.

## Mark and Discover Kleisli Tools (T -> Program[U])

- Mark Kleisli arrows with `# doeff: kleisli` so `find-kleisli` can discover them.
- Prefer `@do` so Program arguments auto-unwrap for natural composition.
- Use `find-kleisli --type-arg <T>` when narrowing candidates.

```python
from doeff import do, Effect

@do
def fetch_user(user_id: str):  # doeff: kleisli
    user = yield Effect("fetch_user", user_id=user_id)
    return user
```

```bash
find-kleisli .
find-kleisli --type-arg str .
```

## Mark and Discover Transforms (Program[T] -> Program[U])

- Mark transforms with `# doeff: transform` so `find-transforms` can discover them.
- Apply transforms sequentially with `doeff run --transform path.to.transform`.

```python
from doeff import Program

def add_trace(program: Program[dict]) -> Program[dict]:  # doeff: transform
    return program.map(lambda data: {**data, "trace": "enabled"})
```

```bash
find-transforms .
doeff run --program myapp.features.auth.login_program --transform myapp.transforms.add_trace
```

## Enable Auto-Discovery of Interpreters and Environments

- Mark interpreter functions with `# doeff: interpreter, default` (inline or docstring).
- Mark default environments with `# doeff: default` and declare them as `Program[dict]`.
- Rely on auto-discovery to pick the closest default interpreter and merge envs root-to-leaf.

```python
from doeff import Program, ProgramInterpreter

def app_interpreter(program: Program):
    """
    Execute programs for the app.
    # doeff: interpreter, default
    """
    return ProgramInterpreter().run(program).value

# doeff: default
base_env: Program[dict] = Program.pure({"timeout": 10, "db_host": "localhost"})
```

## Run Programs via the CLI

- Run `doeff run --program package.module.program` to use auto-discovered interpreter and envs.
- Override discovery with `--interpreter` or add environments with `--env`.
- Combine `--transform` (Program -> Program) and `--apply` (Kleisli) for pipeline customization.
