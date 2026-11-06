"""Failure cases of mismatches between the type declaration (.pyi) next to a Hy module and its implementation (built for sql_effects.pyi and sqlite_sql.pyi,
extended in #2323 to http_server_effects, file_effects, memory_file, random_effects, and seeded_random).

The type declarations are handwritten, so if a field is added, removed, or reordered on the .hy side, or a function argument is changed, only the declaration silently stays stale.
Here the .hy source is read, the public names defined directly under the module are checked against the names in the declaration, and in addition:

- For defrecord / defeffect types, the dataclass field names, order, and presence of defaults are the same as the declaration's fields.
- For defenum types, the member names and values are the same.
- For defk / defhandler functions, the argument names and order are the same.
"""

import ast
import copy
import dataclasses
import enum
import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

import doeff_hy  # noqa: F401  # enable the Hy import hook
import hy
import pytest
from doeff_hy._static_stub import hidden_name

# The stub (.pyi) is written in the type-parameter syntax (PEP 695 -- `class SqlTransaction[T]`), which the ast of 3.11 cannot read. Matching the stub against the module
# does not depend on the Python version, so look at it from 3.12.
pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="the type-parameter syntax of the stub needs Python 3.12 or later")

PACKAGE = Path(__file__).resolve().parent.parent / "doeff_core_effects"

#: Hy modules that have a type declaration (list them here when you add a declaration).
STUBBED_MODULES = (
    "_sql_effects",
    "_sqlite_sql",
    "_sqlite_file_sql",
    "_http_server_effects",
    "_file_effects",
    "_memory_file",
    "_random_effects",
    "_seeded_random",
    "_scripted_http_server",
    "_scripted_process",
    "_meter_effects",
    "_meter_prometheus",
    "_memory_meter",
    "_memory_latest",
    "_latest_effects",
    "_stack_dump_effects",
    "_memory_stack_dump",
    "_faulthandler_stack_dump",
    "_os_file",
    "_ws_client_effects",
    "_scripted_ws_client",
    "_aiohttp_ws_client",
)

#: Heads of the Hy forms that define a name directly under the module.
DEFINING_HEADS = frozenset(
    {
        "defrecord",
        "defenum",
        "defeffect",
        "defclass",
        "defk",
        "deff",
        # A defn with a `cannot be a defk` reason (such as a predicate called inside a comprehension) also defines a public name -- the .pyi the tool generates declares it (#2887).
        "defn",
        "defhandler",
        "val",
        "var",
        "setv",
    }
)


@dataclass(frozen=True)
class StubField:
    """One dataclass field (name and presence of a default)."""

    name: str
    has_default: bool


def _named(form: hy.models.Expression) -> hy.models.Symbol | None:
    """The Symbol of the name of a defining form: the element after the head. The keyword of `(defn :async ...)` is skipped, and a return-value annotation `(defn #^ T name ...)` (which the reader
    reads as `(annotate name T)`) takes the inner name (#4007 -- to check modules of answerers that have an async defn). None if there is no name."""
    rest = [item for item in form[1:] if not isinstance(item, hy.models.Keyword)]
    match rest:
        case [hy.models.Symbol() as name, *_]:
            return name
        case [hy.models.Expression() as annotated, *_]:
            match tuple(annotated):
                case (hy.models.Symbol() as head, hy.models.Symbol() as name, _) if str(head) == "annotate":
                    return name
                case _:
                    return None
        case _:
            return None


def _defined_name(form: object) -> str | None:
    """The name defined by one form directly under .hy (mangled to a Python name). None if it is not a defining form."""
    match form:
        case hy.models.Expression() if (
            len(form) >= 2
            and str(form[0]) in DEFINING_HEADS
            and _named(form) is not None
        ):
            return hy.mangle(str(_named(form)))
        # A decorated class (`(defclass [(dataclass :frozen True)] name [EffectBase] ...)`) has its name in the 3rd position.
        case hy.models.Expression() if (
            len(form) >= 3
            and str(form[0]) == "defclass"
            and isinstance(form[1], hy.models.List)
            and isinstance(form[2], hy.models.Symbol)
        ):
            return hy.mangle(str(form[2]))
        case _:
            return None


