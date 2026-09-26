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

### val / var / lazy val / lazy var / session val / session var(ADR-DOE-HY-006)

defk・deftest・defhandler の節の本体では、名前の束縛を次の形で書きます(setv は doeff-hy-check が「val か var を使う」と警告します)。

| 形 | 意味 |
|---|---|
| `(val x 式)` | 一度だけ束縛する。同じ名前をもう一度束縛すると展開の時の誤り |
| `(var x 式)` | 書き換えられる。書き換えは `(:= x 新しい値)` |
| `(lazy val x 式)` | その呼び出しの中で x を初めて使った時にだけ評価して覚える。使わなければ評価しない。初回が例外なら次にもう一度評価する |
| `(lazy var x 式)` | lazy val と同じく初めて使った時に評価し、`:=` で書き換えられる。使う前に書き換えたら初期値の式は評価しない |
| `(session val x 式)` | defhandler の直下だけ。セッションの間、初めて使った時に 1 回だけ作って共有する |
| `(session var x 式)` | defhandler の直下だけ。セッションで持ち越す書き換えられる状態。書き換えは `(:= x v)` |

```hy
(defk quote-price [ticker]
  {:pre [(: ticker str)] :post [(: % float)]}
  (lazy val table !(LoadPriceTable))      ; 使った時にだけ効果を実行する
  (var price 0.0)
  (when (in ticker table)
    (:= price (get table ticker)))
  price)

(defhandler price-client
  (session val client (make-client (! (Ask "endpoint"))))
  (session var calls 0)
  (FetchPrice [ticker]
    (:= calls (+ calls 1))
    (resume (.fetch client ticker))))
```

- 値の式は `!(効果の式)` と書けます(Hy の reader は `!(f)` を `!` と `(f)` の 2 つに読むので、宣言と `:=` の中では `(! (f))` と同じに受けます)。
- val が禁じるのは名前の束縛し直しだけで、値の中身の書き換え(`(setv (get x k) v)`・`(.append x v)`)は対象外です。`(<- x 効果)` は val の別の書き方です。
- lazy の参照は裸の名前です。fn・内包表記(lfor など)・handle / defhandler の節の中では参照できない(効果を実行できない)ので、先に `(val v x)` で取り出します。
- session の値は状態の効果(Get / Put)を通して読み書きします。キーは `doeff_hy.session.session_key(module, handler, name)` =
  `"<module の __name__>/<handler の名>/<変数の名>"` で、外側の handler はこのキーの `Get` に値を答えれば初期化の式を走らせずに値を差し替え、
  `Put` を受ければ書き込みを観測できます。
- module の直下でも `(require doeff-hy.macros [val var lazy])` で `(val x 式)`・`(var x 式)`・`(lazy val x 式)` を書けます。
  module の直下の lazy val は効果を使わない式だけです(module には handler が無いので、効果で作る資源は defhandler の session val に置きます)。
  module の直下の `(:= …)` は Hy では macro を通らないので効かず、module の var は module の直下の setv で書き換えます。
- 旧い `(lazy …)` / `(lazy-val …)` / `(lazy-var …)` / `(set! …)` は defhandler では動きを変えず、展開の時の `DeprecationWarning` で
  `session val` / `session var` / `:=` への移行を案内します(キーは同じなので値は引き継がれます)。defk・deftest では展開の時の誤りです。
- 旧い書き方どうしの同じ名前の束縛し直し(setv を 2 回など)は doeff-hy-check の赤(`doeff-hy-rebind`)です。for の変数と、互いに排他な if の枝どうしは数えません。

設計の記録: `docs/design/defk-val-var-lazy/design.md`。

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
