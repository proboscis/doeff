"""Test that handle / defhandler written inside a function body does not create names local to the enclosing function.

The runtime expansion of handle and defhandler imports `_doeff_do`, `_doeff_Resume`, `_doeff_Transfer`, `_doeff_Pass`, `_doeff_WithHandlerType`, and
the clause-ending check at the place where it is written (node names are aliases that do not shadow an effect of the same name written by the author --
tests/test_handle_writer_names.py). If that is imported inside the body of a defk (a function), the name becomes local to the whole function, and the `_doeff_do` of a traverse / for/do
read earlier in the same function failed with UnboundLocalError (the same shape as the
bugs with fnk, do!, and nested defk).

handle puts the import, the check, and the handler assembly into an immediately-called function, and evaluates only the body (first argument) at the place where it is written -- the body
belongs to the outer do context (the `!` yield belongs to the outer generator). defhandler binds only the names outward with `_scoped-definition`, like defk.
At the module level, it is laid out as before.

For handle / defhandler written directly in a class body, the import became a class attribute and was not
visible from the clause functions. handle goes into an immediately-called function as inside a function, and defhandler moves the import into the function that assembles the handler
(the name definition stays at the class level).
"""

import builtins
import dis
import sys
import types
from collections.abc import Callable

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_core_effects import scheduled, state, try_handler
from doeff_traverse import fail_handler, sequential
from hy.compiler import hy_compile

from doeff import Program, run

# Names that the expansion of handle / defhandler imports at the place where it is written (`_do-import`, `_vm-imports`, `_clause-endings-import` in handle.hy,
# `_doeff-declare-passes` for the declaration that passes effects outside the clauses straight through, and `doeff_hy` in the defhandler declaration)
_HANDLE_IMPORT_NAMES = frozenset(
    {
        "_doeff_do",
        "_doeff_Resume",
        "_doeff_Transfer",
        "_doeff_Pass",
        "_doeff_WithHandlerType",
        "_doeff_check_clause_endings",
        "_doeff_check_clause_endings_once",
        "_doeff_declare_passes",
        "doeff_hy",
    }
)

# A defk exists at the module level (the ordinary shape of an author's module) -- the `_doeff_do` that the outer function reads first is in the module's globals
_HEADER = """
(require doeff-hy.macros [defk do! <- traverse for/do handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects._effects [Resumption])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defk bump-n [n] {:pre [(: n int)] :post [(: % int)]} (<- y (Bump n)) y)
"""


