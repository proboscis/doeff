"""Check that the names read by the expansion of handle / defhandler do not shadow the writer's effect of the same name (blind A of design verification).

The expansion reads `Resume`, `Transfer`, `Pass`, `WithHandler` (and `Get`, `Put`, `Some` if there is a session) for clause operations and the handler.
When imported under bare names, if the writer named their own effect the same (`Transfer` of a transfer, `Put` of KV), the clause-head
`isinstance` and annotation pointed at the expansion's name, and the effect silently slipped through to the outer handler. At module level the expansion's import overwrote the writer's
`Transfer` with the VM's `Transfer`, and `(Transfer 5)` raised TypeError.

- In a module where the writer made any of the 7 names their own effect, the clauses of handle at module level, in a defk body, in a plain defn body, in a class body, and of
  defhandler receive and answer that effect (passing clause operations resume, transfer, reperform, a `:when` that does not match, and
  the := of a session var).
- After handle / defhandler at module level, the module's names remain the writer's. The expansion does not put names the writer did not write
  into the module globals.
- The expansion (for runtime and for type checking) neither reads nor binds the 7 names (and `typed_resume` and `typed_transfer` for type checking) under bare names
  -- in any location.
"""

import ast
import builtins
import contextlib
import dataclasses
import sys
import types
from collections.abc import Callable
from typing import NamedTuple

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_core_effects import scheduled, state
from doeff_hy._static_view import static_view

from doeff import run

# The doeff names the expansion reads (handle.hy, clause_ops.hy, binding_forms.py, `set!` in macros.hy). Names the writer may use for their own effect
# -- Transfer of a transfer, Put / Get of KV, Some of a value, Resume of resumption, Pass to forward outward, WithHandler to wrap a handler.
_NAMES = ("Resume", "Transfer", "Pass", "WithHandler", "Get", "Put", "Some")

# Names read only by the expansion for type checking (typed_resume / typed_transfer -- typed calls of a clause's resume / transfer)
_STATIC_ONLY_NAMES = ("typed_resume", "typed_transfer")

_HEADER = """
(require doeff-hy.macros [defk <- handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Other [EffectBase] #^ int x)
"""


def _writer_effect(name: str) -> str:
    """A source in which the writer defines an effect with the same name as the expansion's."""
    return f"(defclass [(dataclass :frozen True)] {name} [EffectBase] #^ int n)\n"


def _eval_module(*sources: str) -> types.ModuleType:
    """Load Hy sources into fresh modules in order -- to keep module globals separate per test."""
    mod = types.ModuleType("handle_writer_names")
    sys.modules["handle_writer_names"] = mod
    for source in sources:
        for form in hy.read_many(source):
            hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run_with_state(program: object) -> object:
    """The state handler receives Get and Put of the session var."""
    return run(scheduled(state(initial={})(program)))


# Expressions that receive the writer's effect {N}. All of them answer 101.
# - tail: a resume at the tail (becomes a Transfer)
# - pass: an effect not received (Other) is Passed where there is no clause, a clause whose :when does not match is also Passed, and a resume inside the outer handler's try stays
#   a Resume
_EXPRESSIONS = {
    "tail": "(handle ({N} 1) ({N} [n] (resume (+ n 100))))",
    "pass": """
        (handle
          (handle
            (handle ({N} 1) (Other [x] (resume x)))
            ({N} [n] :when (> n 5) (resume n)))
          ({N} [n] (try (resume (+ n 100)) (except [ValueError] (transfer 0)))))
        """,
}

# defhandler that receives the writer's effect {N} (the := of the session var passes Get, Put, and Some). Answers 101.
_DEFHANDLER = """
(defhandler h (session var calls 99)
  ({N} [n] (:= calls (+ calls 1)) (resume (+ n calls))))
"""
_DEFHANDLER_PROGRAM = "(h ({N} 1))"

_HOSTS = ("module", "defk", "defn", "class")


class _HostedModule(NamedTuple):
    """The Hy source written to the host, and a function that obtains a Program producing the answer from the module that loaded it."""

    source: str
    program: Callable[[types.ModuleType], object]


def _expression_module(host: str, expression: str) -> _HostedModule:
    """The source with the handle expression `expression` written to the host, and how to obtain the Program that produces the answer."""
    match host:
        case "module":
            return _HostedModule(f"(setv program {expression})", lambda mod: mod.program)
        case "defk":
            return _HostedModule(
                f"(defk subject [] {{:pre [] :post [(: % int)]}} (<- r {expression}) r)",
                lambda mod: mod.subject(),
            )
        case "defn":
            return _HostedModule(f"(defn host [] {expression})", lambda mod: mod.host())
        case "class":
            return _HostedModule(f"(defclass C [] (setv program {expression}))", lambda mod: mod.C.program)
        case _:
            raise AssertionError(host)


