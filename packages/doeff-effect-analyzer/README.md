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
    --outer doeff_core_effects._scheduler:scheduled
# a Program that takes its foundation as a parameter
doeff-effects coverage lab.jobs:job --env lab.envs:no_handlers \
    --outer doeff_core_effects._scheduler:scheduled --fold-carried Spawn \
    --bind foundation=lab.envs:production_handlers
```

A Program asks with effects, and the handler made by `effect_analysis()` answers
them by reading the sources (the CLI command runs the same effects; ADR-DOE-API-001
R24):

```python
from doeff import do, run
from doeff_effect_analyzer import (
    AnalyzeEnv, AnalyzeHandler, AnalyzeProgram, CheckCoverage, FoldCarried, effect_analysis,
)

@do
def board_is_covered():
    program = yield AnalyzeProgram("controllers.worker.lab.turns:turn_runner_program")
    scheduler = yield AnalyzeHandler("doeff_core_effects._scheduler:scheduled")
    env = yield AnalyzeEnv("controllers.worker.lab.envs:board_env")
    coverage = yield CheckCoverage(program, env=(scheduler, *env))
    assert coverage.complete, coverage.gaps

    # (defk job [foundation] (<- base list (foundation)) (<- (with-handlers base (body))))
    # read here with its parameter bound; carried Programs whose carrier says it runs them
    # where performed (Spawn, Try …) are counted there.
    job = yield CheckCoverage("lab.jobs:job", env=(scheduler,),
                              bindings={"foundation": production_handlers},
                              fold=FoldCarried(declared=True))
    assert job.complete, job.gaps