def _eval_module(code: str, header: str = _HEADER) -> types.ModuleType:
    """Read Hy source into a new module -- to keep each test's module globals separate."""
    mod = types.ModuleType("handle_scope")
    sys.modules["handle_scope"] = mod
    for form in hy.read_many(header + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run(program: Program) -> object:
    """Run a Program containing handle under the traverse / for/do handler."""
    return run(scheduled(try_handler(fail_handler(sequential(program)))))


_CASES = {
    # Shape of the request: handle comes after the read of traverse's `_doeff_do`
    "handle-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- r (handle (bump-n (sum coll)) (Bump [n] (resume (+ n 100)))))
          r)
        """,
        112,
    ),
    # The `!` in the body runs in the outer defk's do context (if the body goes into the immediately-called function, that function becomes a generator)
    "handle-body-bang-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- r (handle (bump-n (! (do! (len coll)))) (Bump [n] (resume (+ n 100)))))
          r)
        """,
        103,
    ),
    # handle in a branch that does not run is followed by traverse (the import makes the name local even if it does not run)
    "handle-in-untaken-branch-then-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (when (> (len items) 100) (handle (bump-n 1) (Bump [n] (resume n))))
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (sum coll))
        """,
        12,
    ),
    # The clause reads the let name, and the outer local name rebound after the handle, by the values at the time it ran
    "handle-clause-reads-outer-names": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (setv step 7)
          (let [base 1000]
            (setv prog (handle (bump-n (sum coll)) (Bump [n] (resume (+ n base step)))))
            (setv step 70)
            (<- r prog)
            r))
        """,
        1082,
    ),
    "nested-handle-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (<- r (handle (handle (bump-n (len coll)) (Bump [n] :when (> n 5) (resume n)))
                  (Bump [n] (resume (+ n 100)))))
          r)
        """,
        103,
    ),
    # handle comes after the read of `_doeff_do` of for/do in a plain defn
    "handle-in-defn-after-for-do": (
        """
        (defn make [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (handle (bump-n (len items)) (Bump [n] (resume (+ n 100)))))
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- r (make items))
          r)
        """,
        103,
    ),
    # Nested defhandler (the shape of the same `_scoped-definition` as defk)
    "defhandler-in-defk-after-traverse": (
        """
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- coll (traverse (<- x (Iterate items)) (* x 2)))
          (defhandler bump-h (Bump [n] (resume (+ n 100))))
          (<- r (bump-h (bump-n (sum coll))))
          r)
        """,
        112,
    ),
    "defhandler-with-params-in-defn-after-for-do": (
        """
        (defn make [items]
          (setv p (for/do (<- x (From items)) (* x 2)))
          (defhandler bump-h [base] (Bump [n] (resume (+ n base))))
          (bump-h 100))
        (defk subject [items]
          {:pre [(: items list)] :post [(: % int)]}
          (<- r ((make items) (bump-n (len items))))
          r)
        """,
        103,
    ),
}


@pytest.mark.parametrize(("source", "expected"), _CASES.values(), ids=_CASES.keys())
def test_handle_inside_function_runs(source: str, expected: object) -> None:
    assert _run(_eval_module(source).subject([1, 2, 3])) == expected


@pytest.mark.parametrize(("source", "expected"), _CASES.values(), ids=_CASES.keys())
def test_outer_function_binds_no_handle_import(source: str, expected: object) -> None:
    """The names that handle / defhandler import do not appear in the outer function's locals (or closure cells) -- if they did, the earlier read would be unbound."""
    mod = _eval_module(source)
    for outer in (mod.subject, getattr(mod, "make", mod.subject)):
        code = getattr(outer, "__doeff_generator_function__", outer).__code__
        assert _HANDLE_IMPORT_NAMES.isdisjoint(code.co_varnames + code.co_cellvars)


def _module_globals_view(mod: types.ModuleType) -> dict[str, tuple[int, int | None]]:
    """Per name in module globals, (id of the value, element count if a container) -- to compare whether calling the outer function added names or records to globals."""
    return {
        name: (id(value), len(value) if isinstance(value, list | dict | set | tuple) else None)
        for name, value in vars(mod).items()
    }


@pytest.mark.parametrize(("source", "expected"), _CASES.values(), ids=_CASES.keys())
def test_handle_inside_function_leaves_module_globals_unchanged(
    source: str, expected: object
) -> None:
    """handle / defhandler inside a function add no name or record to the module globals however many times the outer function is called -- the sequence of wrapped statements is
    evaluated on every call (the burden of the caller of `_scoped-definition`)."""
    mod = _eval_module(source)
    before = _module_globals_view(mod)
    assert _run(mod.subject([1, 2, 3])) == expected
    assert _run(mod.subject([1, 2, 3])) == expected
    assert _module_globals_view(mod) == before


_HOSTS = {
    "handle": ("(defn host [] (handle (bump-n 1) (Bump [n] (resume (+ n 1)))))", []),
    "defhandler": ("(defn host [] (defhandler h (Bump [n] (resume n))) h)", ["h"]),
    "defhandler-with-params": ("(defn host [] (defhandler h [b] (Bump [n] (resume b))) h)", ["h"]),
}


@pytest.mark.parametrize(("source", "names"), _HOSTS.values(), ids=_HOSTS.keys())
def test_host_function_gets_only_its_own_names(source: str, names: list[str]) -> None:
    """A function that contains only handle / defhandler has, besides the unique names Hy creates (prefix `_hy_` -- names of functions inside the expansion) and the defined names,
    no local names or closure cells (a test that does not depend on the list of imported names)."""
    code = _eval_module(source).host.__code__
    assert [
        name for name in code.co_varnames + code.co_cellvars if not name.startswith("_hy_")
    ] == names


_HALT = """
(defclass [(dataclass :frozen True)] Halt [EffectBase]
  (setv __doeff_resumption__ Resumption.NEVER))
(defn body-evaluated [] (raise (AssertionError "the body was evaluated before the check")))
"""

