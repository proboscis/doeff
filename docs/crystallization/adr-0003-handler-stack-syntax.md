# ADR 0003 — Hy handler stack syntax after public WithHandler removal

Status: Proposed until the macro, semgrep guard, and downstream migration land
together.

Builds on:
- `core-withhandler-compat-shim-delete`
- `defhandler` producing `Program -> Program` handler functions
- public API rule `doeff-no-public-withhandler-shim`

Related rule:

Handler installation in user-facing Hy code is expressed as an explicit handler
stack:

```hy
(with-handler [outer inner] body)
```

The public `doeff.WithHandler` constructor remains deleted. Low-level
`doeff_vm.WithHandler` is an implementation detail for macros and VM tests.

## Context

1. `defhandler` already returns a `Program -> Program` function. This is the
   semantic truth: a handler takes a Program and returns the same Program under
   a handler scope.
2. Direct `(handler body)` calls are semantically correct but visually hide the
   scope boundary. Effect handlers behave more like `try` scopes than ordinary
   data transforms because they intercept effects and own continuations.
3. A single-argument `(with-handler h body)` form would be only a rename of the
   deleted `WithHandler` public shim. The new syntax must encode handler-stack
   order, not just constructor spelling.
4. Koka/Eff-style syntax makes handler scope visible. Hy can approximate that
   with a macro while keeping the runtime model as `Program -> Program`.
5. Some downstream code builds handler stacks at runtime. A Hy macro cannot
   expand a runtime list, so dynamic composition needs a small function API in
   addition to the static syntax.

## Decision

R1. Add canonical Hy syntax:

```hy
(with-handler [h1 h2 h3] body)
```

It expands to:

```hy
(h1 (h2 (h3 body)))
```

The list order is scope order: the leftmost handler is outermost, the rightmost
handler is innermost.

R2. Require a literal non-empty vector. Empty handler stacks are rejected at
macro expansion time because they make a scope form with no scope owner.

R3. Keep `defhandler` first-class. `defhandler` creates reusable handler values
and factories. `with-handler` is only the user-facing syntax for installing
those values over a Program.

R4. Keep `handle` for inline clauses. `handle` remains useful when the effect
clauses belong at the call site instead of in a reusable handler definition.

R5. Static enforcement continues to ban public `doeff.WithHandler` imports and
qualified calls. The rule message points users to `with-handler` in Hy and a
typed Program-stack helper in Python.

R6. Add `doeff.with_handlers(handlers, program)` for runtime-computed stacks.
It uses the same scope order as the macro: the first handler is outermost and
the last handler is innermost. It accepts both `handler(...)`/`defhandler`
Program -> Program functions and raw effect dispatchers, normalizing raw
dispatchers through `doeff.handler`. Empty runtime lists are accepted as
identity because a dynamic stack can legitimately be absent; this does not
change R2 for the reader-facing Hy scope form.

## Consequences

- Hy code gains a readable handler-stack declaration without reintroducing the
  public VM constructor.
- Existing `WithHandler` nests migrate mechanically into a single
  `with-handler` stack.
- Python code cannot use the Hy macro and should use
  `with_handlers([h1, h2], program)`.
- Hy code with a runtime-computed handler list should call `(with-handlers hs
  program)` rather than pretending a macro can consume runtime data.
- Direct `(handler program)` remains the underlying semantic model, but it is no
  longer the preferred reader-facing Hy idiom.

## Invariant TDD and Enforcement

| Invariant | Counterexample that must fail first | Owning layer | Red test | Green mechanism | Static guard | Residual status |
| --- | --- | --- | --- | --- | --- | --- |
| Handler stack order is left-to-right scope order | `(with-handler [outer inner] body)` behaving as `inner(outer(body))` | `doeff-hy` macro | `packages/doeff-hy/tests/test_with_handler_macro.py::test_with_handler_macro_applies_stack_left_to_right` | Macro folds handlers from right to left | N/A | Implemented in this change |
| Runtime handler stacks use the same order as the Hy syntax | `with_handlers([outer, inner], body)` behaving as `inner(outer(body))` | `doeff.program` | `tests/test_with_handlers_helper.py::test_with_handlers_applies_stack_left_to_right` | Helper folds handlers from right to left | Public `WithHandler` ban points Python callers here | Implemented in this change |
| Empty dynamic stacks are identity, not syntax | `with_handlers([], body)` raising or fabricating a handler scope | `doeff.program` | `tests/test_with_handlers_helper.py::test_with_handlers_accepts_empty_runtime_stack_as_identity` | Helper returns the input Program unchanged | N/A | Implemented in this change |
| Runtime helper preserves legacy raw-dispatcher behavior without public `WithHandler` | `with_handlers([raw_dispatcher], body)` failing until caller manually wraps it | `doeff.program` | `tests/test_with_handlers_helper.py::test_with_handlers_normalizes_raw_dispatchers` | Helper normalizes unmarked callables through `handler(...)` | Public `WithHandler` ban pushes callers to helper | Implemented in this change |
| Empty handler stacks are not valid scopes | `(with-handler [] body)` silently returns `body` | `doeff-hy` macro | `packages/doeff-hy/tests/test_with_handler_macro.py::test_with_handler_macro_rejects_empty_stack` | Macro raises `SyntaxError` | N/A | Implemented in this change |
| Public `doeff.WithHandler` does not return as a user API | Hy imports `WithHandler` from `doeff` | semgrep | `tests/semgrep/test_vm_failfast_semgrep_rules.py::test_public_withhandler_rule_detects_legacy_hy_import_and_calls` | Rule covers `doeff/**/*.hy` public imports | `doeff-no-public-withhandler-shim` | Implemented in this change |

## Out of scope

- Banning every direct `(handler body)` call statically. Hy cannot reliably
  distinguish an arbitrary function call from a handler application without
  type information.
- Removing `doeff_vm.WithHandler`. The VM node is still the correct internal
  representation.