def _defhandler_module(host: str, name: str) -> _HostedModule:
    """The source with the defhandler written to the host, and how to obtain the Program that produces the answer."""
    definition = _DEFHANDLER.replace("{N}", name)
    program = _DEFHANDLER_PROGRAM.replace("{N}", name)
    match host:
        case "module":
            return _HostedModule(f"{definition}(setv program {program})", lambda mod: mod.program)
        case "defk":
            return _HostedModule(
                f"(defk subject [] {{:pre [] :post [(: % int)]}} {definition} (<- r {program}) r)",
                lambda mod: mod.subject(),
            )
        case "defn":
            return _HostedModule(f"(defn host [] {definition} {program})", lambda mod: mod.host())
        case "class":
            return _HostedModule(f"(defclass C [] {definition})", lambda mod: mod.C.h(getattr(mod, name)(1)))
        case _:
            raise AssertionError(host)


def _scenarios() -> dict[str, tuple[str, str, Callable[[types.ModuleType], object]]]:
    """For each name x place x way of writing, (the writer's effect name, source, how to obtain the Program)."""
    found: dict[str, tuple[str, str, Callable[[types.ModuleType], object]]] = {}
    for name in _NAMES:
        for host in _HOSTS:
            for kind, expression in _EXPRESSIONS.items():
                source, program = _expression_module(host, expression.replace("{N}", name))
                found[f"{name}-{host}-handle-{kind}"] = (name, source, program)
            source, program = _defhandler_module(host, name)
            found[f"{name}-{host}-defhandler-session"] = (name, source, program)
    return found


_SCENARIOS = _scenarios()


@pytest.mark.parametrize(("name", "source", "program"), _SCENARIOS.values(), ids=_SCENARIOS.keys())
def test_clause_answers_the_writers_effect_named_like_an_expansion_name(
    name: str, source: str, program: Callable[[types.ModuleType], object]
) -> None:
    """The writer's module effect `{name}` is received and answered by the clauses of handle / defhandler -- if hidden by the expansion's `{name}`, the clause-head
    isinstance points at the VM node and the effect passes straight through the clause to become UnhandledEffect (at module level the writer's name is
    overwritten and `({name} 1)` raises TypeError)."""
    mod = _eval_module(_HEADER, _writer_effect(name), source)
    assert _run_with_state(program(mod)) == 101


@pytest.mark.parametrize("name", _NAMES)
def test_module_level_handle_and_defhandler_leave_the_writers_names_alone(name: str) -> None:
    """handle / defhandler at module level do not rebind the writer's module names (such as `Transfer`, `Pass`, `Put`) to something else."""
    mod = _eval_module(_HEADER, _writer_effect(name))
    written = mod.__dict__[name]
    source, _ = _expression_module("module", _EXPRESSIONS["pass"].replace("{N}", name))
    for form in hy.read_many(source + _DEFHANDLER.replace("{N}", name)):
        hy.eval(form, mod.__dict__, module=mod)
    assert mod.__dict__[name] is written


def test_module_level_handle_and_defhandler_do_not_put_the_bare_names_in_the_globals() -> None:
    """The expansion does not put `Resume`, `Transfer`, `Pass`, `WithHandler`, `Get`, `Put`, `Some`, which the writer did not write, into the module globals
    (doing so would share one name with the writer's name)."""
    mod = _eval_module(
        _HEADER,
        _expression_module("module", _EXPRESSIONS["pass"].replace("{N}", "Other"))[0],
        _DEFHANDLER.replace("{N}", "Other"),
    )
    assert {name for name in _NAMES if name in vars(mod)} == set()


# ---------------------------------------------------------------------------
# Expansion structure -- the runtime expansion and the expansion for type checking neither read nor bind bare names
# ---------------------------------------------------------------------------

_EXPANSION_HEADER = _HEADER + "(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)\n"

_EXPANSION_SOURCES = {
    "module-handle": "(setv program (handle (Bump 1) (Bump [n] (resume (+ n 100)))))",
    "module-handle-pass": _EXPRESSIONS["pass"].replace("{N}", "Bump").join(["(setv program ", ")"]),
    "module-defhandler": _DEFHANDLER.replace("{N}", "Bump"),
    "defk-handle": (
        "(defk subject [] {:pre [] :post [(: % int)]} (<- r (handle (Bump 1) (Bump [n] (resume n)))) r)"
    ),
    "defn-defhandler": (
        "(defn host [] (defhandler h (session var calls 0) (Bump [n] (:= calls (+ calls 1)) "
        "(reperform effect))) h)"
    ),
    "class-handle": "(defclass C [] (setv program (handle (Bump 1) (Bump [n] (resume n)))))",
    "class-defhandler": _DEFHANDLER.replace("{N}", "Bump").join(["(defclass C [] ", ")"]),
    "legacy-set!": (
        "(defhandler h (lazy-var calls 0) (Bump [n] (set! calls (+ calls 1)) (resume calls)))"
    ),
}