# The check's ClauseEndingError is a subclass of TypeError (clause_endings.hy is a .hy file so it cannot be looked up for type checking; it is caught by the base type and the wording)
_HALT_RESUMED = r"Halt is an effect that is never resumed \(Resumption\.NEVER\)"

# Shapes of the body: a plain expression, and the shape in which Hy lifts the statement part of the body into statements before the body expression (a statement before `do`, a let binding)
_BODIES = {
    "expression": "(body-evaluated)",
    "statement-before-the-program": "(do (body-evaluated) (bump-n 1))",
    "let-binding": "(let [x (body-evaluated)] (bump-n x))",
}


@pytest.mark.parametrize("body", _BODIES.values(), ids=_BODIES.keys())
def test_handle_checks_clause_endings_before_the_body(body: str) -> None:
    """handle inside a function also finishes the clause-ending check before the body -- if the check fails, the body is not evaluated. If the body were evaluated
    first, it would hit the AssertionError of `body-evaluated` (blind test A of the design verification -- when the function to wrap was built
    in the same expression as the body, the statement part of the body that Hy lifted forward ran before the check)."""
    mod = _eval_module(_HALT + f"(defn subject [] (abs 1) (handle {body} (Halt [] (resume 0))))")
    with pytest.raises(TypeError, match=_HALT_RESUMED):
        mod.subject()


def test_handle_checks_clause_endings_before_the_body_performs_an_effect() -> None:
    """The `!` in the body of a handle in a defk body does not emit an outer effect when the check fails -- if it did, there is no Bump handler, so
    it would be UnhandledEffect."""
    mod = _eval_module(
        _HALT
        + """
        (defk subject [] {:pre [] :post [(: % int)]}
          (<- r (handle (let [x (! (bump-n 5))] (bump-n x)) (Halt [] (resume 0))))
          r)
        """
    )
    with pytest.raises(TypeError, match=_HALT_RESUMED):
        _run(mod.subject())


def test_handle_checks_clause_endings_at_every_evaluation() -> None:
    """The type of a clause's effect is read from the closure on every evaluation, so the check runs each time the same handle is evaluated -- if the memory of a type that passed in an earlier evaluation
    skipped the check of a later evaluation, a handler that resumes an effect that is never resumed would be silently built (blind test B of the design
    verification -- a change that made the check once per place it is written)."""
    mod = _eval_module(
        _HALT
        + """
        (defclass [(dataclass :frozen True)] Ping [EffectBase])
        (defn answer-with [etype value program] (handle program (etype [] (resume value))))
        (defk uses-ping [] {:pre [] :post [(: % int)]} (<- x (Ping)) (+ x 1))
        """
    )
    assert run(mod.answer_with(mod.Ping, 41, mod.uses_ping())) == 42
    with pytest.raises(TypeError, match=_HALT_RESUMED):
        mod.answer_with(mod.Halt, 0, mod.uses_ping())


def test_module_and_class_level_definitions_are_not_wrapped() -> None:
    """The definitions of handle / defhandler at the module level and of defhandler in a class body are not wrapped -- wrapping would change `__qualname__` (the lookup of the
    pickle name). defhandler in a class body moves only the import into the function that assembles the handler."""
    mod = _eval_module(
        """
        (defhandler top-h [base] (Bump [n] (resume (+ n base))))
        (defclass Holder [] (defhandler inner-h [base] (Bump [n] (resume (+ n base)))))
        (setv program (handle (bump-n 1) (Bump [n] (resume (+ n 100)))))
        """
    )
    assert mod.top_h.__qualname__ == "top_h"
    assert mod.Holder.inner_h.__qualname__ == "Holder.inner_h"
    assert run(mod.top_h(5)(mod.bump_n(1))) == 6
    assert run(mod.Holder.inner_h(5)(mod.bump_n(1))) == 6
    assert run(mod.program) == 101
    # The import of handle at the module level is placed in the module globals as before. Check with a module that has only handle -- the import of defhandler in the same module
    # also places WithHandler, so in the module above it passed even when handle was wrapped (the failing case of
    # S3 in the design verification)
    handle_only = _eval_module("(setv program (handle (bump-n 1) (Bump [n] (resume (+ n 100)))))")
    assert run(handle_only.program) == 101
    assert "_doeff_WithHandlerType" in vars(handle_only)


