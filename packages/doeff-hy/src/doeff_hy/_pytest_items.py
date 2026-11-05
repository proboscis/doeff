"""Record of pytest items -- written by the macros that make items at expansion time, and read by the pytest plugin without importing the test module.

The shape that keeps pytest collection under 1 second: importing a test module during collection runs the module's top level
and takes 10 seconds for one file. So the macros that make items (deftest, defadr, defsemgrep, and the module-level
``(val pytestmark …)``) record "the functions and marks this module shows to pytest" at expansion time. The record goes into the
module as an expansion expression (``record-at-import`` pushes a JSON string onto the module's ``__doeff_pytest_items__`` -- baked into the pyc),
and the pytest plugin (doeff-adr) takes it out once when it imports, and stores it in its own cache keyed by a hash of the source contents.
The Hy importer is left untouched (#1291).

This module is the **sole source of definition** of the record shape: it holds both the writing function (called by macros) and the reading function (called by the plugin).
The meaning of deftest keys (``:marks``, ``:params`` …) is read on the macro side, and what arrives here is the form rewritten in pytest terms (function name, argument names,
parametrize, mark, skipif). The plugin does not know deftest keys.

Handling of params values: at expansion time only the shape of the expression is visible, so if the value is a literal (string, integer, float, bool, None) the value is
recorded as is, and if it is a literal container (list, tuple, dict, set, keyword) it is recorded as a "value whose content does not matter" (the pytest id becomes
``<argument name><number>`` and does not depend on the value). Anything else (an expression, a name reference) is not decided until the test count runs,
so it is recorded as "dynamic". After the first import, the plugin fills in the real values and explicit ids, and if it can save them, uses the record from the next collection.
"""

import json
import types
from collections.abc import Iterable, MutableMapping, Sequence
from dataclasses import dataclass

import hy

# Name of the module attribute where the expansion expression pushes the record (the plugin takes it from the imported module).
ITEMS_ATTR = "__doeff_pytest_items__"

JsonValue = str | int | float | bool | None


# ---------------------------------------------------------------------------
# Types of the record (the shape returned to the reader)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiteralValue:
    """Among the values of params, those whose value is known at expansion time (the pytest id is determined from the value)."""

    value: JsonValue


@dataclass(frozen=True)
class OpaqueValue:
    """Among the values of params, a literal container (the pytest id is the argument name and a number -- independent of the value)."""


ParamValue = LiteralValue | OpaqueValue


@dataclass(frozen=True)
class Parametrize:
    argnames: str
    values: tuple[ParamValue, ...]
    ids: tuple[str | None, ...] | None = None


@dataclass(frozen=True)
class Mark:
    name: str


@dataclass(frozen=True)
class SkipIf:
    """The condition is an expression evaluated at run time, so it is not recorded. It is evaluated at item setup from the marks of the real function."""


Decorator = Parametrize | Mark | SkipIf


@dataclass(frozen=True)
class FunctionItem:
    """One test function shown to pytest. ``decorators`` are in source order (top first) -- applied from the bottom, as with the real thing."""

    name: str
    argnames: tuple[str, ...]
    decorators: tuple[Decorator, ...]


@dataclass(frozen=True)
class ModuleMarks:
    """The module-level ``pytestmark``."""

    names: tuple[str, ...]


@dataclass(frozen=True)
class Dynamic:
    """Something whose item shape is not decided at expansion time (the count depends on the value of an expression, etc.)."""

    where: str
    reason: str


Record = FunctionItem | ModuleMarks | Dynamic


# ---------------------------------------------------------------------------
# JSON boundary (conversion between record shape and types happens only here)
# ---------------------------------------------------------------------------


class MalformedRecord(ValueError):
    """The record is not in the shape this module writes (written by a different version of doeff-hy, etc.)."""


def _encode_value(value: ParamValue) -> dict[str, object]:
    """One params value into record JSON (the writing-side boundary)."""
    match value:
        case LiteralValue(v):
            return {"literal": v}
        case OpaqueValue():
            return {"opaque": True}


def _encode_decorator(decorator: Decorator) -> dict[str, object]:
    """One decorator into record JSON (the writing-side boundary)."""
    match decorator:
        case Parametrize(argnames, values, ids):
            return {"parametrize": argnames, "values": [_encode_value(v) for v in values], "ids": ids}
        case Mark(name):
            return {"mark": name}
        case SkipIf():
            return {"skipif": True}


def _encode(record: Record) -> dict[str, object]:
    """One record into JSON -- the shape the expansion expression pushes onto the module and doeff-adr saves in the cache."""
    match record:
        case FunctionItem(name, argnames, decorators):
            return {
                "function": name,
                "args": list(argnames),
                "decorators": [_encode_decorator(d) for d in decorators],
            }
        case ModuleMarks(names):
            return {"pytestmark": list(names)}
        case Dynamic(where, reason):
            return {"dynamic": where, "reason": reason}