run(effect_analysis()(board_is_covered()))
```

| Effect | Answer |
|---|---|
| `AnalyzeProgram(target, bindings=None)` | `ProgramEffects` — what a Program function performs |
| `AnalyzeHandler(target, name=None)` | `HandlerEffects` — what a handler answers and its clauses perform |
| `AnalyzeEnv(builder, bindings=None)` | `tuple[HandlerEffects, ...]` — an env builder's handlers, outermost first |
| `CheckCoverage(program, env=(), bindings=None, fold=FoldCarried(), supplied=())` | `Coverage` — the gaps, the handlers that could not be read and the places not followed |

`effect_analysis(result_cache=True)` keeps a `CheckCoverage` answer of a Program
function read there on disk, reused while every module file it depends on has
the same content. A target that cannot be analyzed is thrown back where the
effect was performed. `SuppliedDeclaration` (in `CheckCoverage.supplied`) tells
the analyzer what a handler it cannot read answers and performs.

How it reads code (`python/doeff_effect_analyzer/_program_effects.py`,
`_handler_effects.py`):

- **Hy is macro-expanded first** with Hy's own compiler, so `defk`, `<-`,
  `defhandler` and project macros that wrap them (a `defservice` that expands to
  `defk`) are read as the Python they become.
- **Names resolve by importing** the module that defines the Program. An effect
  is any class that subclasses `doeff.EffectBase` — including the project's
  own — however it was imported (aliases, re-exports). No Program is run and no
  handler is installed.
- A Program's effects: `yield E(...)` / `yield from E(...)`; calls to other
  Program functions (`yield f(...)`, `yield from f(...)`, a factory that
  `return`s one) are followed, with the call chain kept in `via`.
- **Handlers a Program installs itself are subtracted.** `with_handlers([h …],
  body)` (`with-handlers`), `h1(h2(body))` for handler values (what `with-handler
  [h1 h2] body` expands to) and `WithHandler(dispatch, body)` (what `handle`
  expands to) make a *handled* scope (`ProgramEffects.handled`): the body's
  effects run through those handlers from the innermost out, a handled effect is
  replaced by what its clause performs, and only the residual leaves.
  `effect_types` is that residual; `CheckCoverage` runs it on through an env.
- **Parameters are read with what the caller passes.** `AnalyzeProgram(f,
  bindings={"foundation": production_handlers})` (or `--bind`, or a
  `functools.partial` target) binds a parameter, and a call `(job
  production-handlers)` found in a body binds `job`'s parameter the same way. A
  foundation used as `(<- base list (foundation))` then `(with-handlers base …)`,
  as `(foundation (body))` with a handler value, or as a function that returns
  `(with-handlers [...] program)` is folded in. Unbound, it stays `unresolved`.
- Programs handed to an effect or helper (`Spawn(child(...))`,
  `RemoteJob(task(...))`, `remote_job(task(...))`) are reported as `carried`
  with their own effect sets. Whether they run under the same handlers is the
  carrier's meaning (`Spawn`: yes; `RemoteJob`: elsewhere), so the caller folds
  them in explicitly (`CheckCoverage(..., fold=FoldCarried(declared=True))`,
  `effect_types_with(FoldCarried(names=("Spawn",)))`, `--fold-carried Spawn`). A Program argument the callee installs handlers around itself is read
  there, not carried.
- A handler clause is a branch on `isinstance(effect, T)` (or `match effect:
  case T(...)`) inside a function or lambda that dispatches on one of its
  parameters — the shape `defhandler` expands to (a lambda when the only clause
  is `(resume v)`) and hand-written handlers use. The effects a clause performs
  (`(<- (Await ...))` in a clock handler) must be handled by an outer handler;
  `CheckCoverage` walks the env from the innermost handler out. A clause's body
  is read the first time its `emits` is asked for — `CheckCoverage` asks only
  the clauses an effect reaches.
- Factories returning `doeff.handler(dispatch)`, `functools.partial(dispatch,
  ...)`, a method of an object they build (`handler(runtime.handle)`, or an
  attribute `__init__` sets to one — doeff-time's clocks), dispatchers that
  `return dispatch(..., effect, k)`, and wrappers around a handler call are
  followed to the function holding the `isinstance` branches. A factory whose
  clauses cannot be read may declare them: `f.__doeff_handles__ = (E, …)` (what it
  answers) and `f.__doeff_effects__ = (…)` (what its clauses perform; both are
  needed). `HandlerEffects.basis` says which decided: `clauses`, `declared` or
  `unread`; a declaration that disagrees with the clauses read is reported. A
  body wrapper that answers no effect but performs some itself around the body
  (reads a start position, starts tasks, then runs the body — doeff-records'
  `records-signal-handler`) declares `f.__doeff_handles__ = ()`: its
  `__doeff_effects__` become `HandlerEffects.performs` and go to the handlers
  outside it.
- An env builder is a function — plain, `defk` or `deff` — returning a handler
  list, outermost first: a list literal, reached through the names it was bound
  to (`_contract_result`, `val`, `(<- base list (other-builder))`), with `*base`
  spreads, `a + b`, and calls to other builders.
- What cannot be followed is reported, never dropped: `unresolved` entries in a
  Program report (e.g. a yielded parameter, an unbound foundation), and handlers
  whose clauses cannot be read — in the env or installed inside the Program —
  handle nothing and are named in `unknown_handlers`. Either makes
  `Coverage.complete` false (`Coverage.unresolved`, `Coverage.unknown_handlers`).
- A function the Program calls with a Program argument is read with that argument
  bound: if it runs the Program itself (`(with-handlers [...] program)`), the
  Program is read inside it; if it does not visibly run it but reads as a handler
  (`scheduled`), the call is a handled scope under that handler; otherwise the
  Program is carried by the function.

Tests: `tests/python/` (pytest; puts `python/` on the path when the package is
not installed).

## The Rust core (`seda`, `doeff_effect_analyzer._native`)

The crate in `src/` is the earlier engine: `seda analyze` / `seda hy` and the
`analyze` / `analyze_symbol` functions of the `_native` extension (inside the
package — not shown from its root). It recognises effects by a fixed vocabulary of doeff constructor names
(`Ask(`, `Tell(`, `Get(`, `Put(`, `slog(`; `src/effect_registry.rs`) and reads Hy with its own reader, so it does not see project-defined
effect classes or code produced by user macros. Use the Python front end above
(the effects) for effect sets and coverage.

## Building

```
uv sync
```

The build goes through `tools/doeff_cargo_backend.py`, the one entry for Rust builds (ADR-DOE-BUILD-001): it reuses
the wheel stored under the hash of the Rust sources and builds only when they changed. For incremental Rust builds
while editing the crate, give it a target dir of your own: `CARGO_TARGET_DIR=<dir> uv sync`.

Mixed layout: `python-source = "python"`, extension module
`doeff_effect_analyzer._native`.
