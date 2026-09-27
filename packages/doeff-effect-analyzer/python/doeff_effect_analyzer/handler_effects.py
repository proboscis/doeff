"""Which effects a handler handles, which effects its clauses perform, and whether an
env (an ordered handler stack) covers every effect a Program performs.

Read from source like :mod:`doeff_effect_analyzer.program_effects` (Hy is
macro-expanded first, names resolve by importing the defining module; nothing is
run).

A handler clause is recognised by its shape, which ``defhandler`` (doeff-hy) and
hand-written Python handlers share: a function (or lambda) of two or more
parameters whose body tests ``isinstance(effect, T)`` on one of them (``T`` a
class or a tuple of classes; negated tests and ``match effect: case T(...)`` are
understood too).  The branch taken when the test holds is the clause for ``T``;
the effects it performs (followed through Program calls) are what the clause
emits outward.

``analyze_handler`` accepts a handler factory (``(defhandler h [args] ...)``,
``def reader(env): ...``), a clause function, or a handler value made by
``defhandler`` / ``doeff.handler`` (``__doeff_handler_data__``).  A factory that
returns ``doeff.handler(dispatch)`` / ``functools.partial(dispatch, …)`` / a
method of an object it builds (``handler(runtime.handle)``, or an attribute its
``__init__`` sets to one) is followed to the dispatch function.  When the clauses
cannot be read, a factory (or handler value) may declare them:

    factory.__doeff_handles__ = (DelayEffect, GetTimeEffect)  # effects it answers
    factory.__doeff_effects__ = (Spawn,)                      # effects its clauses perform

Every answer says which decided it (``HandlerEffects.basis``: ``clauses`` /
``declared`` / ``unread``).  Both marks are needed: a handler that declares what it
answers but not what it performs stays ``unread``.

``analyze_env`` reads an env builder — a function (plain, ``defk`` or ``deff``)
that returns a handler list, outermost first: a list literal, through the names
it was bound to (``_contract_result``, ``val``, ``(<- base list (f))``),
``[*base, h]`` and ``base + [h]``, and calls to other builders.
``check_coverage`` runs a Program's effects through it from the innermost handler
outward; ``residual`` is what leaves.
"""

import ast
import contextvars
import dataclasses
import functools
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import FunctionType
from typing import Any

from doeff_effect_analyzer.program_effects import (
    UNBOUND,
    Basis,
    Clause,
    EffectUse,
    Escape,
    FunctionNode,
    HandlerEffects,
    Location,
    ProgramEffects,
    Residual,
    Unresolved,
    _body_nodes,
    _Bound,
    _bound_operand,
    _call_bindings,
    _do_decorator,
    _Facts,
    _function_of,
    _handler_wrapper_function,
    _is_effect_class,
    _is_installer,
    _locate,
    _location_of,
    _no_carrier,
    _Reader,
    _report_facts,
    _Scope,
    _scope_of,
    bindings_for,
    pass_through,
    qualified_name,
    resolve_target,
)


@dataclass(frozen=True)
class Gap:
    """An effect no handler in the env handles.  ``origin`` = who performs it."""

    effect: type
    origin: str

    def __str__(self) -> str:
        return f"{self.effect.__name__} (performed by {self.origin})"


@dataclass(frozen=True)
class Coverage:
    """Whether an env answers every effect a Program performs, and what stops the answer
    from being certain."""

    gaps: tuple[Gap, ...]
    unknown_handlers: tuple[str, ...]
    unresolved: tuple[Unresolved, ...] = ()

    @property
    def complete(self) -> bool:
        """No gap, every handler on the way was read, and every place in the Program was
        followed (an unread handler or an unfollowed place could hide a gap)."""
        return not self.gaps and not self.unknown_handlers and not self.unresolved


# --------------------------------------------------------------------------- handlers


def analyze_handler(target: Any, *, name: str | None = None) -> HandlerEffects:
    """What a handler (factory, value or dispatcher; or ``"module:attr"``) handles and performs."""
    obj = resolve_target(target) if isinstance(target, str) else target
    label = name or (target if isinstance(target, str) else _label_of(obj))
    return _analyze_handler(obj, label, depth=0)


