# doeff-hy-check — static type checking for Hy code

## Purpose

Hy source is invisible to pyright, so no static type checking applied to doeff + Hy code
(measured on 2026-09-23 on the agora-controllers experiment branch `wt/doeff-worker-pod`: of the deliberately injected mistakes,
the Python DI version caught 7 of 7 with pyright, the typed Python doeff version caught 10 of 10,
and the Hy version caught 0 statically). `doeff-hy-check` expands Hy source with the real macros, feeds the result to pyright,
and reports the errors at `.hy` lines.

## Usage

```sh
doeff-hy-check [PATH ...] --root <repo root> [--import-root clients/hy] [--json]
# example (agora-controllers)
PYTHONPATH=.:clients/hy doeff-hy-check --root . --import-root clients/hy controllers/worker
```

- `PATH` is a `.hy` file or a directory. Errors appear in the form `path:line:col - error: message (rule)`.
- Exit 1 if there are errors, 0 if none, 2 if the tool could not run.
- The pyright configuration is the root's `pyrightconfig.json` (or `[tool.pyright]` in `pyproject.toml` if absent).
  Only `reportDeprecated` is raised to error (for "Program in statement position" below).
- Imports are resolved with the `sys.path` of the Python that runs (`--python`; default is the Python running the tool).
  For `.hy` dependencies, the tool expands the `.hy` files under the root that the checked file imports.

## Carry (`--carry <file>`) — do not re-check `.hy` files whose inputs are unchanged

A shortcut for users who check a whole repo every time (the daily type stage of agora-controllers, hy-type-trend — about 3,000 `.hy` files in range).
The file named by `--carry` (the carry document) keeps, per file, an "input digest" and the pyright diagnostics from that run. On the next run,
`.hy` files whose inputs equal the previous run's are not passed to pyright; the previous diagnostics are carried over. The carried answer must equal
the answer from checking everything
(test = `tests/test_static_check_carry.py` — with real pyright, it compares against a full check for runs with a body-only change, a change to the signature of a used name, a change to a transitive type, and a change that makes an unresolved
import resolvable).

The inputs (`InputGraph.inputs_of` in `doeff_hy/_static_carry.py`) are four:

1. The file itself — the `.hy` source, the expanded Python, and the position map.
2. The declaration surface of the names the file uses, and the declarations of the names the surface uses (all transitively). Surface = the part
   pyright reads to decide the name's type on the user's side — for a fully typed function, only the decorator and signature (the body is just an "empty or not" marker); for a class, the whole thing (for typed
   methods that do not assign to self attributes, only the signature); for untyped functions, variables, and imports, the whole statement. Docstrings and line numbers are excluded. Module-level import statements are
   split per name. For `__all__`, only per-name presence (an `__all__` written in an unreadable form, and star imports, use the whole statement). Name declarations are read from expansions under the root and from `.py` / `.pyi` in the `--python` search path that lie outside the prefix (the editable doeff checkout) —
   so when the doeff pin is bumped, only the `.hy` files that use changed declarations are re-checked.
3. Where the modules the file imports resolve — among the candidate files that could match under the same search path as pyright (root, extraPaths, typings, the python search path, and the retreat
   from the importing file's directory), the list of those that exist (a new file that changes an import's destination changes the input), plus py.typed.
4. The environment fingerprint — the carry format version, pyright version, pyright configuration, python version and search path, the names of the contents of directories under the prefix (site-packages, stdlib)
   (dist-info names = distribution versions) and the contents of `.pth` files, and the source of the functions that produce diagnostics and map them back to `.hy` positions. If any of these changes, everything is re-checked.

Paths inside inputs are spelled with the root replaced by the marker `@root` and the root's parent by `@parent` — the daily run puts the root under a per-task working directory and places the checkout of the doeff
pin next to the root (so paths differ per run); thus measuring the same tree at a different location yields the same inputs.

Where it errs on the safe side: files that use a module unreadable due to a syntax error are always checked. A reference that uses a module as a value, and star imports, depend on all of the module's
names. If the carry file is unreadable, it says so and checks everything (the measurement is not halted).

Review (`--carry-refresh-runs N`, default 12): among files with unchanged inputs, the ceil(count / N) files whose last check is oldest are re-checked every run, and the files whose answer differs from the carried
diagnostics are counted and reported (`summary.refresh_mismatches` in the carry document) — a dependency the input definition overlooks shows up here.