def _expand(source: str, *, static: bool) -> ast.Module:
    """Expand the Hy source into a Python AST (without evaluating). For static, the expansion for type checking."""
    mod = types.ModuleType("handle_writer_names_expand")
    sys.modules["handle_writer_names_expand"] = mod
    with static_view() if static else contextlib.nullcontext():
        return hy.compiler.hy_compile(
            hy.read_many(_EXPANSION_HEADER + source), mod, filename="<writer-names>"
        )


@pytest.mark.parametrize("static", [False, True], ids=["runtime", "static"])
@pytest.mark.parametrize("source", _EXPANSION_SOURCES.values(), ids=_EXPANSION_SOURCES.keys())
def test_expansion_neither_reads_nor_binds_a_bare_expansion_name(source: str, static: bool) -> None:
    """The expansion neither reads nor binds bare names shared with the writer's namespace -- it reads only aliases with the `_doeff_` prefix. The source the writer wrote
    has none of the 7 names, so a bare name in the expansion tree is a name where the expansion's spelling collides with the writer's name."""
    tree = _expand(source, static=static)
    watched = {*_NAMES, *_STATIC_ONLY_NAMES}
    read = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    bound = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert (read | bound) & watched == set()


# ---------------------------------------------------------------------------
# Position -- the import lines of the expansion for type checking are on the user's handler line (not pulled to line 1)
# ---------------------------------------------------------------------------

_PADDING = "\n" * 24


def test_static_expansion_imports_keep_the_handlers_line() -> None:
    """The import of the `_doeff_` aliases that the expansion for type checking emits per handler is placed on the user's handler line. If the model the reader builds is mixed into the
    import, the macro that attaches positions stretches the enclosing expression's range to line 1, and diagnostics (unused import, undefined name) land at 1:1
    (the baseline version dc3385b9a is the handler line -- measured in blind A of design verification)."""
    mod = types.ModuleType("handle_writer_names_position")
    sys.modules[mod.__name__] = mod
    source = _PADDING + _EXPANSION_HEADER + _EXPANSION_SOURCES["module-defhandler"]
    with static_view():
        tree = hy.compiler.hy_compile(hy.read_many(source), mod, filename="<position>")
    first_user_line = len(_PADDING) + 1
    aliased = {
        alias.asname: node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname is not None and alias.asname.startswith("_doeff_")
    }
    assert aliased, "the expansion does not emit the alias import"
    assert {name: line for name, line in aliased.items() if line < first_user_line} == {}


# ---------------------------------------------------------------------------
# Expansion structure (general form) -- independent of the name list. A name the expansion reads or binds is either the writer's or the expansion's (the `_doeff_` prefix)
# ---------------------------------------------------------------------------

# Names the expansion puts in the module that do not have the `_doeff` / `_hy` prefix: `hy` that the Hy compiler puts in every module, the doeff_hy modules imported by the declaration bookkeeping
# (declarations.hy), and helpers bound by the expansion of a defk body (`_helper-imports` in macros.hy --
# not those of handle / defhandler). Assuming the writer never uses the same names, they are allowed by name (a bare name not here is red).
_EXPANSION_OWNED_PLAIN = frozenset(
    {"hy", "doeff_hy", "_install_guard_globals", "_guard_performed", "_guard_statement_value"}
)


def _expansion_owned(name: str) -> bool:
    """Whether it is a name the expansion places as its own (the `_doeff` prefix, the `_hy` prefix of Hy gensyms, dunders, the 2 allowed names)."""
    return name.startswith(("_doeff", "_hy", "__")) or name in _EXPANSION_OWNED_PLAIN


def _bound_names(tree: ast.AST) -> frozenset[str]:
    """Names bound inside the tree (assignment, arguments, def / class, import, `as` of except, global declarations)."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name, ctx=ast.Store() | ast.Del()):
                bound.add(name)
            case ast.arg(arg=name):
                bound.add(name)
            case (
                ast.FunctionDef(name=name)
                | ast.AsyncFunctionDef(name=name)
                | ast.ClassDef(name=name)
            ):
                bound.add(name)
            case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
                bound.update(alias.asname or alias.name.split(".")[0] for alias in aliases)
            case ast.ExceptHandler(name=str(name)):
                bound.add(name)
            case ast.Global(names=names) | ast.Nonlocal(names=names):
                bound.update(names)
    return frozenset(bound)


def _import_bound_names(tree: ast.AST) -> frozenset[str]:
    """Names bound by imports inside the tree."""
    return frozenset(
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    )


def _written_symbols(source: str) -> frozenset[str]:
    """Symbols (in Python spelling) the writer wrote in the source. For a dotted spelling, the head name is included too."""
    names: set[str] = set()

    def walk(form: object) -> None:
        if isinstance(form, hy.models.Symbol):
            names.update(hy.mangle(part) for part in str(form).split(".") if part)
        elif isinstance(form, hy.models.Sequence):
            for item in form:
                walk(item)

    for form in hy.read_many(source):
        walk(form)
    return frozenset(names)


def _unowned_reads(tree: ast.Module, source: str) -> frozenset[str]:
    """Among names the expansion tree reads, those not bound in the tree, not builtins, not written by the writer, and not the expansion's own. A spelling that shares
    one name with the writer's namespace (a bare name) shows up here."""
    reads = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return frozenset(
        name
        for name in reads - _bound_names(tree) - _written_symbols(source)
        if not hasattr(builtins, name) and not _expansion_owned(name)
    )


