# doeff-hy

Macros for writing [doeff](https://github.com/proboscis/doeff) programs, effects,
handlers and tests in [Hy](https://hylang.org/), a Lisp that compiles to Python.

**This README is for** developers who want to write doeff code in Hy, or who read Hy
code that uses doeff. After reading it you can install doeff-hy, define an effect, a
function that performs it and a handler for it, call them from Python, test them with
pytest, and look up each macro.

## What it does

doeff-hy gives you `defk`, `defeffect`, `defhandler`, `deftest` and related macros.
They expand to ordinary doeff code (`@do` generators, `EffectBase` dataclasses,
handler installers, pytest functions) with runtime contracts (`:pre` / `:post`) on
arguments and results.

Parts of [doeff-core-effects](https://github.com/proboscis/doeff/tree/main/packages/doeff-core-effects)
are written with these macros; the extra `hy` of doeff installs doeff-hy for them.

## Installation

```bash
pip install "doeff[hy]"   # installs doeff-hy
pip install doeff-hy      # the same set of packages
```

`pip install doeff` without the extra does not install doeff-hy.

Requires Python 3.11 or newer and Hy 1.3 (doeff-hy pins `hy>=1.3.0,<=1.3.1`, the
versions its tests cover).

## Quick start

`greeting.hy` defines an effect, a function that performs it, and a handler:

```hy file=greeting.hy
(require doeff-hy.macros [defeffect defk defhandler <-])

(defeffect Greeting
  "Ask which word to greet with."
  {:fields [(: lang str)]
   :answer str
   :tags {:context "example" :role "intent"}})

(defk greet [name]
  {:pre [(: name str)] :post [(: % str)]}
  (<- word (Greeting :lang "en"))
  (+ word ", " name))

(defhandler english
  (Greeting [lang]
    (resume "hello")))
```

`main.py` imports it from Python and runs it:

```python file=main.py
import doeff_hy  # noqa: F401  - lets Python import .hy / .hyk / .hyp modules
from doeff import run
from doeff_core_effects import scheduled

from greeting import english, greet

print(run(scheduled(english(greet("doeff")))))  # hello, doeff
```

- `(<- word effect)` performs the effect and binds its answer to `word`.
- `{:pre [(: name str)] :post [(: % str)]}` checks the argument and the result types
  at run time (`%` is the result).
- `english` is a handler installer: it wraps a program and answers every `Greeting`
  performed inside it with `(resume "hello")`.

## Testing with deftest

`deftest` defines a pytest test whose body performs effects. Put the tests in
`test_greeting.hy`:

```hy file=test_greeting.hy
(require doeff-hy.macros [deftest <-])
(import doeff_core_effects [Ask])
(import greeting [english greet])

(deftest test-greet
  (<- text (english (greet "doeff")))
  (assert (= text "hello, doeff")))

(deftest test-greet-from-env
  {:env {"name" "world"}}
  (<- name (Ask "name"))
  (<- text (english (greet name)))
  (assert (= text "hello, world")))
```

A `deftest` function asks pytest for a fixture named `doeff_interpreter` and calls it
with the test's program (and with `env=` when the test sets `:env`). You decide which
handlers the tests run under. pytest also needs to be told to collect `test_*.hy`
files. Both go in `conftest.py`:

```python file=conftest.py
import doeff_hy  # noqa: F401  - lets Python import .hy / .hyk / .hyp modules
import pytest
from doeff import run
from doeff_core_effects import reader
from doeff_core_effects import scheduled


@pytest.fixture
def doeff_interpreter():
    """How a deftest body runs: deftest calls this with the test's program."""

    def interpret(program, env=None):
        return run(scheduled(reader(env=env or {})(program)))

    return interpret


def pytest_collect_file(file_path, parent):
    """Collect deftest functions from test_*.hy files."""
    if file_path.suffix == ".hy" and file_path.name.startswith("test_"):
        return pytest.Module.from_parent(parent, path=file_path)
    return None
```

```bash run
pytest   # 2 passed
```

[doeff-hy-pytest](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy-pytest),
installed with doeff-hy, is a pytest plugin that pytest loads by itself. It fixes a
Hy problem on Python 3.14 and offers optional time limits for these tests.

### deftest options

The optional map after the test name (or after its fixture list) takes these keys:

| Key | What it does | Example |
| --- | --- | --- |
| `:interpreters` | Runs the test once per name; the names are passed to a `doeff_interpreter_name` fixture | `{:interpreters ["fast" "traced"]}` |
| `:env` | Passed to `doeff_interpreter` as `env=` | `{:env {"name" "world"}}` |
| `:params` | `pytest.mark.parametrize` for each fixture parameter | `{:params {"day" ["2026-01-01" "2026-02-01"]}}` |
| `:marks` | Adds pytest marks (`pytest -m "not slow"`) | `{:marks ["slow"]}` |
| `:skip-if` / `:skip-reason` | Skips when the expression is true; it is evaluated when the module is imported | `{:skip-if (not (shutil.which "git")) :skip-reason "git not installed"}` |

With `:interpreters`, resolve the name in `conftest.py`:

```python excerpt
INTERPRETERS = {
    "fast": fast_interpreter,
    "traced": traced_interpreter,
}


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name):
    return INTERPRETERS[doeff_interpreter_name]
```

Fixture parameters go in a list after the name:

```hy excerpt
(deftest test-report-for-day [day]
  {:params {"day" ["2026-01-01" "2026-02-01"]}}
  (<- report (daily-report day))
  (assert (> (len report.rows) 0)))
```

## File extensions

| Extension | Purpose | Allowed macros |
| --- | --- | --- |
| `.hy`  | General Hy source (handlers, effects, utilities) | All |
| `.hyk` | Kleisli modules (reusable effectful functions) | `defk`, `deff`, `defhandler`, `deftest` |
| `.hyp` | Program modules (entry points) | `defp`, `defpp`, `deftest` |

`import doeff_hy` registers `.hyk` and `.hyp` as Hy source extensions.

## Macros

Bring macros in with `(require doeff-hy.macros [<names>])` — `require`, never `import`. A macro that is
used without being required is compiled by Hy as a call to an undefined function (`NameError: name
'hyx_doXexclamation_markX' is not defined`); doeff-hy prints a `HyMacroUseWarning` with the line to add,
e.g. `(require doeff-hy.macros [do! <-])`, before the traceback. The same warning names the `require`
to write when a macro was brought in with `import`.

| Macro | Purpose |
| --- | --- |
| `defeffect` | Define an effect type: a frozen dataclass inheriting `EffectBase`, with its fields, answer type and tags |
| `defk` | Define an effectful function (`@do` + `:pre` / `:post` contracts) |
| `fnk` | Anonymous effectful function |
| `deff` | Define a pure function with `:pre` / `:post` contracts |
| `defhandler` | Define a handler installer from one clause per effect type |
| `defp` | Define a program constant (`:post` required) |
| `defpp` | Define a `Program[Program[T]]` constant |
| `deftest` | Define an effectful pytest test |
| `do!` | Inline do block: perform effects in sequence |
| `<-` | Perform an effect and bind its answer: `(<- name effect)`; `(<- name Type effect)` also checks `isinstance` |
| `!` | Perform an effect in argument position: `(f (! (Ask "x")))` |
| `on-raise` | Map matching `Raise` reasons to answers: `(on-raise body (Conflict d) answer …)` |
| `absent-as` | Fold `Absent` into a default: `(absent-as 0 body)` |
| `defpipeline` | Named-stage pipeline composition |
| `traverse` | Applicative traverse over collections |
| `for/do` | Comprehension whose body performs effects |

### Handlers

A `defhandler` clause names an effect type and its fields, and must end every path
with one of `resume`, `transfer`, `finish`, `reperform` or `raise`. `(finish value)`
ends the handler's scope without resuming; finishing an ordinary effect needs
`:finish-reason "…"`. Clauses for `Raise` and `Absent` never resume.

### Typed binding

`(<- name Type effect)` expands to the bind plus `(assert (isinstance name Type) …)`.
The expansion has one definition point (`_bind-yield` in `macros.hy`), shared by the
`<-` macro and by every body that accepts `<-` (`do!`, `defp`, `deftest`, `for/do`,
`traverse`, `defhandler` clauses), so the type contract holds wherever the bind is
written. The 2- and 3-element forms add no check.

`None` is a type in these positions, as in a Python annotation: `(: % None)`,
`(<- x None effect)` and `#(int None)` check against `type(None)` at run time
(`runtime_type_form` in `doeff_hy/type_forms.py` is the one place that maps it).
`(| int None)` needs no mapping.

### Absence and failure

Expected absence and failure are two effects in `doeff_core_effects._effects`:
`Absent(why)` (the Maybe side) and `Raise(reason)` (the Result side). Code writes only
the success path; the places that want to *do* something about absence or failure
fold them into values: `maybe` (`Absent` → `Nothing`) and `result` (`Raise` → `Err`)
from `doeff_core_effects._outcomes`, and the `on-raise` / `absent-as` macros. The
nesting order picks the shape: `(result (maybe b))` is `Ok(Some v)` / `Ok(Nothing)` /
`Err(e)`.

`defeffect` may split its answer by meaning — `:absent [Missing] :failure [Unreachable]
:value [Conflict]`; the rest is success. For such an effect `(<- x (ReadRow …))` binds
only the success and performs `Absent` / `Raise(answer)` in the caller's scope
instead; `:absent F` at the end of a bind turns an absence inside that bind into
`Raise(F)`. `<-` also opens `Ok` / `Err` / `Some` / `Nothing` values. An effect
without the declaration binds exactly as before (the bind yields the same object).

### Where `!` may appear

`(! effect)` is replaced with a `yield` expression at the position where it is
written. Branches, `and` / `or` short-circuiting, argument order, `try` suites, and
literal containers therefore keep normal Python evaluation order.

Python-native `lfor` / `gfor` / `sfor` / `dfor` comprehensions and plain nested `fn` /
`defn` bodies cannot host a doeff `!` safely. Macro expansion raises an
`ADR-DOE-HY-003` `SyntaxError` with a rewrite: use `for/do` plus `<-` for effectful
iteration, and `fnk` or `defk` for an effectful function.

### Names: val, var, lazy val, lazy var, session val, session var

In the bodies of `defk`, `deftest` and `defhandler` clauses, bind names with these
forms (`doeff-hy-check` warns on `setv` and asks for `val` or `var`):

| Form | Meaning |
| --- | --- |
| `(val x expr)` | Bind once. Binding the same name again is an error at expansion time |
| `(var x expr)` | Rebindable. Rebind with `(:= x new-value)` |
| `(lazy val x expr)` | Evaluated the first time `x` is used in that call, then remembered. Never evaluated if unused. If the first evaluation raises, the next use evaluates again |
| `(lazy var x expr)` | Like `lazy val`, and rebindable with `:=`. If rebound before first use, `expr` is never evaluated |
| `(session val x expr)` | Directly under `defhandler` only. Created once, on first use, and shared for the handler's session |
| `(session var x expr)` | Directly under `defhandler` only. Rebindable state that lives for the handler's session. Rebind with `(:= x v)` |

In `prices.hy`:

```hy file=prices.hy
(require doeff-hy.macros [defeffect defk defhandler <-])

(defeffect LoadPrices
  "Load the whole price table."
  {:answer dict
   :tags {:context "example" :role "intent"}})

(defk price-of [ticker]
  {:pre [(: ticker str)] :post [(: % float)]}
  (lazy val table (! (LoadPrices)))   ; performed only if `table` is used
  (var price 0.0)
  (when (in ticker table)
    (:= price (get table ticker)))
  price)

(defhandler price-table
  (session var loads 0)               ; counts loads for the whole session
  (LoadPrices []
    (:= loads (+ loads 1))
    (resume {"ACME" 12.5})))
```

Run from Python:

```python
import doeff_hy  # noqa: F401  - lets Python import .hy / .hyk / .hyp modules
from doeff import run
from doeff_core_effects import state
from doeff_core_effects import scheduled

from prices import price_of, price_table

print(run(scheduled(state()(price_table(price_of("ACME"))))))  # 12.5
```

- A value expression may be written `!(effect)`. Hy's reader reads `!(f)` as `!` and
  `(f)`; in declarations and `:=` both spellings mean `(! (f))`.
- `val` forbids rebinding the name only. Changing the value's contents
  (`(setv (get x k) v)`, `(.append x v)`) is allowed. `(<- x effect)` is another way
  to write `val`.
- A lazy name is referenced by its bare name. It cannot be referenced inside `fn`,
  comprehensions (`lfor` and friends) or `handle` / `defhandler` clauses (they cannot
  perform effects); take it out first with `(val v x)`.
- Session values are read and written through the state effects (`Get` / `Put`), so
  **install `state()` outside the handler**. The key is
  `doeff_hy.session.session_key(module, handler, name)` =
  `"<module __name__>/<handler name>/<variable name>"`. An outer handler that answers
  `Get` for this key replaces the value without running the initial expression, and
  one that receives `Put` observes the writes.
- At module top level, `(require doeff-hy.macros [val var lazy])` lets you write
  `(val x expr)`, `(var x expr)` and `(lazy val x expr)`. A top-level `lazy val` takes
  only expressions that perform no effects (a module has no handler; put resources
  built with effects in a `defhandler`'s `session val`). Top-level `(:= …)` does not
  go through a macro in Hy, so rebind a module `var` with a top-level `setv`.
- The older forms `(lazy …)`, `(lazy-val …)`, `(lazy-var …)` and `(set! …)` keep
  their behaviour in `defhandler` and emit a `DeprecationWarning` at expansion time
  pointing to `session val` / `session var` / `:=` (the keys are the same, so values
  carry over). In `defk` and `deftest` they are expansion-time errors.
- Rebinding the same name twice with the older forms (two `setv`s, for example) is a
  `doeff-hy-rebind` error from `doeff-hy-check`. Loop variables and mutually
  exclusive `if` branches are not counted.

## Static type checking

`doeff-hy-check PATH... --root <repo>` expands Hy sources with the real macros and runs
[pyright](https://github.com/microsoft/pyright) on the result, reporting errors at
`.hy` lines. It needs `pyright` on `PATH` (or `--pyright <command>`). Contract types
(`(: x T)`) become parameter and result annotations, so argument types, missing
arguments, result types, a forgotten `<-`, and effect constructor arguments are
checked. What it does not catch yet (effect result types, env / effect sets) and why:
[docs/static-check.md](https://github.com/proboscis/doeff/blob/main/packages/doeff-hy/docs/static-check.md).

## What installing doeff-hy changes in your environment

- **A startup hook.** doeff-hy installs `doeff_hy_bytecode_guard.pth`, which runs at
  every Python start in the environment. It wraps the standard source loader so that a
  Hy module is recompiled when a macro it used has changed (Python's `.pyc` check looks
  only at the source file, not at the macros that expanded it). `import doeff_hy`
  installs the same hook when `.pth` files are skipped (`python -S`). The same loader hook
  warns (`HyMacroUseWarning`) when a Hy module uses a doeff-hy macro it never required, or
  imports a macro instead of requiring it.
- **A compiled-code cache.** Compiled Hy code is also kept in a shared directory:
  `$DOEFF_HY_CODE_STORE`, by default `$XDG_CACHE_HOME/doeff-hy/code-store`
  (`~/.cache/doeff-hy/code-store`). Set `DOEFF_HY_CODE_STORE=off` to turn it off. When
  `DOEFF_HY_CODE_STORE` names the directory, the macro-expanded syntax tree of every module
  that gets compiled is kept there too (compressed, next to its code), so that
  doeff-effect-analyzer does not expand the same source again.
- **Partial Hy type stubs** (`hy-stubs`), so that pyright in strict mode accepts the
  `import hy` lines that Hy's expansion always emits.

## How it fits with the other doeff packages

doeff is published as five core packages. `pip install doeff` installs the first three; the extra
`hy` (`pip install "doeff[hy]"`) adds doeff-hy and doeff-hy-pytest.

| Package | Role |
| --- | --- |
| [doeff](https://github.com/proboscis/doeff) | The Python API: `@do`, handler installers, `run`, the `doeff` CLI |
| [doeff-vm](https://github.com/proboscis/doeff/tree/main/packages/doeff-vm) | The Rust VM that executes programs |
| [doeff-core-effects](https://github.com/proboscis/doeff/tree/main/packages/doeff-core-effects) | Reference implementations of effects and their handlers |
| **doeff-hy** | This package: macros for writing doeff code in Hy |
| [doeff-hy-pytest](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy-pytest) | pytest plugin for tests written with doeff-hy |

Arrows point from a package to a package it depends on (the dotted one only with the extra `hy`):

```text
doeff ──► doeff-vm
  │
  └─────► doeff-core-effects ┄┄► doeff-hy ──► doeff-hy-pytest
```

doeff-hy also depends on doeff and doeff-core-effects, so the five packages are
released together with matching versions.

## License

MIT. See [LICENSE](https://github.com/proboscis/doeff/blob/main/LICENSE).

Its own dependencies — Hy (with funcparserlib) and pydantic — are MIT-licensed.
Through doeff-core-effects it also installs frozendict, which is LGPL-3.0; see
[the doeff-core-effects README](https://github.com/proboscis/doeff/tree/main/packages/doeff-core-effects#licenses-of-dependencies).
