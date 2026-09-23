# doeff-hy

Standard Hy macros for doeff effect composition.

## File Extensions

| Extension | Purpose | Allowed macros |
|-----------|---------|----------------|
| `.hy`  | General Hy source (interpreters, effects, utilities) | All |
| `.hyk` | Kleisli modules (reusable logic) | defk, deff, defhandler, deftest |
| `.hyp` | Program modules (entrypoints) | defp, defpp, deftest |

Extensions are registered automatically when `doeff_hy` is imported.

## Usage

```hy
;; In .hyk files (kleisli logic):
(require doeff-hy.macros [defk deff deftest <- !])
(import doeff [do :as _doeff-do])

(defk fetch-data [url]
  {:pre [(: url str)] :post [(: % dict)]}
  (<- response (http-get url))
  response)

;; In .hyp files (entrypoints):
(require doeff-hy.macros [defp <-])
(import doeff [do :as _doeff-do])

(defp p-main
  {:post [(: % dict)]}
  (<- data (fetch-data "https://example.com"))
  (process data))
```

## Macros

| Macro | Purpose |
|-------|---------|
| `defk` | Define kleisli function (`@do` + `:pre`/`:post` contracts + bang) |
| `deff` | Define pure function with `:pre`/`:post` contracts |
| `defp` | Define Program constant (`:post` required) |
| `defpp` | Define Program[Program[T]] constant |
| `deftest` | Define effectful test (expands to pytest function) |
| `do!` | Monadic do block — inline effect sequencing |
| `<-` | Perform effect, bind result — `(<- name Type effect)` also asserts `isinstance` |
| `!` | Inline effect bind in argument position |
| `defpipeline` | Named-stage pipeline composition |
| `traverse` | Applicative traverse over collections |
| `for/do` | SQL-like comprehension with effects |

### Typed binding

`(<- name Type effect)` expands to the bind plus `(assert (isinstance name Type) …)`.
The expansion has one definition point (`_bind-yield` in `macros.hy`) shared by the
`<-` macro and every body expander that pre-parses `<-` (`do!` / `defp` / `deftest` /
`for/do` / `traverse` / `defhandler` clauses), so the type contract holds at runtime
wherever the bind is written. The 2- and 3-element forms add no check.

`None` is a type in these positions, as in a Python annotation: `(: % None)`,
`(<- x None effect)` and `#(int None)` check against `type(None)` at runtime
(`_runtime-type` in `macros.hy` is the one place that maps it). `(| int None)`
needs no mapping.

### Static type checking

`doeff-hy-check PATH... --root <repo>` expands Hy sources with the real macros and runs
pyright on the result, reporting errors at `.hy` lines. Contract types (`(: x T)`) become
parameter/result annotations, so argument types, missing arguments, result types, a
forgotten `<-`, and effect constructor arguments are checked. What it does not catch yet
(effect result types, env/effect sets) and why: `docs/static-check.md`.

### Bang evaluation position

`(! effect)` is replaced with a `yield` expression at the position where it is
written. Branches, `and`/`or` short-circuiting, argument order, `try` suites,
and literal containers therefore retain normal Python evaluation semantics.

Python-native `lfor`/`gfor`/`sfor`/`dfor` comprehensions and plain nested
`fn`/`defn` bodies cannot host a doeff bang safely. Macro expansion raises an
`ADR-DOE-HY-003` `SyntaxError` with a rewrite: use `for/do` plus `<-` for
effectful iteration, and `fnk` or `defk` for an effectful function.

## Testing with deftest

`deftest` generates pytest-compatible test functions. Tests use `<-` for effect binding and `assert` for validation.

```hy
;; Basic test (in .hyk or .hyp file):
(require doeff-hy.macros [deftest <-])
(import doeff [do :as _doeff-do])

(deftest test-fetch-returns-data
  (<- data (fetch-data "https://example.com"))
  (assert (> (len data) 0))
  (assert (in "status" data)))
```

### Interpreter parametrize

Tests run under doeff interpreters provided by pytest fixtures. Specify which interpreters via `:interpreters`:

```hy
(deftest test-signal-deterministic
  {:interpreters ["cllm_test" "cllm_sim"]}
  (<- plan (compute-signal "2026-04-01"))
  (assert (> (len plan.orders) 0)))
```

Interpreter keys are resolved in `conftest.py`:

```python
# conftest.py
import pytest

INTERPRETERS = {
    "cllm_test": cllm_test_interpreter,
    "cllm_sim": cllm_sim_interpreter,
}

@pytest.fixture
def doeff_interpreter(doeff_interpreter_name):
    return INTERPRETERS[doeff_interpreter_name]
```

### Env overrides

Per-test environment overrides (merged with default env from conftest):

```hy
(deftest test-with-sim-time
  {:interpreters ["cllm_sim"]
   :env {"nakagawa.sim_start_time" "2026-04-10T06:00:00+09:00"}}
  (<- result (my-pipeline))
  (assert result))
```

### Fixture parameters

```hy
(deftest test-signal-dates [trade-date]
  {:params {"trade-date" ["2026-04-01" "2026-03-15"]}
   :interpreters ["cllm_test"]}
  (<- plan (compute-signal trade-date))
  (assert (> (len plan.orders) 0)))
```

### Marks and conditional skip

```hy
;; Static marks for pytest -m filtering
(deftest test-kabu-prices
  {:marks ["e2e" "slow"]}
  (<- result (fetch-prices))
  (assert result))

;; Dynamic skip — condition evaluated at import time
(deftest test-kabu-prices
  {:skip-if (not (can-reach "plutus" 18082))
   :skip-reason "kabuStation unreachable"}
  (<- result (fetch-prices))
  (assert result))
```

Run with mark filtering:

```bash
pytest -m "not e2e"       # skip e2e tests
pytest -m "slow"          # only slow tests
pytest -m "cllm and not slow"
```

### conftest.py for Hy test collection

To collect deftest from `.hy`/`.hyk`/`.hyp` files, add a pytest plugin:

```python
# conftest.py
import doeff_hy  # registers .hyk/.hyp extensions
import hy        # enables Hy imports
```

pytest will discover `test_*` functions generated by `deftest` in Hy modules when they are imported via conftest or `pytest_collect_file`.
