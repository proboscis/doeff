"""Map a type expression to a form that can be passed as the second argument of ``isinstance`` at run time -- the one place
where the contract ``(: x T)`` and the binding ``(<- x T e)`` check types at run time.

The contract check in macros.hy and the expansion of the ``<-`` binding call this function. External tools that translate Hy
files to Python and type-check them (the Hy projection of the quality checker) also call it when mirroring the run-time
guarantee of a binding, so they do not carry a second copy of the rules (root 2 of #3366 -- the copy had no rules, left
``(get tuple #(int int))`` as written in ``isinstance``, and flagged as a type error a binding that passes at run time).

- ``None``: in a type annotation it means "the type of the value None" (PEP 484), but ``None`` cannot be passed as the second
  argument of ``isinstance`` (``TypeError: isinstance() arg 2 must be a type ...``). ``(: % None)`` used to raise a type error
  at run time (measured in ``clock-driver`` of ``sim_clock.hy`` on 2026-09-23), so it is mapped to ``None.__class__``
  (NoneType). The same applies inside a tuple ``#(int None)``.
  The target avoids looking up a name -- the expansion is placed inside the user's function, so calling the bare name ``type``
  would be shadowed by a local name ``type`` (e.g. ``(val type (.get value "type"))``) and raise TypeError (#1825). An
  attribute of the constant None is not shadowed by local names.
  The earlier ``hy.I.types.NoneType`` went through hy.__getattr__ -> slashes2dots on every check and used about 4% of the profile (#1845).
- Generic types with element types ``(get tuple #(X ...))`` / ``(of tuple X ...)`` / ``(of dict K V)`` / ``(get dict #(K V))``:
  ``isinstance`` does not accept them (``TypeError: isinstance() argument 2 cannot be a parameterized generic``), so they are
  mapped to the outer type (tuple, dict). Only the outer type is checked at run time; element types are left to the static
  type checker (the annotations of doeff-hy-check) (the decision of #1790: checking the elements at run time would walk every
  element on each check and would also require recursion over nested types).
- ``(| A B)``: mapped to the tuple ``#(A B)`` (generic types and ``None`` inside are mapped by the rules above: ``(| (get tuple #(str ...)) None)`` becomes
  ``#(tuple (. None __class__))``). ``isinstance`` accepts a tuple and a union alike, but a union rebuilds ``A | B`` on every
  contract check (``_Py_union_type_or`` -- 1.5% of the CPU of the scheduler thread of the record service),
  whereas a tuple only reads the names and lines them up. Names are read on every call, so names defined later and names of
  closures or arguments can still be resolved as before (evaluating once at definition time and remembering the result would break both, so we do not do that).
- Any other form is returned as is (the same object)."""

import types
import typing

from hy.models import Expression, Object, Sequence, Symbol, Tuple

#: Heads of generic types (``(get base ...)`` / ``(of base ...)``) -- only the outer type ``base`` is checked at run time.
_GENERIC_HEADS = frozenset({"get", "of"})


def runtime_type_form(tp: Object) -> Object:
    """Turn the type expression ``tp`` into a form that can be placed as the second argument of ``isinstance`` (rules above; an unchanged form is the same object)."""
    match tp:
        case Symbol() if str(tp) == "None":
            return Expression([Symbol("."), Symbol("None"), Symbol("__class__")])
        case Tuple():
            return Tuple([runtime_type_form(item) for item in _children(tp)])
        case Expression() if (base := _generic_base(tp)) is not None:
            return runtime_type_form(base)
        case Expression() if _head(tp) == "|" and len(tp) >= 2:
            _, *members = _children(tp)
            return Tuple([runtime_type_form(item) for item in members])
        case _:
            return tp


#: Values that can be placed as the second argument of isinstance (a type, a tuple of types, a union of types).
RuntimeClass: typing.TypeAlias = type | types.UnionType | tuple["RuntimeClass", ...]


def runtime_class(tp: object) -> RuntimeClass:
    """Map the run-time type value ``tp`` to its outer type -- called only when the expansion of a contract or binding passed a
    name (a type alias) to ``isinstance`` and got a TypeError.

    ``runtime_type_form`` maps literal generic types at expansion time, but the value of a name is unknown then. If the value of a
    name is a generic type with type arguments (``Callable[..., object]``, ``FrozenMap[object]``) or a union containing one,
    ``isinstance`` refuses it, so at run time we map it by the same rule (outer type only -- the decision of #1790). A value that
    is not a type (a number, a string, ...) is rejected with a TypeError that names what the writer put there.
    """
    if tp is None:
        return type(None)
    if isinstance(tp, type):
        return tp
    if isinstance(tp, tuple):
        members: tuple[object, ...] = tp
        return tuple(runtime_class(item) for item in members)
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        # ``Annotated[T, mark]`` (doeff's BuiltBy etc. -- a mark for static readers) is T at run time.
        return runtime_class(typing.get_args(tp)[0])
    if isinstance(tp, types.UnionType) or origin is typing.Union:
        return tuple(runtime_class(item) for item in typing.get_args(tp))
    if isinstance(origin, type):
        return origin
    raise TypeError(f"the contract type is not a type that can be checked at run time (not a type, or a form without an outer type): {tp!r}")


def _children(node: Sequence) -> tuple[Object, ...]:
    """Children of a form -- children of a Hy form are Hy models (the sequence type returns object, so narrow to model here)."""
    return tuple(_model(item) for item in node)


def _model(item: object) -> Object:
    """Accept a child of a form as a Hy model (a non-model child is a shape the reader never produces, so stop instead of silently mapping it)."""
    match item:
        case Object():
            return item
        case _:
            raise TypeError(f"a child of a Hy form is not a Hy model: {item!r}")


def _head(node: Expression) -> str | None:
    """Spelling of the head symbol of a parenthesized form (None if the head is not a symbol or the form is empty)."""
    match tuple(node):
        case (Symbol() as head, *_):
            return str(head)
        case _:
            return None


def _generic_base(node: Expression) -> Symbol | None:
    """The ``base`` of ``(get base ...)`` / ``(of base ...)`` (only when it is a symbol). None if not a generic type form."""
    match tuple(node):
        case (Symbol() as head, Symbol() as base, *_) if str(head) in _GENERIC_HEADS:
            return base
        case _:
            return None