_MAX_FACTORY_HOPS = 3

# Handler functions being read further up: a clause that installs its own handler
# again must not read it forever.
_READING: contextvars.ContextVar[frozenset[Any]] = contextvars.ContextVar(
    "doeff_effect_analyzer_reading", default=frozenset()
)


def _analyze_handler(obj: Any, label: str, *, depth: int) -> HandlerEffects:
    """Clauses read from source, else the declaration marks — with the basis kept."""
    declared = _declared(obj, label)
    function = _handler_function(obj)
    if function is None:
        read = _unread(label, Unresolved("not a handler or handler factory", repr(obj), _nowhere()))
        return _decide(read, declared)
    if function in _READING.get():
        return _unread(
            label,
            Unresolved(
                "handler installs itself inside its own clause",
                function.__qualname__,
                _location_of(function),
            ),
        )
    token = _READING.set(_READING.get() | {function})
    try:
        read = _read_function(function, label, depth=depth)
    finally:
        _READING.reset(token)
    return _decide(read, declared)


def _read_function(function: Any, label: str, *, depth: int) -> HandlerEffects:
    """The clauses in ``function``'s source, following a factory to the dispatcher it returns."""
    located = _locate(function)
    if isinstance(located, Unresolved):
        return _unread(label, located)
    root, scope, filename = located.node, located.scope, located.filename
    read = _clauses_in(root, scope, filename, label, _Reader())
    if read.clauses:
        return HandlerEffects(label, read.clauses, read.unresolved, Basis.CLAUSES)
    if depth < _MAX_FACTORY_HOPS:
        # A factory that returns a dispatch function (by reference, as
        # functools.partial(dispatch, ...) or doeff.handler(dispatch)), or a handler
        # that returns dispatch(..., effect, k) — read the dispatch function.
        for returned in _returned_handlers(root, scope):
            followed = _analyze_handler(returned, label, depth=depth + 1)
            if followed.known:
                return followed
    return _unread(
        label,
        *read.unresolved,
        Unresolved("no isinstance(effect, T) clause found", label, _location_of(function)),
    )


def _decide(read: HandlerEffects, declared: HandlerEffects | None) -> HandlerEffects:
    """Clauses read from source win; declarations are used when nothing could be read.
    A declaration that disagrees with the clauses read is reported, not ignored."""
    if read.known:
        if declared is not None and declared.known and declared.handled != read.handled:
            note = Unresolved(
                "declared __doeff_handles__ differs from the clauses read",
                f"declared {_names(declared.handled)} / read {_names(read.handled)}",
                read.clauses[0].location,
            )
            return dataclasses.replace(read, unresolved=(*read.unresolved, note))
        return read
    if declared is not None:
        return dataclasses.replace(declared, unresolved=(*read.unresolved, *declared.unresolved))
    return read


def _names(classes: Iterable[type]) -> str:
    """Short class names for a report line."""
    return "[" + ", ".join(sorted(cls.__name__ for cls in classes)) + "]"


def _unread(label: str, *unresolved: Unresolved) -> HandlerEffects:
    """A handler whose coverage is unknown, with the reasons."""
    return HandlerEffects(label, (), tuple(unresolved), Basis.UNREAD)


def _declared(obj: Any, label: str) -> HandlerEffects | None:
    """The clauses a handler (or its factory) declares with ``__doeff_handles__``."""
    holder = next(
        (c for c in _mark_holders(obj) if getattr(c, "__doeff_handles__", None) is not None), None
    )
    if holder is None:
        return None
    function = _function_of(holder)
    location = _location_of(function) if function is not None else _nowhere()
    handled = _declared_classes(holder, "__doeff_handles__", location)
    if getattr(holder, "__doeff_effects__", None) is None:
        missing = Unresolved(
            "declares __doeff_handles__ but not __doeff_effects__ (what its clauses perform)",
            label,
            location,
        )
        return _unread(label, *handled.problems, missing)
    performed = _declared_classes(holder, "__doeff_effects__", location)
    emits = ProgramEffects(
        target=f"{label} (declared)",
        effects=tuple(EffectUse(effect, location) for effect in performed.found),
    )
    clauses = tuple(Clause(cls, emits, location) for cls in handled.found)
    return HandlerEffects(
        label,
        clauses,
        (*handled.problems, *performed.problems),
        Basis.DECLARED if clauses else Basis.UNREAD,
    )


