"""In the body of a defk ``match``, convert keyword field names of class patterns to Python attribute names (``hy.mangle``).

Hy 1.3.1's ``match`` emits the keyword field names of a class pattern into Python's ``case`` without mangling them
(``compile_pattern`` in hy/core/result_macros.py -- ``kwd_attrs=[kwd.name for kwd in keywords]``).
``(match r (Rec :ended-reason None) ...)`` becomes ``case Rec(ended-reason=None):``, and since no value has an attribute
``ended-reason``, that clause never matches whatever the value is and silently falls through to the next clause. The Hy field name
``ended-reason`` becomes the attribute ``ended_reason`` in defclass / defrecord, so if the pattern side is aligned with the same
``hy.mangle``, users can keep writing Hy's natural spelling ``(Rec :ended-reason None)``.

We do not fix Hy itself (no fork, no monkeypatch, no change to the pinned hy version -- ADR-DOE-HY-008
"i said no. i asked you to make workaround and report me"). What we fix is the body of defk (including arguments and contracts) and the doeff macros
that build ``match`` in their expansion (the on-raise handling and the doeff-events handling -- a match born after defk's rewrite);
a ``match`` outside defk is stopped by DOEFF169 of doeff-linter (the rule does not apply inside a defk form).

Only the keyword field names of class patterns are fixed. We do not touch a keyword as a value pattern (``:a-b`` in ``(Rec :kind :a-b)``),
clause bodies and the guard expression after ``:if``, anything inside quote / quasiquote, or the keys of mapping patterns. Unchanged subtrees are
returned as the same object (position information is kept as is).
"""

from collections.abc import Iterator
from typing import TypeVar

from hy import mangle
from hy.models import Dict, Expression, Keyword, List, Object, Sequence, Set, Symbol, Tuple

# Form types that get rebuilt (Expression, List, Tuple, Set, Dict -- rebuilding returns the same type).
_Form = TypeVar("_Form", bound=Sequence)

# Heads of forms whose inside is not walked as code (a quoted form is a value and is not compiled as a Hy match).
_QUOTING_HEADS = frozenset({"quote", "quasiquote"})
# Heads of parenthesized forms inside a pattern that are not class patterns (value reference, or pattern, `#*` / `#**`).
_NOT_CLASS_HEADS = frozenset({".", "|", "unpack-iterable", "unpack-mapping"})


def mangle_match_fields(form: Object) -> Object:
    """Return the form with the keyword field names of the class patterns of every ``match`` inside it ``hy.mangle``d."""
    return _code(form)


def _head(node: Expression) -> str | None:
    """Spelling of the head symbol of a parenthesized form (None if the head is not a symbol)."""
    match tuple(node):
        case (Symbol() as head, *_):
            return str(head)
        case _:
            return None


def _code(node: Object) -> Object:
    """Walk a form in code position (when a ``match`` form is found, fix the patterns of its clauses)."""
    match node:
        case Expression() if _head(node) in _QUOTING_HEADS:
            return node
        case Expression() if _head(node) == "match":
            return _match_form(node)
        case Expression() | List() | Tuple() | Set() | Dict():
            return _rebuilt(node, [_code(item) for item in node])
        case _:
            return node


def _match_form(node: Expression) -> Expression:
    """``(match subject clause ...)`` -- walk the subject as code and fix the clauses with ``_match_clauses``."""
    match tuple(node):
        case (head, subject, *clauses):
            return _rebuilt(node, [head, _code(subject), *_match_clauses(clauses)])
        case _:
            return node


def _match_clauses(forms: list[Object]) -> Iterator[Object]:
    """Fix in order the clause sequence ``pattern [:as name] [:if guard] body ...`` and return it (pattern via ``_pattern``, guard and body via ``_code``)."""
    at = 0
    while at < len(forms):
        yield _pattern(forms[at])
        at += 1
        if _is_keyword(forms, at, ":as"):
            yield from forms[at : at + 2]
            at += 2
        if _is_keyword(forms, at, ":if"):
            yield forms[at]
            yield from map(_code, forms[at + 1 : at + 2])
            at += 2
        yield from map(_code, forms[at : at + 1])
        at += 1


def _is_keyword(items: list[Object], at: int, spelling: str) -> bool:
    """Whether item number ``at`` of items is a keyword spelled ``spelling``."""
    return at < len(items) and isinstance(items[at], Keyword) and str(items[at]) == spelling


def _is_class_pattern(node: Expression) -> bool:
    """Whether this is a class pattern of the form ``(Rec ...)`` / ``(mod.Rec ...)`` (not a value reference, or pattern, or unpack)."""
    match tuple(node):
        case (Symbol() as head, *_):
            return str(head) not in _NOT_CLASS_HEADS
        case (Expression() as head, *_):
            return _head(head) == "."
        case _:
            return False


def _pattern(node: Object) -> Object:
    """Fix one pattern (also follows nested sequence, mapping, or pattern, and class pattern arguments)."""
    match node:
        case Expression() if _is_class_pattern(node):
            return _class_pattern(node)
        case Expression() if _head(node) == "|":
            return _rebuilt(node, [node[0], *(_pattern(item) for item in node[1:])])
        case List() | Tuple() | Dict():
            return _rebuilt(node, [_pattern(item) for item in node])
        case _:
            return node


def _class_pattern(node: Expression) -> Expression:
    """``(Rec positional-pattern ... :field pattern ...)`` -- mangle the keyword field names and follow the argument patterns."""
    return _rebuilt(node, [node[0], *_class_arguments(list(node[1:]))])


def _class_arguments(forms: list[Object]) -> Iterator[Object]:
    """Fix the arguments of a class pattern in order and return them.

    What follows a keyword is always read as the pattern of that field (``:a-b`` in ``(Rec :kind :a-b)`` is a value pattern and is not fixed).
    ``:as name`` goes through the same reading (``as`` stays ``as`` when mangled, and the name is a symbol pattern left as is).
    """
    at = 0
    while at < len(forms):
        item = forms[at]
        if isinstance(item, Keyword) and at + 1 < len(forms):
            yield _field_name(item)
            yield _pattern(forms[at + 1])
            at += 2
        else:
            yield _pattern(item)
            at += 1


def _field_name(keyword: Keyword) -> Keyword:
    """Turn a field-name keyword into a Python attribute name (the same object if unchanged)."""
    name = mangle(keyword.name)
    if name == keyword.name:
        return keyword
    renamed = Keyword(name)
    renamed.replace(keyword)  # copy the position (line, column) from the original keyword (replace writes into itself)
    return renamed


def _rebuilt(node: _Form, items: list[Object]) -> _Form:
    """Rebuild a form of the same type only when at least one child changed (copy the position from the original form)."""
    if len(items) == len(node) and all(new is old for new, old in zip(items, node, strict=True)):
        return node
    rebuilt = type(node)(items)
    rebuilt.replace(node, recursive=False)  # children keep their own positions, so only this form's position is copied
    return rebuilt