def _unowned_imports(tree: ast.Module, header_tree: ast.Module) -> frozenset[str]:
    """Among names bound by imports in the expansion tree, those that are neither imports of the writer's header nor the expansion's own."""
    return frozenset(
        name
        for name in _import_bound_names(tree) - _import_bound_names(header_tree)
        if not _expansion_owned(name)
    )


@pytest.mark.parametrize("static", [False, True], ids=["runtime", "static"])
@pytest.mark.parametrize("source", _EXPANSION_SOURCES.values(), ids=_EXPANSION_SOURCES.keys())
def test_expansion_reads_and_binds_only_written_or_expansion_owned_names(
    source: str, static: bool
) -> None:
    """A form independent of the name list: names the expansion reads are the writer's or the expansion's (the `_doeff_` prefix), and names it binds are only the expansion's. If the expansion reads a new name (such as a node added by a table row)
    under a bare spelling, it goes red even if the name is not in the watched list (the hole in the hand-copied name list that blinds A and B of design verification
    pointed out)."""
    tree = _expand(source, static=static)
    header_tree = _expand("", static=static)
    assert _unowned_reads(tree, _EXPANSION_HEADER + source) == frozenset()
    assert _unowned_imports(tree, header_tree) == frozenset()


def test_general_check_catches_a_new_node_read_bare_where_the_name_list_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutation where a table-row rewrite reads a new node under a bare Symbol (`ResumeBare`) -- the list of 9 names cannot catch it (a name not in the list), but the general form does."""
    from doeff_hy import _clause_ops as clause_ops
    from doeff_hy import _expansion_names as expansion_names

    def bare_rewrite(
        args: list[hy.models.Object], recur: Callable[[hy.models.Object], hy.models.Object]
    ) -> hy.models.Object:
        # The continuation is the clause's continuation `_doeff_k` (reading a bare `k` would also catch reads other than this mutation)
        return hy.models.Expression(
            [
                hy.models.Symbol("yield"),
                hy.models.Expression(
                    [
                        hy.models.Symbol("ResumeBare"),
                        expansion_names.continuation_symbol(),
                        recur(args[0]),
                    ]
                ),
            ]
        )

    resume = clause_ops.named_op("resume")
    assert resume is not None
    mutant = dataclasses.replace(resume, nodes=("ResumeBare",), rewrite=bare_rewrite)
    # The table of expansion names is closed (a name not in the table is rejected by home) -- add the mutated node's place to the table too
    monkeypatch.setitem(expansion_names._HOMES, "ResumeBare", "doeff")
    monkeypatch.setattr(
        clause_ops,
        "CLAUSE_OPS",
        tuple(mutant if op.name == "resume" else op for op in clause_ops.CLAUSE_OPS),
    )
    source = "(setv program (handle (Bump 1) (Bump [n] (setv answer (resume n)) answer)))"
    tree = _expand(source, static=False)
    watched = {*_NAMES, *_STATIC_ONLY_NAMES}
    list_based = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    } & watched
    assert list_based == set()
    assert _unowned_reads(tree, _EXPANSION_HEADER + source) == frozenset({"ResumeBare"})


def test_names_the_module_level_expansion_adds_are_written_or_expansion_owned() -> None:
    """Names that handle / defhandler at module level (including session var) add to the module globals are only names the writer wrote or the expansion's own
    (the general form of R2 -- including names outside the test that looks only at the bare 7 names)."""
    source = _expression_module("module", _EXPRESSIONS["pass"].replace("{N}", "Other"))[
        0
    ] + _DEFHANDLER.replace("{N}", "Other")
    mod = _eval_module(_HEADER, source)
    bare = _eval_module(_HEADER)
    added = frozenset(vars(mod)) - frozenset(vars(bare))
    unowned = {
        name
        for name in added
        if not _expansion_owned(name) and name not in _written_symbols(source)
    }
    assert unowned == set()