@dataclass(frozen=True)
class _DeclaredClasses:
    found: tuple[type, ...]
    problems: tuple[Unresolved, ...]


def _declared_classes(holder: Any, attribute: str, location: Location) -> _DeclaredClasses:
    """The effect classes a mark names, and a problem for anything else it names."""
    value = getattr(holder, attribute)
    items = list(value) if isinstance(value, (tuple, list, frozenset, set)) else [value]
    return _DeclaredClasses(
        found=tuple(item for item in items if _is_effect_class(item)),
        problems=tuple(
            Unresolved(
                f"{attribute} names something that is not an effect class", repr(item), location
            )
            for item in items
            if not _is_effect_class(item)
        ),
    )


def _mark_holders(obj: Any) -> Iterator[Any]:
    """``obj``, the dispatcher it installs, and what they wrap (where a mark may sit)."""
    seen: set[int] = set()
    pending = [obj]
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, functools.partial):
            pending.append(current.func)
        pending.append(getattr(current, "__doeff_handler_data__", None))
        pending.append(getattr(current, "__wrapped__", None))


def _returned_handlers(root: FunctionNode, scope: _Scope) -> Iterator[Any]:
    wrappers = (functools.partial, _handler_wrapper_function())
    for child in _body_nodes(root):
        if not isinstance(child, ast.Return) or child.value is None:
            continue
        expr = child.value
        if isinstance(expr, ast.Name) and expr.id in scope.local_calls:
            expr = scope.local_calls[expr.id]  # Hy: `_hy_anon = f(...)` then `return _hy_anon`
        if isinstance(expr, ast.Call):
            head = scope.resolve(expr.func)
            if head is UNBOUND:
                continue
            # partial(dispatch, ...) / handler(dispatch) → dispatch;
            # dispatch(..., effect, k) → dispatch
            is_wrapper = any(head is wrapper for wrapper in wrappers)
            expr = expr.args[0] if is_wrapper and expr.args else expr.func
        value = scope.resolve(expr)
        if value is not UNBOUND and _function_of(value) is not None:
            yield value


def _label_of(obj: Any) -> str:
    name = getattr(obj, "__doeff_name__", None)
    if isinstance(name, str):
        return name
    function = _handler_function(obj)
    return qualified_name(function) if function is not None else repr(obj)


def _nowhere() -> Location:
    return Location("<unknown>", 0)


def _handler_function(obj: Any) -> Any:
    data = getattr(obj, "__doeff_handler_data__", None)
    if data is not None:
        function = _function_of(data)
        if function is not None:
            return function
    return _function_of(obj)


def _clause_functions(
    root: FunctionNode,
) -> Iterator[tuple[FunctionNode, tuple[FunctionNode, ...]]]:
    """``root`` and the functions nested in it that dispatch on a parameter."""

    def walk(
        node: FunctionNode, enclosing: tuple[FunctionNode, ...]
    ) -> Iterator[tuple[FunctionNode, tuple[FunctionNode, ...]]]:
        if _effect_param(node) is not None:
            yield node, enclosing
        for child in ast.walk(node):
            if child is node or not isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            if _directly_nested(node, child):
                yield from walk(child, (*enclosing, node))

    yield from walk(root, ())


def _directly_nested(parent: FunctionNode, child: FunctionNode) -> bool:
    return any(node is child for node in _body_nodes(parent))


def _effect_param(node: FunctionNode) -> str | None:
    """The parameter the body dispatches on (``isinstance(p, T)`` / ``match p``)."""
    positional = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)]
    if len(positional) < 2:
        return None
    for param in positional:
        if any(_isinstance_target(test, param) is not None for test in _tests_in(node)):
            return param
        if any(
            isinstance(child, ast.Match) and _is_name(child.subject, param)
            for child in _body_nodes(node)
        ):
            return param
    return None