# handle / defhandler written directly in a class body. The import in a class body becomes a class attribute, and the functions created inside the expansion
# (clause functions, the function that assembles the handler, the function that wraps the body) skip the class level when looking up names, so they cannot see it -- before the fix,
# Transfer, Resume, Pass in handle clauses and _doeff_do, _doeff_check_clause_endings_once in defhandler were NameError.
# If a defk, handle, or defhandler exists at the module level, its import places names in the module globals and hides the hole, so read it with a module of
# a single class (the body is the effect itself).
_CLASS_ONLY_HEADER = """
(require doeff-hy.handle [handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defclass [(dataclass :frozen True)] Scale [EffectBase] #^ int n)
"""

_CLASS_BODY_CASES = {
    # A trailing resume becomes Transfer (_tail-resume-to-transfer)
    "handle-transfer": (
        "(defclass C [] (setv program (handle (Bump 1) (Bump [n] (resume (+ n 100))))))",
        lambda mod: mod.C.program,
    ),
    # A resume inside try stays Resume
    "handle-resume": (
        """
        (defclass C []
          (setv program
            (handle (Bump 1) (Bump [n] (try (resume (+ n 100)) (except [ValueError] (transfer 0)))))))
        """,
        lambda mod: mod.C.program,
    ),
    # A clause whose :when does not match passes to the outer handler with Pass
    "handle-when-guard-pass": (
        """
        (defclass C []
          (setv program
            (handle (handle (Bump 1) (Bump [n] :when (> n 5) (resume n))) (Bump [n] (resume (+ n 100))))))
        """,
        lambda mod: mod.C.program,
    ),
    "defhandler": (
        "(defclass C [] (defhandler h (Bump [n] (resume (+ n 100)))))",
        lambda mod: mod.C.h(mod.Bump(1)),
    ),
    "defhandler-with-params": (
        "(defclass C [] (defhandler h [base] (Bump [n] (resume (+ n base)))))",
        lambda mod: mod.C.h(100)(mod.Bump(1)),
    ),
    # A session var clause reads Get, Put, Some and the session key
    "defhandler-session-var": (
        """
        (defclass C []
          (defhandler h (session var calls 99) (Bump [n] (:= calls (+ calls 1)) (resume (+ n calls)))))
        """,
        lambda mod: mod.C.h(mod.Bump(1)),
    ),
    # `<-` in a clause emits an effect to the outer handler (outer in the same class) and binds the answer
    "defhandler-bind": (
        """
        (defclass C []
          (defhandler h (Bump [n] (<- r (Scale n)) (resume (+ r 100))))
          (defhandler outer (Scale [n] (resume n))))
        """,
        lambda mod: mod.C.outer(mod.C.h(mod.Bump(1))),
    ),
}


def _run_with_state(program: Program) -> object:
    """Run the Program of handle / defhandler in a class body -- the state handler receives Get and Put of the session var."""
    return run(scheduled(state(initial={})(program)))


@pytest.mark.parametrize(
    ("source", "program"), _CLASS_BODY_CASES.values(), ids=_CLASS_BODY_CASES.keys()
)
def test_class_body_handle_clauses_read_their_operations(
    source: str, program: Callable[[types.ModuleType], Program]
) -> None:
    """The clauses of handle / defhandler directly in a class body run reading Transfer, Resume, Pass -- NameError before the fix."""
    mod = _eval_module(source, header=_CLASS_ONLY_HEADER)
    assert _run_with_state(program(mod)) == 101


# A shape where the author's module binds the same names as the handle expansion to something else (for example the Transfer effect of a bank transfer)
_MODULE_OWN_NAMES = """
(setv Transfer "author's Transfer" Resume "author's Resume" Pass "author's Pass")
"""