def _hy_defined_names(module: str) -> tuple[str, ...]:
    """Public names defined directly under the .hy module (in the order written)."""
    forms = hy.read_many((PACKAGE / f"{module}.hy").read_text(encoding="utf-8"))
    names = (_defined_name(form) for form in forms)
    return tuple(n for n in names if n is not None and not n.startswith("_") and not hidden_name(n))


def _stub_tree(module: str) -> ast.Module:
    return ast.parse((PACKAGE / f"{module}.pyi").read_text(encoding="utf-8"))


def _declared_name(node: ast.stmt) -> str | None:
    """The name declared by one statement directly under the declaration (an import is not a declaration -- None)."""
    match node:
        case (
            ast.ClassDef(name=name)
            | ast.FunctionDef(name=name)
            | ast.AnnAssign(target=ast.Name(id=name))
        ):
            return name
        case ast.Assign(targets=[ast.Name(id=name)]):
            return name
        case _:
            return None


def _stub_declared_names(tree: ast.Module) -> tuple[str, ...]:
    """Public names declared directly under the declaration module (excluding types inside a declaration that start with _). Names not shown on the type surface, such as the module tag (MODULE_TAGS),
    are removed from both sides with hidden_name, the same judgment as the generation tool doeff_hy._static_stub (#2909)."""
    names = (_declared_name(node) for node in tree.body)
    return tuple(n for n in names if n is not None and not n.startswith("_") and not hidden_name(n))


def _stub_fields(node: ast.ClassDef) -> tuple[StubField, ...]:
    """The annotated fields (in the order written) of the body of a declared class."""
    return tuple(
        StubField(name=stmt.target.id, has_default=stmt.value is not None)
        for stmt in node.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    )


def _stub_enum_members(node: ast.ClassDef) -> tuple[tuple[str, str], ...]:
    """The members (name and value, in the order written) of a declared StrEnum."""
    return tuple(
        (stmt.targets[0].id, stmt.value.value)
        for stmt in node.body
        if isinstance(stmt, ast.Assign)
        and isinstance(stmt.targets[0], ast.Name)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _runtime_fields(cls: type) -> tuple[StubField, ...]:
    return tuple(
        StubField(
            name=f.name,
            has_default=f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING,
        )
        for f in dataclasses.fields(cls)
    )


def _stub_parameters(node: ast.FunctionDef) -> tuple[str, ...]:
    args = node.args
    return tuple(a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs))


def _class_mismatch(name: str, value: object, node: ast.ClassDef) -> str | None:
    """The mismatch between one declared class and the implemented type (None = matches or not a form to check)."""
    match value:
        case type() if issubclass(value, enum.Enum):
            actual = tuple((m.name, str(m.value)) for m in value)
            declared = _stub_enum_members(node)
            return (
                None
                if actual == declared
                else f"{name} has different members: implementation {actual} / declaration {declared}"
            )
        case type() if dataclasses.is_dataclass(value):
            actual_fields = _runtime_fields(value)
            declared_fields = _stub_fields(node)
            return (
                None
                if actual_fields == declared_fields
                else f"{name} has different fields: implementation {actual_fields} / declaration {declared_fields}"
            )
        case _:
            return None


def _function_mismatch(name: str, value: object, node: ast.FunctionDef) -> str | None:
    """The mismatch between the arguments of one declared function and the implemented function (None = matches)."""
    match value:
        case _ if callable(value):
            actual = tuple(inspect.signature(value).parameters)
            declared = _stub_parameters(node)
            return (
                None
                if actual == declared
                else f"{name} has different arguments: implementation {actual} / declaration {declared}"
            )
        case _:
            return f"{name} is not a function in the implementation"