def _tests_in(node: FunctionNode) -> Iterator[ast.expr]:
    for child in _body_nodes(node):
        if isinstance(child, (ast.If, ast.IfExp)):
            yield child.test


def _is_name(expr: ast.expr, name: str | None) -> bool:
    return isinstance(expr, ast.Name) and expr.id == name


@dataclass(frozen=True)
class _IsInstance:
    classes: ast.expr
    negated: bool


def _isinstance_target(test: ast.expr, param: str | None) -> _IsInstance | None:
    """``isinstance(param, X)`` in ``test`` (possibly inside ``and`` / ``not``)."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _isinstance_target(test.operand, param)
        return None if inner is None else _IsInstance(inner.classes, not inner.negated)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        for value in test.values:
            found = _isinstance_target(value, param)
            if found is not None:
                return found
        return None
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Name)
        and test.func.id == "isinstance"
        and len(test.args) == 2
        and _is_name(test.args[0], param)
    ):
        return _IsInstance(test.args[1], negated=False)
    return None


@dataclass(frozen=True)
class _NamedClasses:
    found: list[type]
    missing: list[str]


def _classes(expr: ast.expr, scope: _Scope) -> _NamedClasses:
    """Effect classes named by ``expr`` (a class, a tuple of them, or a name bound to one)."""
    if isinstance(expr, ast.Tuple):
        parts = [_classes(element, scope) for element in expr.elts]
        return _NamedClasses(
            found=[cls for part in parts for cls in part.found],
            missing=[text for part in parts for text in part.missing],
        )
    value = scope.resolve(expr)
    if _is_effect_class(value):
        return _NamedClasses([value], [])
    if isinstance(value, tuple) and value and all(_is_effect_class(v) for v in value):
        return _NamedClasses(list(value), [])
    return _NamedClasses([], [ast.unparse(expr)])


@dataclass(frozen=True)
class _ReadClauses:
    clauses: tuple[Clause, ...]
    unresolved: tuple[Unresolved, ...]


def _clauses_in(
    root: FunctionNode, root_scope: _Scope, filename: str, label: str, reader: _Reader
) -> _ReadClauses:
    """The clauses of ``root`` and of the dispatch functions nested in it."""
    clauses: list[Clause] = []
    unresolved: list[Unresolved] = []
    for clause_fn, enclosing in _clause_functions(root):
        scope = root_scope
        for outer in enclosing[1:]:
            scope = _scope_of(outer, root_scope.module, parent=scope)
        if clause_fn is not root:
            scope = _scope_of(clause_fn, root_scope.module, parent=scope)
        read = _read_clauses(reader, clause_fn, scope, filename, label)
        clauses.extend(read.clauses)
        unresolved.extend(read.unresolved)
    return _ReadClauses(tuple(clauses), tuple(unresolved))


def _read_clauses(
    reader: _Reader, node: FunctionNode, scope: _Scope, filename: str, label: str
) -> _ReadClauses:
    param = _effect_param(node)
    clauses: list[Clause] = []
    unresolved: list[Unresolved] = []
    everything = list(_body_nodes(node))
    for child in everything:
        branches = _branches(child, param, everything)
        for classes_expr, region in branches:
            location = Location(filename, getattr(child, "lineno", 0))
            named = _classes(classes_expr, scope)
            unresolved.extend(
                Unresolved("handled class is not an importable effect class", text, location)
                for text in named.missing
            )
            facts = _Facts()
            reader.collect(region, scope, filename, facts, generator=True)
            emits = _report_facts(reader, facts, label=f"{label} clause")
            clauses.extend(
                Clause(handles=cls, emits=emits, location=location) for cls in named.found
            )
    return _ReadClauses(tuple(clauses), tuple(unresolved))


def _branches(
    child: ast.AST, param: str | None, everything: list[ast.AST]
) -> list[tuple[ast.expr, list[ast.AST]]]:
    """(classes expression, nodes run when the effect is of those classes)."""
    if isinstance(child, ast.If):
        return _if_branches(child, param, everything)
    if isinstance(child, ast.IfExp):
        found = _isinstance_target(child.test, param)
        if found is None:
            return []
        branch = child.orelse if found.negated else child.body
        return [(found.classes, list(ast.walk(branch)))]
    if isinstance(child, ast.Match) and _is_name(child.subject, param):
        return [
            (case.pattern.cls, _region(case.body))
            for case in child.cases
            if isinstance(case.pattern, ast.MatchClass)
        ]
    return []


def _if_branches(
    child: ast.If, param: str | None, everything: list[ast.AST]
) -> list[tuple[ast.expr, list[ast.AST]]]:
    found = _isinstance_target(child.test, param)
    if found is None:
        return []
    taken = _region(child.body)
    if not found.negated:
        return [(found.classes, taken)]
    # `if not isinstance(effect, T): <pass it on>` — the rest of the body handles T.
    skipped = {id(node) for node in taken}
    return [(found.classes, [n for n in everything if id(n) not in skipped])]


def _region(statements: Iterable[ast.stmt]) -> list[ast.AST]:
    out: list[ast.AST] = []
    for statement in statements:
        out.append(statement)
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        stack = list(ast.iter_child_nodes(statement))
        while stack:
            node = stack.pop()
            out.append(node)
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                stack.extend(ast.iter_child_nodes(node))
    return out


# --------------------------------------------------------------------------- handler expressions


def _inline_handler(
    node: FunctionNode, scope: _Scope, filename: str, label: str, location: Location
) -> HandlerEffects:
    """The clauses of a dispatcher written in a body (``handle``'s lambda or local def)."""
    # A clause that runs the Program installing this very handler again would read
    # it forever (each handler is read with a fresh reader) — stop at the second visit.
    if node in _READING.get():
        return _unread(
            label, Unresolved("handler installs itself inside its own clause", label, location)
        )
    token = _READING.set(_READING.get() | {node})
    try:
        read = _clauses_in(
            node, _scope_of(node, scope.module, parent=scope), filename, label, _Reader()
        )
    finally:
        _READING.reset(token)
    if read.clauses:
        return HandlerEffects(label, read.clauses, read.unresolved, Basis.CLAUSES)
    return _unread(
        label,
        *read.unresolved,
        Unresolved("no isinstance(effect, T) clause found", label, location),
    )


def raw_handler_of(expr: ast.expr, scope: _Scope, filename: str, label: str) -> HandlerEffects:
    """A raw dispatcher ``(effect, k)`` written in a body: ``WithHandler(h, body)``'s ``h``
    (``handle`` expands to ``_doeff_do(lambda effect, k: …)`` or to a local def)."""
    wrappers = (_do_decorator(), _handler_wrapper_function())
    while (
        isinstance(expr, ast.Call)
        and len(expr.args) == 1
        and not expr.keywords
        and any(scope.resolve(expr.func) is wrapper for wrapper in wrappers)
    ):
        expr = expr.args[0]
    location = Location(filename, getattr(expr, "lineno", 0))
    node: FunctionNode | None = None
    if isinstance(expr, ast.Lambda):
        node = expr
    elif isinstance(expr, ast.Name) and scope.resolve(expr) is UNBOUND:
        node = scope.local_functions.get(expr.id)
    if node is not None:
        return _inline_handler(node, scope, filename, label, location)
    value = scope.resolve(expr)
    if value is UNBOUND:
        return _unread(
            label, Unresolved("handler is not a module-level name", ast.unparse(expr), location)
        )
    return analyze_handler(value, name=label)


def element_of(
    element: ast.expr, scope: _Scope, filename: str, *, depth: int = 0
) -> HandlerEffects:
    """The handler one element of a handler list denotes (a factory call or a value)."""
    text = ast.unparse(element)
    location = Location(filename, getattr(element, "lineno", 0))
    if isinstance(element, ast.Lambda):
        return raw_handler_of(element, scope, filename, text)
    if (
        isinstance(element, ast.Name)
        and scope.resolve(element) is UNBOUND
        and depth < _MAX_LIST_HOPS
    ):
        if element.id in scope.local_values:  # h = (reader {...}) … [h]
            return element_of(scope.local_values[element.id], scope, filename, depth=depth + 1)
        if element.id in scope.local_functions:  # a dispatch function defined in the body
            return raw_handler_of(element, scope, filename, text)
    head = element.func if isinstance(element, ast.Call) else element
    value = scope.resolve(head)
    if value is UNBOUND:
        return _unread(text, Unresolved("handler is not a module-level name", text, location))
    if isinstance(element, ast.Call) and value is _handler_wrapper_function() and element.args:
        return raw_handler_of(element.args[0], scope, filename, text)
    return _element_handler(element, value, scope, text)


def _element_handler(element: ast.expr, value: Any, scope: _Scope, text: str) -> HandlerEffects:
    """The handler an env element denotes.

    ``(wrap (make-handler …))``: when ``wrap`` has no clauses of its own and its
    first argument is itself a handler factory call, the wrapped handler is read
    (``wrap`` only adapts it — e.g. gives a ``functools.partial`` a name).
    """
    direct = analyze_handler(value, name=text)
    if direct.known or not isinstance(element, ast.Call) or not element.args:
        return direct
    inner = element.args[0]
    if not isinstance(inner, ast.Call):
        return direct
    inner_value = scope.resolve(inner.func)
    if inner_value is UNBOUND:
        return direct
    wrapped = analyze_handler(inner_value, name=text)
    return wrapped if wrapped.known else direct


_MAX_LIST_HOPS = 6


def stack_of(
    expr: ast.expr, scope: _Scope, filename: str, *, depth: int = 0
) -> list[HandlerEffects]:
    """The handlers a handler-list expression denotes, outermost first.

    A list / tuple literal (``*spread`` elements included), ``a + b``, a name bound
    once to one of these (or to ``yield builder()`` — ``(<- base list (builder))``),
    a call to a builder function, or a bound / module-level list of handler values.
    What cannot be read becomes an ``unread`` entry (it may hide a gap).
    """
    text = ast.unparse(expr)
    location = Location(filename, getattr(expr, "lineno", 0))
    if depth > _MAX_LIST_HOPS:
        return [_unread(text, Unresolved("handler list is too indirect to follow", text, location))]
    deeper = depth + 1
    stack: list[HandlerEffects]
    match expr:
        case ast.List(elts=elements) | ast.Tuple(elts=elements):
            stack = [
                handler
                for element in elements
                for handler in (
                    stack_of(element.value, scope, filename, depth=deeper)
                    if isinstance(element, ast.Starred)
                    else [element_of(element, scope, filename)]
                )
            ]
        case ast.BinOp(left=left, op=ast.Add(), right=right):
            stack = [
                *stack_of(left, scope, filename, depth=deeper),
                *stack_of(right, scope, filename, depth=deeper),
            ]
        case (
            ast.Yield(value=ast.expr() as inner)
            | ast.YieldFrom(value=inner)
            | ast.Await(value=inner)
        ):
            stack = stack_of(_bound_operand(inner, scope), scope, filename, depth=deeper)
        case ast.Name(id=name) if scope.resolve(expr) is UNBOUND and name in scope.local_values:
            stack = stack_of(scope.local_values[name], scope, filename, depth=deeper)
        case ast.Call(func=func) if (builder := _builder_function(scope.resolve(func))) is not None:
            stack = _env_of(builder, _call_bindings(builder, expr, scope), text, depth=deeper)
        case _:
            value = scope.resolve(expr)
            stack = (
                [analyze_handler(item) for item in value]
                if isinstance(value, (list, tuple))
                else [_unread(text, Unresolved("handler list could not be read", text, location))]
            )
    return stack


def _builder_function(value: Any) -> FunctionType | None:
    """A function that builds a handler list (not a class, not a handler value)."""
    if value is UNBOUND or isinstance(value, type) or _is_installer(value):
        return None
    return _function_of(value)


def _env_of(
    function: FunctionType, bound: _Bound, text: str, *, depth: int
) -> list[HandlerEffects]:
    """The handler list a builder called inside another list returns (unread when it cannot be)."""
    located = _locate(function, bound)
    if isinstance(located, Unresolved):
        return [_unread(text, located)]
    node, scope, filename = located.node, located.scope, located.filename
    returned = _returned_list(node)
    if returned is None:
        return [
            _unread(
                text,
                Unresolved(
                    "builder does not return a handler list",
                    function.__qualname__,
                    _location_of(function),
                ),
            )
        ]
    return stack_of(returned, scope, filename, depth=depth)


def _returned_list(node: FunctionNode) -> ast.expr | None:
    """What a builder returns (its last ``return``; a lambda's body)."""
    if isinstance(node, ast.Lambda):
        return node.body
    returns = [
        child.value
        for child in _body_nodes(node)
        if isinstance(child, ast.Return) and child.value is not None
    ]
    return returns[-1] if returns else None


# --------------------------------------------------------------------------- env


def analyze_env(builder: Any, *, bindings: Mapping[str, Any] | None = None) -> list[HandlerEffects]:
    """Handlers of an env builder, outermost first.

    ``(defn env [config ctx] [(h1) (h2 x) h3])``, and the same written with ``defk`` /
    ``deff`` (the list reaches ``return`` through ``_contract_result`` and ``val``
    names); ``bindings`` binds the builder's parameters.  Each element is a handler
    factory call or a handler value; ``*base`` spreads another list.
    """
    obj = resolve_target(builder) if isinstance(builder, str) else builder
    function = _function_of(obj)
    if function is None:
        raise TypeError(f"{builder!r} is not a function")
    located = _locate(function, bindings_for(function, keywords=bindings))
    if isinstance(located, Unresolved):
        raise ValueError(f"{function.__qualname__}: {located.reason}: {located.text}")
    node, scope, filename = located.node, located.scope, located.filename
    returned = _returned_list(node)
    if returned is None:
        raise ValueError(f"{function.__qualname__} does not return a handler list")
    return stack_of(returned, scope, filename)


# --------------------------------------------------------------------------- coverage


def residual(
    program: ProgramEffects,
    env: Sequence[HandlerEffects] = (),
    *,
    include: Callable[[Any], bool] = _no_carrier,
) -> Residual:
    """What leaves ``program``: its effects after the handlers it installs itself
    (``with_handlers`` / ``with-handler`` / ``handle`` in its body), then after ``env``
    (outermost first).  ``include`` folds in carried Programs by carrier."""
    return pass_through(program.residual_with(include), env, include)


def check_coverage(
    effects: Iterable[type] | ProgramEffects | Residual,
    env: Sequence[HandlerEffects],
    *,
    origin: str = "program",
    include: Callable[[Any], bool] = _no_carrier,
) -> Coverage:
    """Run ``effects`` through ``env`` (outermost first) from the innermost handler out.

    A handled effect is replaced by the effects its clause performs; whatever is
    left after the outermost handler is a gap.  A clause keyed on a parent class
    also handles its subclasses (``isinstance`` semantics).  A Program's own
    handlers (``with_handlers`` in its body) are applied first.  A handler that
    could not be read — in the env or inside the Program — and a place in the
    Program the reader could not follow (``unresolved``) make coverage incomplete.
    """
    if isinstance(effects, ProgramEffects):
        origin = effects.target if origin == "program" else origin
        inner = effects.residual_with(include)
    elif isinstance(effects, Residual):
        inner = effects
    else:
        inner = Residual(tuple(Escape(effect, EffectUse(effect, _nowhere())) for effect in effects))
    out = pass_through(inner, env, include)
    gaps = sorted(
        (Gap(escape.effect, escape.by or origin) for escape in out.escapes),
        key=lambda gap: (gap.effect.__name__, gap.origin),
    )
    return Coverage(
        gaps=tuple(gaps), unknown_handlers=out.unknown_handlers, unresolved=out.unresolved
    )