def _text(raw: object) -> str:
    """Reject the record instead of converting a value of the wrong type to a string."""
    if not isinstance(raw, str):
        raise MalformedRecord(f"not a string: {raw!r}")
    return raw


def _array(raw: object) -> list[object]:
    """Accept only a JSON array. Do not read a string as a sequence of characters."""
    if not isinstance(raw, list):
        raise MalformedRecord(f"not an array: {raw!r}")
    return raw


def _decode_value(raw: object) -> ParamValue:
    """Verify that a params literal is an allowed scalar before turning it into a value."""
    match raw:
        case {"literal": value}:
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise MalformedRecord(f"literal value is not a scalar: {value!r}")
            return LiteralValue(value)
        case {"opaque": True}:
            return OpaqueValue()
    raise MalformedRecord(f"params value {raw!r}")


def _decode_ids(raw: object, count: int) -> list[str | None] | None:
    if raw is None:
        return None
    ids: list[str | None] = [None if value is None else _text(value) for value in _array(raw)]
    if len(ids) != count:
        raise MalformedRecord("parametrize ids and values have different lengths")
    return ids


def _decode_decorator(raw: object) -> Decorator:
    """Read a decorator from record JSON. Check the actual type of each field."""
    match raw:
        case {"parametrize": argnames} as parameters:
            decoded: tuple[ParamValue, ...] = tuple(_decode_value(value) for value in _array(parameters.get("values")))
            ids: list[str | None] | None = _decode_ids(parameters.get("ids"), len(decoded))
            return Parametrize(_text(argnames), decoded, None if ids is None else tuple(ids))
        case {"mark": name}:
            return Mark(_text(name))
        case {"skipif": True}:
            return SkipIf()
    raise MalformedRecord(f"decorator {raw!r}")


def _decode(raw: object) -> Record:
    """Record JSON into types. Stop with MalformedRecord on an unknown shape or invalid field."""
    match raw:
        case {"function": name} as function:
            return FunctionItem(
                name=_text(name),
                argnames=tuple(_text(arg) for arg in _array(function.get("args"))),
                decorators=tuple(_decode_decorator(decorator) for decorator in _array(function.get("decorators"))),
            )
        case {"pytestmark": names}:
            return ModuleMarks(tuple(_text(name) for name in _array(names)))
        case {"dynamic": where, "reason": reason}:
            return Dynamic(_text(where), _text(reason))
    raise MalformedRecord(f"record {raw!r}")


# ---------------------------------------------------------------------------
# Writing side (called by macros at expansion time)
# ---------------------------------------------------------------------------


class _NotStatic(Exception):
    """The form was something whose value is not decided at expansion time (the record becomes Dynamic)."""


_CONTAINERS = (hy.models.List, hy.models.Tuple, hy.models.Dict, hy.models.Set, hy.models.Keyword)
_CONSTANTS: dict[str, JsonValue] = {"True": True, "False": False, "None": None}


def _param_value(form: object) -> ParamValue:
    """Turn one form of a params value into a record value for which the pytest id is the same."""
    match form:
        case hy.models.FString():
            raise _NotStatic("f-string")
        case hy.models.String() as text:
            return LiteralValue(str(text))
        case hy.models.Integer() as number:
            return LiteralValue(int(number))
        case hy.models.Float() as number:
            return LiteralValue(float(number))
        case hy.models.Symbol() if str(form) in _CONSTANTS:
            return LiteralValue(_CONSTANTS[str(form)])
        case _ if isinstance(form, _CONTAINERS):
            return OpaqueValue()
        case _:
            raise _NotStatic(hy.repr(form))


def _values(form: object) -> list[ParamValue]:
    """Read the list form of parametrize values -- the count is not decided unless it is a list / tuple literal."""
    match form:
        case hy.models.List() | hy.models.Tuple():
            return [_param_value(value) for value in form]
        case _:
            raise _NotStatic(f"values {hy.repr(form)}")


# The fixed part of the record expression (a name containing a dot cannot be built directly with hy.models.Symbol, so read it with the reader).
_RECORD_IMPORT = hy.read("(import doeff-hy._pytest-items)")
_RECORD_AT_IMPORT = hy.read("doeff-hy._pytest-items.record-at-import")


def _record_form(record: Record) -> hy.models.Expression:
    """Turn one record into an expression added to the expansion -- at import time push a JSON string onto the module's ``__doeff_pytest_items__``."""
    text = json.dumps(_encode(record), ensure_ascii=False, sort_keys=True)
    return hy.models.Expression(
        [
            hy.models.Symbol("do"),
            _RECORD_IMPORT,
            hy.models.Expression(
                [
                    _RECORD_AT_IMPORT,
                    hy.models.Expression([hy.models.Symbol("globals")]),
                    hy.models.String(text),
                ]
            ),
        ]
    )


def record_at_import(module_globals: MutableMapping[str, object], text: str) -> None:
    """The entry the expansion expression calls at import time -- push one record (a JSON string) onto the module."""
    items = module_globals.setdefault(ITEMS_ATTR, [])
    if not isinstance(items, list):
        raise MalformedRecord(f"{ITEMS_ATTR} is not a list: {type(items).__name__}")
    items.append(text)


