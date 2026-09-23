# doeff-effect-analyzer

Static effect analysis for doeff Programs: which effects a Program performs,
which effects each handler handles (and performs in turn), and whether an env —
an ordered handler stack — handles every effect before anything runs.

## Program effects and env coverage (Python and Hy)

```bash
doeff-effects program  controllers.worker.lab.turns:turn_runner_program
doeff-effects handler  controllers.transport.clock:clock_handler
doeff-effects coverage controllers.worker.lab.turns:turn_runner_program \
    --env controllers.worker.lab.envs:board_env \
    --outer doeff_core_effects.scheduler:scheduled
```

```python
from doeff_effect_analyzer.program_effects import analyze_program
from doeff_effect_analyzer.handler_effects import analyze_env, analyze_handler, check_coverage

program = analyze_program("controllers.worker.lab.turns:turn_runner_program")
env = [analyze_handler("doeff_core_effects.scheduler:scheduled"),
       *analyze_env("controllers.worker.lab.envs:board_env")]
coverage = check_coverage(program, env)
assert coverage.complete, coverage.gaps
```

How it reads code (`python/doeff_effect_analyzer/program_effects.py`,
`handler_effects.py`):

- **Hy is macro-expanded first** with Hy's own compiler, so `defk`, `<-`,
  `defhandler` and project macros that wrap them (a `defservice` that expands to
  `defk`) are read as the Python they become.
- **Names resolve by importing** the module that defines the Program. An effect
  is any class that subclasses `doeff_vm.EffectBase` — including the project's
  own — however it was imported (aliases, re-exports). No Program is run and no
  handler is installed.
- A Program's effects: `yield E(...)` / `yield from E(...)`; calls to other
  Program functions (`yield f(...)`, `yield from f(...)`, a factory that
  `return`s one) are followed, with the call chain kept in `via`.
- Programs handed to an effect or helper (`Spawn(child(...))`,
  `RemoteJob(task(...))`, `remote_job(task(...))`) are reported as `carried`
  with their own effect sets. Whether they run under the same handlers is the
  carrier's meaning (`Spawn`: yes; `RemoteJob`: elsewhere), so the caller folds
  them in explicitly (`effect_types_with(...)`, `--fold-carried Spawn`).
- A handler clause is a branch on `isinstance(effect, T)` (or `match effect:
  case T(...)`) inside a function that dispatches on one of its parameters — the
  shape `defhandler` expands to and hand-written handlers use. The effects a
  clause performs (`(<- (Await ...))` in a clock handler) must be handled by an
  outer handler; `check_coverage` walks the env from the innermost handler out.
- An env builder is a function returning a list literal of handlers, outermost
  first. Factories returning `functools.partial(dispatch, ...)`, dispatchers that
  `return dispatch(..., effect, k)`, and wrappers around a handler call are
  followed to the function holding the `isinstance` branches.
- What cannot be followed is reported, never dropped: `unresolved` entries in a
  Program report (e.g. a yielded parameter), and handlers whose clauses cannot be
  read make `Coverage.complete` false (`unknown_handlers`).

Tests: `tests/python/` (pytest; puts `python/` on the path when the package is
not installed).

## The Rust core (`seda`, `doeff_effect_analyzer._native`)

The crate in `src/` is the earlier engine: `seda analyze` / `seda hy` and the
`analyze` / `analyze_symbol` functions (loaded lazily from the `_native`
extension). It recognises effects by a fixed vocabulary of call names (`ask(`,
`emit(` …) and reads Hy with its own reader, so it does not see project-defined
effect classes or code produced by user macros. Use the Python front end above
for effect sets and coverage.

## Building

```
uv run maturin develop --manifest-path packages/doeff-effect-analyzer/Cargo.toml
```

Mixed layout: `python-source = "python"`, extension module
`doeff_effect_analyzer._native`.