def _mismatches(module: str, tree: ast.Module) -> tuple[str, ...]:
    """The list of mismatches between the declaration (tree) and the implementation (empty = matches)."""
    runtime = importlib.import_module(f"doeff_core_effects.{module}")
    defined = _hy_defined_names(module)
    declared = _stub_declared_names(tree)
    public = [node for node in tree.body if _declared_name(node) in defined]
    missing = tuple(f"public name not in the declaration: {n}" for n in defined if n not in declared)
    extra = tuple(f"declared name not in the implementation: {n}" for n in declared if n not in defined)
    classes = (
        _class_mismatch(node.name, getattr(runtime, node.name), node)
        for node in public
        if isinstance(node, ast.ClassDef)
    )
    functions = (
        _function_mismatch(node.name, getattr(runtime, node.name), node)
        for node in public
        if isinstance(node, ast.FunctionDef)
    )
    return missing + extra + tuple(m for m in (*classes, *functions) if m is not None)


@pytest.mark.parametrize("module", STUBBED_MODULES)
def test_the_stub_matches_the_hy_module(module: str) -> None:
    assert _mismatches(module, _stub_tree(module)) == ()


def _without_field(tree: ast.Module, class_name: str, field: str) -> ast.Module:
    """A copy of the declaration with the field field removed from the class class_name."""
    broken = copy.deepcopy(tree)
    for node in broken.body:
        match node:
            case ast.ClassDef(name=name) if name == class_name:
                node.body = [
                    s
                    for s in node.body
                    if not (isinstance(s, ast.AnnAssign) and ast.unparse(s.target) == field)
                ]
            case _:
                pass
    return broken


def test_a_dropped_stub_field_is_found() -> None:
    # Removing the field rowcount from the declared SqlRows is found as a field mismatch (the test does not pass silently).
    broken = _without_field(_stub_tree("_sql_effects"), "SqlRows", "rowcount")
    assert [m for m in _mismatches("_sql_effects", broken) if m.startswith("SqlRows has different fields")]


def test_a_dropped_effect_field_is_found() -> None:
    # Removing the field ws_send_max_bytes from the declared effect HttpListen (a defclass dataclass) is found as a field mismatch.
    broken = _without_field(_stub_tree("http_server_effects"), "HttpListen", "ws_send_max_bytes")
    assert [
        m
        for m in _mismatches("http_server_effects", broken)
        if m.startswith("HttpListen has different fields")
    ]


def test_a_dropped_arrival_remote_is_found() -> None:
    # Removing the sender field remote from the declaration of HttpRequestArrived, an event the script carries, is found as a field mismatch.
    broken = _without_field(_stub_tree("http_server_effects"), "HttpRequestArrived", "remote")
    assert [
        m
        for m in _mismatches("http_server_effects", broken)
        if m.startswith("HttpRequestArrived has different fields")
    ]


def test_a_dropped_handler_parameter_is_found() -> None:
    # Removing the argument script from the declared scripted_http_server is found as an argument mismatch.
    broken = copy.deepcopy(_stub_tree("scripted_http_server"))
    for node in broken.body:
        match node:
            case ast.FunctionDef(name="scripted_http_server"):
                node.args.args = []
            case _:
                pass
    assert [
        m
        for m in _mismatches("scripted_http_server", broken)
        if m.startswith("scripted_http_server has different arguments")
    ]


def test_a_module_mark_is_not_a_public_name_but_a_dropped_function_still_is() -> None:
    # The head tag MODULE-TAGS (the mark of doeff-linter) is not counted as a name on the type surface, same as the generation tool static_stub (#2909). memory_file.hy
    # defines MODULE-TAGS, and the declaration has no MODULE_TAGS but it is not a mismatch. Removing one public function from the declaration is still found as before.
    forms = hy.read_many((PACKAGE / "_memory_file.hy").read_text(encoding="utf-8"))
    assert "MODULE_TAGS" in {_defined_name(form) for form in forms}
    assert "MODULE_TAGS" not in _hy_defined_names("_memory_file")
    assert _mismatches("_memory_file", _stub_tree("_memory_file")) == ()
    broken = copy.deepcopy(_stub_tree("_memory_file"))
    dropped = next(
        node.name for node in broken.body if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    )
    broken.body = [node for node in broken.body if not (isinstance(node, ast.FunctionDef) and node.name == dropped)]
    assert f"public name not in the declaration: {dropped}" in _mismatches("_memory_file", broken)