def record_function(
    name: object,
    argnames: Sequence[object],
    decorators: Sequence[tuple[str, object, object]],
) -> hy.models.Expression:
    """The entry through which a macro that makes test functions adds to the expansion the record of that function's pytest shape (returns the expression to add).

    ``decorators`` is a sequence of tuples in source order, in the following four shapes:
    ``("parametrize", <string of argument names>, <form of the values list>)`` /
    ``("mark", <form of the mark name>, None)`` / ``("skipif", None, None)`` /
    ``("scans", None, <form of a list of glob strings>)`` (deftest's ``:scans`` -- mark ``scans``. Only the mark name is kept in the record:
    the ``-m`` of collection reads the name, and the glob is read from the source by the selection tool).
    """
    fname = hy.mangle(str(name))
    try:
        specs: list[Decorator] = []
        for kind, first, second in decorators:
            match kind:
                case "parametrize":
                    specs.append(Parametrize(str(first), tuple(_values(second))))
                case "mark":
                    specs.append(Mark(hy.mangle(str(first))))
                case "skipif":
                    specs.append(SkipIf())
                case "scans":
                    specs.append(Mark("scans"))
                case _:
                    raise ValueError(f"pytest_items: unknown decorator kind {kind!r}")
    except _NotStatic as exc:
        return _record_form(Dynamic(fname, str(exc)))
    return _record_form(FunctionItem(fname, tuple(hy.mangle(str(a)) for a in argnames), tuple(specs)))


# Among module marks, those that change the collection result (item count, fixture closure) -- a call form needs values, so it is not recorded.
_COLLECTION_SHAPING_MARKS = frozenset({"parametrize", "usefixtures"})


def _mark_name(form: object) -> str:
    """From the form of a module-level mark, read the mark names that ``-m`` reads at collection (other shapes do not have a decided value).

    Reads ``pytest.mark.<name>`` and its call ``(pytest.mark.<name> …)``. The call's arguments (such as the skipif condition) are
    not recorded -- the plugin evaluates them after replacing them with the marks of the real module at item setup. However, calls of marks that change the collection result
    (parametrize, usefixtures) need values, so they are treated as undecided.
    """
    # The Hy reader reads ``pytest.mark.real-world`` as the expression ``(. pytest mark real-world)``.
    match form:
        case hy.models.Expression() if len(form) == 4 and all(
            isinstance(part, hy.models.Symbol) for part in form
        ) and [str(part) for part in list(form)[:3]] == [".", "pytest", "mark"]:
            return hy.mangle(str(list(form)[3]))
        case hy.models.Expression() if len(form) >= 1:
            name = _mark_name(list(form)[0])
            if name in _COLLECTION_SHAPING_MARKS:
                raise _NotStatic(hy.repr(form))
            return name
        case _:
            raise _NotStatic(hy.repr(form))


def record_module_binding(head: str, args: Sequence[object]) -> hy.models.Expression | None:
    """The entry to add to the expansion the record of the module-level ``(val pytestmark …)`` / ``(var pytestmark …)`` marks (returns the expression to add).
    Other bindings are not recorded (None)."""
    if head not in ("val", "var") or len(args) < 2:
        return None
    if not isinstance(args[0], hy.models.Symbol) or hy.mangle(str(args[0])) != "pytestmark":
        return None
    value = args[-1]
    try:
        match value:
            case hy.models.List() | hy.models.Tuple():
                forms = list(value)
            case _:
                forms = [value]
        names = tuple(_mark_name(form) for form in forms)
    except _NotStatic as exc:
        return _record_form(Dynamic("pytestmark", str(exc)))
    return _record_form(ModuleMarks(names))


# ---------------------------------------------------------------------------
# Reading side (called by the plugin at collection)
# ---------------------------------------------------------------------------


def encode_records(records: Iterable[Record]) -> list[str]:
    """Save also the real values filled in after import in the same record format as the macro."""
    return [json.dumps(_encode(record), ensure_ascii=False, sort_keys=True) for record in records]


def decode_records(texts: Iterable[object]) -> list[Record]:
    """Read a sequence of record JSON strings (pushed onto the module, or saved in the plugin's cache) into types.

    If the shape differs, ``MalformedRecord`` (the caller imports that file and collects).
    """
    try:
        return [_decode(json.loads(_text(text))) for text in texts]
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise MalformedRecord(str(exc)) from exc


def module_record_texts(module: types.ModuleType) -> list[str]:
    """The records (JSON strings) that the expansion expressions pushed onto the imported module. Empty for a module that uses none of the record-making macros."""
    items = vars(module).get(ITEMS_ATTR, [])
    if not isinstance(items, list) or not all(isinstance(t, str) for t in items):
        raise MalformedRecord(f"{module.__name__}.{ITEMS_ATTR} is not a list of strings")
    return list(items)