The carry document's `summary` fields (`run`, `checked`, `carried`, `reasons`, `refresh_mismatches`, `mismatched`) are for users to read;
the other fields belong to the tool.

## How it works

1. Each `.hy` is expanded with the Hy compiler. doeff-hy macros emit the "expansion for type checking" only at this time
   (switch = `doeff_hy/_static_view.py`). Source imports are not executed (only a macro `require` imports the macro's module,
   as is usual in Hy compilation).
2. In a temporary tree that mirrors the root's contents via symlinks, the expanded Python is placed as `<name>.py` and pyright is run.
   Files in the user's repo are not modified.
3. Pyright diagnostic positions are mapped back from the expanded Python node to the Hy expression position. An expression synthesized by a macro
   carries the position of the user's expression that wraps it (`doeff_hy/_positions.hy`, the same mechanism as the traceback fix).

The shapes a macro expansion takes for types:

| Expansion | Runtime expansion | Expansion for type checking |
|---|---|---|
| `defk` / `deff` parameters | `def f(x: 'int')` (maps the `:pre` types to string annotations) | same |
| `deff` return value | `-> 'T'` | same |
| `defk` return value | no annotation (generator) | `-> 'T'` (maps `(: % T)` of `:post` — a defk in this expansion is a plain function without yield. Even a defk that calls itself lets pyright read the answer type without relying on inference. Not attached to a defk that writes yield directly in its body, nor to a `:post` whose type is only a description string) |
| `:tp [T]` (type parameters) in a `defk` / `deff` contract | emits no type parameters (the PEP 695 spelling does not appear in runtime code). In the contract's isinstance, T is erased to `object` — `(of Program T object)` becomes `Program`, and a bare `(: % T)` is always true (same direction as the rule #1790 that checks only the outer type; the failure message keeps the type as written) | `def f[T](body: 'Program[T, object]') -> 'T'` (generic for a function whose answer is determined by the argument type — e.g., a wrapper returning the body's answer. Also listed in the .pyi of the tool `doeff_hy._static_stub`, so the answer does not become Incomplete). `defp` rejects `:tp` |
| `:tp [T]` (type parameters) of `defeffect` | emits no type parameters (the class's `__type_params__` is empty — T is read from the module's `(val T (TypeVar "T"))`) | `class E[T](_doeff_effect_base):` (PEP 695 — the field `(: program (get Program #(T object)))` and `:answer (\| T …)` read T. The .pyi of the tool `doeff_hy._static_stub` is `class E[T](_doeff_effect_base[T \| …])`, so the program's answer T flows into the user's `<-` answer. Without `:tp`, the check of the defining module flags the field's T as "has no meaning in this context") |
| module-level `(val #^ type name expr)` / `(var #^ type name expr)` (type annotation) | `name: type = expr` (write it for values whose type is not determined by the expression — an empty dict, the result of `re.compile`. The annotation is evaluated at module level) | same. The .pyi gets `name: type`, so the constant does not become Incomplete. `lazy` and `session` do not accept annotations |
| body result (`defk` / `deff`) | At the top of the function (right after the `:pre` check), `_contract_result: 'T'` is declared without a value; at exits (an early `(return x)` and the last expression) it is assigned as `(_contract_result,) = (value,)`, a one-element tuple without annotation (local variable annotations are not evaluated at runtime. Every exit value is checked against the declared T, and even when the body ends with `while True` and never reaches the last assignment, early exits are read as T. The tuple assignment is used so that Hy's relocation of the temporary names for match / if values to the assignment target avoids the `= None` that match places before the case being checked against T — CPython compiles this assignment to the same bytecode as a plain assignment) | same |
| body result (`do!` / `defp`) | `_contract_result: 'T' = last expression` (the exit is the single last expression) | same |
| `(<- x T e)` | `x = yield e` + isinstance check | `x: 'T' = _doeff_perform(e)` (same shape as Python's `@effectful` `x = perform(e)`; emits no yield — docs/24-effectful-perform.md) |
| `(! e)` | `yield e` (goes through open-bind) | `_doeff_perform(e)` (same as `<-` — the yield value of an unannotated generator is Unknown) |
| result of calling a defk | `doeff.do.do` | the `do` of `doeff_hy/_static_types.pyi`: the same type (core's `Expand[T, E]`) plus an overload for functions without yield |
| `(resume v)` / `(transfer v)` in `defhandler` | `_doeff_Resume(k, v)` / `_doeff_Transfer(k, v)` | core's `_doeff_typed_resume(effect, k, v)` / `_doeff_typed_transfer` (checks v against the effect's answer type). Both are `_doeff_` aliases that do not shadow the writer's own same-named effects (`doeff_hy/_expansion_names.py` is the single source of truth for spelling and import) |
| the function that runs the clauses of `defhandler` / `handle` | `(effect: 'union of clause types', k)` (annotation for VM narrowing) | `(effect: object, k: _doeff_Continuation) -> _doeff_ClauseRun` (the clause's `isinstance` narrows the type). Imports are only `_doeff_Pass` and `_doeff_WithHandlerType` |
| the function that receives the body in `defhandler` | `def __doeff_handler_fn__(__doeff_body__):` | `(__doeff_body__: _doeff_HandlerBody[_doeff_HandledAnswer]) -> _doeff_HandledScope[_doeff_HandledAnswer]` (carries the body's answer type as is — types = `static_types.pyi`; name table = `HANDLER_STATIC_NAMES` in `static_view.py`) |
| `session val` / `session var` in `defhandler` | binds the name to the answer of `(yield (_doeff_Get key))` (the content of `_doeff_Some`) or to the initial value (what is put into the state is the initial value) | binds the name to the initial value (the type is the type of the initial expression — the answer of Get has no type). The key name is kept because `Put` of `:=` looks it up. `Put` / `Some` of `:=` and the old `set!`, and `session_key` of the initial key, are referenced as `_doeff_Put`, `_doeff_Some`, `_doeff_session_key`, taken from the helper imports at the top of the module (`SESSION_STATIC_IMPORTS` in `static_view.py` — placed only when read). Emitting imports per handler caused an unused-import error in modules whose handlers have no `:=` (the re-export spelling `Some :as Some` did not work because Hy drops an alias with the same name). The types of `doeff_hy._session` are in `session.pyi` |
| `:answer` of `defeffect` | in the class body, `__doeff_answer__: ClassVar[object] = A \| B` (the answer type expression itself) | `__doeff_answer__: ClassVar[object] = cast(object, (A, B))` (a tuple of the union's members — avoids the error pyright raises when it evaluates `A \| B` in value position as a type, and only reads the names. Putting it in the bookkeeping setattr made it vanish from type checking, and imports used only for the answer type became reportUnusedImport. The type of the `<-` binding is unchanged) |
| check of how clauses end | imports from `doeff_hy._clause_endings` | imports the same names from the typed `static_types` at the top of the module (clause_endings is .hy, so its types are invisible) |
| expression in statement position | `_guard_statement_value(form, …)` | `_guard_statement_value(reveal_type(form), …)` |
| attribute on a function | `setattr(f, '__doeff_body__', …)` | same |
| function of `deftest` | `def test_x(doeff_interpreter, tmp_path):` + `return doeff_interpreter(…)` | `def test_x(doeff_interpreter: _doeff_DeftestInterpreter, tmp_path: _doeff_TmpPath) -> None:` (fixture name → type table = `DEFTEST_FIXTURE_TYPES` in `static_view.py`; types = `static_types.pyi`. Names not in the table copy the writer's `#^ T name`, or `object` if absent). The decorator pytest is the module-head `_doeff_pytest` |
| recording pytest items | `doeff_hy._pytest_items.record_at_import(globals(), …)` | removed as bookkeeping (`static_check.without_bookkeeping`) |
| imports of `_doeff_` aliases (macros defined by the owning package, such as `defsystem` of doeff-cluster) | per system, `import doeff_cluster._shared.intent.service_model as _doeff_cluster_service_model`, `import doeff_cluster._shared.entry.service_build as _doeff_cluster_service_build`, `import doeff_hy._declarations` | emits the same statements, and `static_check.without_bookkeeping` collapses the module-level duplicates to the first one (what it collapses = `import <module> as _doeff_<name>` where the bound name starts with the expansion-name prefix `_doeff_` (`expansion_names.PREFIX`), and imports of the root `doeff_hy` — doeff-hy does not hold the owning module's spelling). A module with two systems does not get a reportDuplicateImport error for an import the writer did not write. Imports the writer duplicated themselves are not collapsed and stay as errors |

## What it catches (measured 2026-09-23)

The same 10 kinds (in the same order as `pyright_mistakes_main.py` of typed doeff) + 2 kinds were written in Hy on top of the agora-controllers
sample (`summarize` in `controllers/worker/lab/turns.hy`, the effects `ReadShared` / `WriteShared` /
`SleepSeconds` / `RemoteJob`, `defservice`) and measured. The baseline file (with no mistakes) gives 0 findings.

| Mistake | Caught? | Error |
|---|---|---|
| 1 wrong argument type for a task `(summarize 1 [])` | caught | `reportArgumentType` (`conv` is `str`) |
| 2 missing argument for a task `(summarize conv)` | caught | `reportCallIssue` |
| 3 wrong return type (returns a dict with `:post [(: % int)]`) | caught | `reportAssignmentType` (at the line of the last expression) |
| 4a forgot `<-` (value is used) | caught | `reportArgumentType` where it is used |
| 4b forgot `<-` (statement only) | caught | `doeff-hy-unperformed` |
| 5 wrong argument type for an effect `(SleepSeconds "1")` | caught | `reportArgumentType` |
| 6 missing env handler (putting a writing service into a read-only env) | not caught | — |
| 7 write from a read-only job (sending a writing task to a read-only env) | not caught | — |
| 8 emitting an effect not in the declaration | cannot be written in Hy (no mechanism to declare the effect set) | — |
| 9 wrong handler answer type `(resume "yes")` | caught if the effect declares its answer type | `reportArgumentType` (the value of `typed_transfer`) |
| extra: wrong effect answer type `(<- rows int (ReadShared …))` | caught if the effect declares its answer type | `reportAssignmentType` |
| extra: wrong return type of a bound defk `(<- rows int (summarize …))` | caught | `reportAssignmentType` |

"If the answer type is declared" = writing the effect as `(defclass [(dataclass :frozen True)] ReadShared [(get EffectBase dict)] …)`
(doeff core's `EffectBase[T]`, docs/23-static-typing.md). The effects in the agora-controllers sample are plain
`EffectBase`, so as they are, 9 and the extra kind are not caught. Measuring a copy with the same fields where only the answer type was added,
both were caught. Summary: of the 10 kinds, 6 (1, 2, 3, 4a, 4b, 5) are caught with the sample as is, and
7 (+9) if the effect declares its answer type. Of the 2 extra kinds, both are caught if the answer type is declared.

Applied to the 7 kinds in the DI version's README (wrong task argument type, missing argument, wrong return type, forgotten `<-`, wrong clock argument type,
missing guard method, write from a read-only job), 5 of the 6 that can be written in Hy are caught (forgotten `<-` in both forms).

Regression test = `packages/doeff-hy/tests/test_static_check.py` (self-contained samples; 9 caught kinds and a 0-finding baseline).

## What it does not catch, and where to fix it

- **The env and the effect set** (6, 7, 8): `:env` of a service declaration (`doeff_cluster._shared.intent.service_model.service`) and `:env` of `RemoteJob` are import-path
  strings, and the set of effects a Program emits is also not declared in Hy. doeff core's `@do` reads the E of
  `Generator[E, Any, T]` as "the effects it may emit" (docs/23-static-typing.md), so the remaining work is
  to create a way to write E on the Hy side (for example, write the effect list in the defk contract, turn it into a generator
  annotation in the expansion for type checking, make `<-` a `yield from`, and add the called Program's E) and to express the env of
  agora-controllers' service declarations as types.
- **Effects that do not declare an answer type**: the `(<- x T (Eff …))` check and the handler answer check
  only work when the effect has the T of `EffectBase[T]`. The answer of an effect without a declaration passes as Any.

## `<-` on an effect with declarations, and the relay `Perform` (ADR-DOE-CORE-EFFECTS-003)

- The type of x in `(<- x e)`: the runtime `<-` turns an :absent answer into Absent and a :failure answer into Raise and emits it in the caller's scope, so
  they never reach the bound name. For an effect class whose defeffect declares :absent / :failure, the type-checking expansion gives
  `def __iter__(self) -> BoundRun[<the :answer union minus the :absent and :failure members>]` (`bound-answer-methods` in declarations.hy;
  the generated stub copies it too), and through the `_doeff_perform` overload (`BoundRun` in static_types.pyi), `Program[T, E]` reads T from `__iter__` (`_doeff_perform` stays a single declaration — making it an overload would change the diagnostic when the annotation does not match to reportCallIssue).
  The Answer of the effect base `EffectBase[Answer]` stays the whole declaration (handlers answer with values in all three cases — R4 — so `typed_resume` accepts all of them).
  A hand-written `.pyi` declares the same method (same shape as the generated stub).
- Relay (re-emitting an effect without opening the declaration): fix on one form, `(<- answer (Perform effect))` or `(! (Perform ask))`. `Perform[T]` is
  covariant in the answer and accepts a union of effects (the `ask` of `Perform(ask)` may be `A | B`), and `_doeff_perform` returns
  the whole declared answer T. The doeff-linter's relay judgment (the clause shape read by DOEFF143, 157, etc. — `clause_shape`) also reads `(Perform effect)` as
  the same re-emission as `(yield effect)`. A raw `(yield effect)` is typed only inside a defhandler clause (the answer is `object`), so do not use it.

## Rules for avoiding false positives

- Expressions in statement position (ADR-DOE-HY-001) are caught by the deprecated overload in `macros.pyi`. Pyright picks the deprecated overload even for values of
  unknown type (Unknown), so if `reveal_type` at the same position is
  Unknown / Any, the tool drops the error (a value of unknown type is not presumed to be an "unrun Program").
- `<-` on a value of type `object` / Unknown is not an error (the last overload of `_doeff_perform`).
- The "source not found" warning for modules whose implementation is `.hy` (such as `doeff_hy.macros`) is dropped, and
  the "unresolved import" of a `.hy` module without a stub becomes a note rather than an error (that module's types are invisible).
- When a macro copies the same user expression to two places, the same error appears twice, so errors with the same position and message are merged into one (`(<- x e)` becomes `_doeff_perform(e)`, where e appears only once).

Of the 49 files under `controllers/worker` in agora-controllers (33 + samples and tests), the 45 findings that remained were
type holes in user code (loose `#^ object` annotations, passing `str | None` as is, etc.), mismatches with the repo's old
`typings/acp_client` stub (19), and the shape of Hy's own compilation (`setv` inside a comprehension becomes
`nonlocal`; 2). There were 0 false positives caused by doeff-hy's expansion.

## [removed]

| Decision | Reason | How to revert |
|---|---|---|
| Type checking has pyright read "the Python expanded by the real macros" | keeps the definition point of macro semantics to one (copying macro semantics into a separate projector would create a second definition point) | delete `static_check.py` |
| Type-only shapes are emitted only while the switch (`static_view`) is on (the `<-` shape, the typed `do`, `typed_resume`) | does not change the runtime expansion, the bytecode cache, or execution cost | remove the `_static-view?` branch |
| Annotations of parameters and result variables are also attached in the runtime expansion (as strings) | reduces the divergence between the two expansions. Being strings, they are not evaluated at definition time and do not fail on types of names defined later | revert `_annotate-params` / `_result-binding` |
| Attributes on functions are attached with `setattr` | type checkers flag assignment to an unknown attribute on a function as an error. Runtime semantics are the same | revert to the original `(setv (. f attr) …)` |

## Binding findings (ADR-DOE-HY-006)

Separately from type diagnostics, findings that macros emit at expansion time are listed in the same output (collected by `collect_findings` in `doeff_hy/_static_view.py`;
for module level, the outermost sequence of the source is read by `binding_forms.module_findings`).

| Rule | Severity | What |
|---|---|---|
| `doeff-hy-setv` | warning | setv (which binds a name) in the bodies of defk, deftest, and defhandler clauses and at module level — use val or var |
| `doeff-hy-rebind` | error | rebinding the same name among old-style bindings (a for variable and mutually exclusive branches are not counted), setv of a module-level val's name, and a `(:= …)` at module level that has no effect |
| `doeff-hy-legacy-lazy` | warning | old lazy / lazy-val / lazy-var / set! in defhandler (migrate to session val / session var / :=) |

`doeff-hy-rebind` is the first stage of enforcing against rebinding; once it reaches 0 findings on each repo's mainline, it will switch to an expansion error (design record
`docs/design/defk-val-var-lazy/design.md` §8.3).