@pytest.mark.parametrize(
    ("source", "program"), _CLASS_BODY_CASES.values(), ids=_CLASS_BODY_CASES.keys()
)
def test_class_body_handle_leaves_module_globals_alone(
    source: str, program: Callable[[types.ModuleType], Program]
) -> None:
    """handle / defhandler in a class body do not place the names the clauses read in the module globals, and do not read from there -- they leave the author's module's
    same names (Transfer etc.) unchanged, and the clauses are not affected by them. A fix that puts helpers into the module globals with setdefault (the shape of defk's
    `_install-guard-globals`) would leave the author's Transfer in place and the clauses would read it, and a fix that imports would
    overwrite the author's names."""
    mod = _eval_module(_MODULE_OWN_NAMES + source, header=_CLASS_ONLY_HEADER)
    bare = _eval_module(_MODULE_OWN_NAMES + "(defclass C [])", header=_CLASS_ONLY_HEADER)
    assert _run_with_state(program(mod)) == 101
    assert sorted(vars(mod)) == sorted(vars(bare))
    assert (mod.Transfer, mod.Resume, mod.Pass) == (
        "author's Transfer",
        "author's Resume",
        "author's Pass",
    )


# Names that functions created inside the expansion of a class body may look up as global: builtins, and the module `__name__` that the session key reads
# (_build-lazy-init-forms). Beyond these, only the names the author wrote in handle / defhandler (effect types and names in clause bodies).
_GLOBALS_ALWAYS_ALLOWED = frozenset(dir(builtins)) | {"__name__"}

# Also add shapes read only by the structure tests (go through clause operations, binding, declarations, and the expansion of session val -- not run)
_CLASS_BODY_SHAPES = {
    **{name: source for name, (source, _) in _CLASS_BODY_CASES.items()},
    "handle-reperform": "(defclass C [] (setv program (handle (Bump 1) (Bump [n] (reperform effect)))))",
    "defhandler-finish": '(defclass C [] (defhandler h (Bump [n] :finish-reason "deadline" (finish n))))',
    "defhandler-declared-session-val": """
        (defclass C []
          (defhandler h {:effects [Scale]} (session val base (abs 1))
            (Bump [n] (<- r (Scale n)) (resume (+ r base)))))
        """,
}


def _written_names(source: str) -> frozenset[str]:
    """Names (in Python spelling) the author wrote in source. Excludes the defined name of defhandler -- if a function inside the expansion read it by name,
    in a class body it would skip the class attribute and look up the module globals."""
    names: set[str] = set()

    def walk(form: object) -> None:
        if isinstance(form, hy.models.Symbol):
            names.add(hy.mangle(str(form)))
        elif isinstance(form, hy.models.Sequence):
            items = list(form)
            if isinstance(form, hy.models.Expression) and items and str(items[0]) == "defhandler":
                items = items[:1] + items[2:]
            for item in items:
                walk(item)

    for form in hy.read_many(source):
        walk(form)
    return frozenset(names)


def _class_body_code(source: str) -> types.CodeType:
    """Compile a module with a single class and return the code of the body of class C (not run)."""
    mod = types.ModuleType("handle_scope")
    sys.modules["handle_scope"] = mod
    tree = hy_compile(hy.read_many(_CLASS_ONLY_HEADER + source), mod)
    module_code = compile(tree, "<handle_scope>", "exec")
    return next(
        const
        for const in module_code.co_consts
        if isinstance(const, types.CodeType) and const.co_name == "C"
    )


def _nested_global_loads(code: types.CodeType) -> set[str]:
    """Names that functions created inside code (nested code) look up as global. Lookups of the class body itself are not counted."""
    names: set[str] = set()
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= {
                instruction.argval
                for instruction in dis.get_instructions(const)
                if instruction.opname in ("LOAD_GLOBAL", "LOAD_FROM_DICT_OR_GLOBALS")
            }
            names |= _nested_global_loads(const)
    return names


@pytest.mark.parametrize("source", _CLASS_BODY_SHAPES.values(), ids=_CLASS_BODY_SHAPES.keys())
def test_class_body_handle_functions_load_only_written_globals(source: str) -> None:
    """Functions created inside the expansion of handle / defhandler in a class body (clause functions, the function that assembles the handler, the function that wraps the body) do not look up anything global beyond builtins,
    `__name__`, and the names the author wrote. What the expansion reads by name is imported inside the assembling function and read from the closure --
    names imported or defined at the class level become class attributes, and a function skips the class level and looks up the module globals. A test that does not depend on the list of imported names,
    and also catches clause shapes that the running tests miss (design verification
    blind B: a change in which a clause reads the defined name `h` by name passed the running tests)."""
    loaded = _nested_global_loads(_class_body_code(source))
    assert loaded - _GLOBALS_ALWAYS_ALLOWED - _written_names(source) == set()
