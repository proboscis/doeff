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
answers but not what it performs stays ``unread``.  A body wrapper that answers no
effect but performs some itself around the body (reads a start position, starts
tasks, then runs the body) declares ``__doeff_handles__ = ()``: what it declares in
``__doeff_effects__`` is its ``performs`` and goes to the handlers outside it.

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
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import FunctionType
from typing import Any

from doeff_effect_analyzer.program_effects import (
    UNBOUND,
    Basis,
    Binding,
    Clause,
    EffectUse,
    Escape,
    FunctionNode,
    HandlerEffects,
    Location,
    ProgramEffects,
    ReceivedEffect,
    Residual,
    Unresolved,
    _bind_expression,
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
    _LocalFunction,
    _locate,
    _location_of,
    _no_carrier,
    _passed_arguments,
    _ProgramAnyOf,
    _ProgramArg,
    _Reader,
    _report_facts,
    _Scope,
    _scope_of,
    _WrittenArgument,
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
    if _answers_nothing(holder):
        # ``__doeff_handles__ = ()``: a body wrapper that answers no effect — what it
        # declares it performs, it performs itself around the body (``performs``).
        return HandlerEffects(
            label, (), performed.problems, Basis.DECLARED, performs=emits
        )
    clauses = tuple(Clause(cls, emits, location) for cls in handled.found)
    return HandlerEffects(
        label,
        clauses,
        (*handled.problems, *performed.problems),
        Basis.DECLARED if clauses else Basis.UNREAD,
    )


def _answers_nothing(holder: Any) -> bool:
    """Whether ``holder`` declares with an empty ``__doeff_handles__`` that it answers no
    effect (a body wrapper) — not a declaration whose names all turned out not to be classes."""
    value = getattr(holder, "__doeff_handles__")
    return isinstance(value, (tuple, list, frozenset, set)) and len(value) == 0


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


_CLAUSE_FUNCTIONS: dict[
    int, tuple[FunctionNode, tuple[tuple[FunctionNode, tuple[FunctionNode, ...]], ...]]
] = {}


def _clause_functions(
    root: FunctionNode,
) -> tuple[tuple[FunctionNode, tuple[FunctionNode, ...]], ...]:
    """``root`` and the functions nested in it that dispatch on a parameter.

    Found once per def node (as ``_body_nodes`` lists a body once): a handler is read again
    for every Program that installs it, and the tree never changes (agora-redesign #1586)."""
    cached = _CLAUSE_FUNCTIONS.get(id(root))
    if cached is not None and cached[0] is root:
        return cached[1]
    found = tuple(_walk_clause_functions(root))
    _CLAUSE_FUNCTIONS[id(root)] = (root, found)
    return found


def _walk_clause_functions(
    root: FunctionNode,
) -> Iterator[tuple[FunctionNode, tuple[FunctionNode, ...]]]:
    """Walk the dispatchers under ``root`` for ``_clause_functions``, with the defs enclosing each
    (their scopes are rebuilt when a clause is read)."""

    def walk(
        node: FunctionNode, enclosing: tuple[FunctionNode, ...]
    ) -> Iterator[tuple[FunctionNode, tuple[FunctionNode, ...]]]:
        if _effect_param(node) is not None:
            yield node, enclosing
        for child in _nested_functions(node):
            yield from walk(child, (*enclosing, node))

    yield from walk(root, ())


def _nested_functions(parent: FunctionNode) -> Iterator[FunctionNode]:
    """The defs and lambdas written directly in ``parent``'s body, in ``ast.walk`` order.

    Breadth-first from the body like ``ast.walk``, but stopping at every def / class / lambda,
    so each level reads its own body only — walking the whole subtree at every level read a
    node once per function enclosing it (agora-redesign #1586). Leaving out the other fields
    (arguments, decorators) and the subtrees below does not reorder the nodes kept."""
    queue: deque[ast.AST] = deque([parent.body] if isinstance(parent, ast.Lambda) else parent.body)
    while queue:
        node = queue.popleft()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node
        elif not isinstance(node, ast.ClassDef):
            queue.extend(ast.iter_child_nodes(node))


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
            # Read once per handled class, with the effect parameter bound to the effect
            # of that class (performing it emits that class — ``ReceivedEffect``).
            for cls in named.found:
                facts = _Facts()
                reader.collect(region, _received_in(scope, param, cls), filename, facts, generator=True)
                emits = _report_facts(reader, facts, label=f"{label} clause")
                clauses.append(Clause(handles=cls, emits=emits, location=location))
    return _ReadClauses(tuple(clauses), tuple(unresolved))


def _received_in(scope: _Scope, param: str | None, cls: type) -> _Scope:
    """``scope`` with the clause's effect parameter bound to the effect it received."""
    if param is None:
        return scope
    received = _Bound((Binding(param, ReceivedEffect(cls)),))
    return dataclasses.replace(scope, bound=scope.bound.plus(received))


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
            (cls, _region(case.body))
            for case in child.cases
            for cls in _pattern_classes(case.pattern)
        ]
    return []


def _pattern_classes(pattern: ast.pattern) -> list[ast.expr]:
    """The class expressions a ``case`` pattern matches the effect by: a class pattern ``A(...)``, each
    alternative of an or pattern ``A(...) | B(...)`` (every one runs the same body), and the pattern
    under ``... as name``. Other patterns (``_``, values) name no class."""
    if isinstance(pattern, ast.MatchClass):
        return [pattern.cls]
    if isinstance(pattern, ast.MatchOr):
        return [cls for alternative in pattern.patterns for cls in _pattern_classes(alternative)]
    if isinstance(pattern, ast.MatchAs) and pattern.pattern is not None:
        return _pattern_classes(pattern.pattern)
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
    elif isinstance(expr, ast.Name) and isinstance(local := scope.resolve(expr), _LocalFunction):
        node = local.node
    if node is not None:
        return _inline_handler(node, scope, filename, label, location)
    # ``WithHandler(factory(args), body)`` — the dispatcher the factory returns
    # (``run-in-transaction``'s ``transaction-scope``), read as an env element's factory is.
    head = expr.func if isinstance(expr, ast.Call) else expr
    value = scope.resolve(head)
    if isinstance(expr, ast.Call) and _function_of(value) is None:
        value = UNBOUND
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
    if isinstance(element, ast.Name) and isinstance(scope.resolve(element), _LocalFunction):
        return raw_handler_of(element, scope, filename, text)  # a dispatch function defined in the body
    placed = _placed_element(element, scope, filename, text, location, depth=depth)
    if placed is not None:
        return placed
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


# How many names, builders and written arguments a handler list is followed through.  Only
# these hops can come back to where they started (``x = [*x]``, a builder calling itself);
# the literal, ``+``, bind and spread around them are finite in the tree and not counted —
# counting them stopped a ``(<- launch (managed-launch [] adapter))`` inside a builder
# before its plain list was reached (agora-redesign #2674).
_MAX_LIST_HOPS = 12


@dataclass(frozen=True)
class _Slot:
    """One place in a handler list read from source: the handler there, and whether it stands
    for a whole list the reader could not open (``whole_list`` — it may hold any number of
    handlers, so the places after it are not known by position)."""

    handler: HandlerEffects
    whole_list: bool = False


def stack_of(
    expr: ast.expr, scope: _Scope, filename: str, *, depth: int = 0
) -> list[HandlerEffects]:
    """The handlers a handler-list expression denotes, outermost first.

    A list / tuple literal (``*spread`` elements included), ``a + b``, a name bound
    once to one of these (or to ``yield builder()`` — ``(<- base list (builder))``),
    a call to a builder function, a builder's parameter bound to what its caller wrote,
    or a bound / module-level list of handler values.  What cannot be read becomes an
    ``unread`` entry (it may hide a gap).
    """
    return [slot.handler for slot in _slots_of(expr, scope, filename, depth=depth)]


def _slots_of(expr: ast.expr, scope: _Scope, filename: str, *, depth: int) -> list[_Slot]:
    """``stack_of`` with each place marked when it stands for a list read as one entry."""
    text = ast.unparse(expr)
    location = Location(filename, getattr(expr, "lineno", 0))
    if depth > _MAX_LIST_HOPS:
        reason = "handler list is too indirect to follow"
        return [_Slot(_unread(text, Unresolved(reason, text, location)), whole_list=True)]
    hop = depth + 1
    slots: list[_Slot]
    match expr:
        case ast.List(elts=elements) | ast.Tuple(elts=elements):
            slots = [
                slot
                for element in elements
                for slot in (
                    _slots_of(element.value, scope, filename, depth=depth)
                    if isinstance(element, ast.Starred)
                    else [_Slot(element_of(element, scope, filename, depth=depth))]
                )
            ]
        case ast.BinOp(left=left, op=ast.Add(), right=right):
            slots = [
                *_slots_of(left, scope, filename, depth=depth),
                *_slots_of(right, scope, filename, depth=depth),
            ]
        case ast.IfExp() if (wrapper := _bind_expression(expr, scope)) is not None:
            # doeff-hy's bind (``(<- hs (builder))``): the list the bound builder answers.
            slots = _slots_of(_bound_operand(wrapper, scope), scope, filename, depth=depth)
        case ast.IfExp(body=body, orelse=orelse):
            slots = _one_stack([body, orelse], scope, filename, text, location, depth=depth)
        case (
            ast.Yield(value=ast.expr() as inner)
            | ast.YieldFrom(value=inner)
            | ast.Await(value=inner)
        ):
            slots = _slots_of(_bound_operand(inner, scope), scope, filename, depth=depth)
        case ast.Name(id=name) if scope.resolve(expr) is UNBOUND and name in scope.local_values:
            slots = _slots_of(scope.local_values[name], scope, filename, depth=hop)
        case ast.Name() if isinstance(written := scope.resolve(expr), _WRITTEN):
            # A builder's parameter: the list its caller wrote for it, read where written.
            slots = _slots_of(written.expr, written.scope, written.filename, depth=hop)
        case ast.Call(func=func) if (builder := _builder_function(scope.resolve(func))) is not None:
            slots = _env_of(builder, _builder_bindings(builder, expr, scope), text, depth=hop)
        case _:
            value = scope.resolve(expr)
            slots = (
                [_Slot(analyze_handler(item)) for item in value]
                if isinstance(value, (list, tuple))
                else [
                    _Slot(
                        _unread(text, Unresolved("handler list could not be read", text, location)),
                        whole_list=True,
                    )
                ]
            )
    return slots


# What a builder's parameter may be bound to that names an expression written in the caller:
# a Program-looking call (``(indexed (runtime-handlers))``) or any other argument the reader
# could not bind to an object (``(managed-launch [] adapter)``).
_WRITTEN = (_ProgramArg, _WrittenArgument)


def _builder_bindings(function: FunctionType, call: ast.Call, scope: _Scope) -> _Bound:
    """``function``'s parameters bound to the arguments of ``call``: an object when known
    here, else the expression the caller wrote (read as a handler where the builder places
    the parameter in its list)."""
    known = _call_bindings(function, call, scope)
    written = _Bound(
        tuple(
            Binding(passed.name, _WrittenArgument(passed.argument, scope))
            for passed in _passed_arguments(function, call, scope)
            if passed.name not in known.names()
        )
    )
    return written.plus(known)


def _placed_element(
    element: ast.expr,
    scope: _Scope,
    filename: str,
    text: str,
    location: Location,
    *,
    depth: int,
) -> HandlerEffects | None:
    """The handler at a place in a list written elsewhere, or None when ``element`` is not
    such a place: a builder's parameter bound to what its caller wrote (read there), a name
    unpacked from a list (``(setv [lower adapter] (runtime-handlers))``), or a constant index
    into one (``(get runtime 0)``).  A place inside a list that could not be read whole is
    that list's unread entry under the same name — the name an unreadable parent was
    reported by passes to its parts (agora-redesign #2674).  A name bound once to an element
    (``h = (reader {...})`` … ``[h]``) is read as that element."""
    hop = depth + 1
    value = scope.resolve(element)
    placed: HandlerEffects | None
    match element:
        case _ if depth >= _MAX_LIST_HOPS and _follows(element, value, scope):
            reason = "handler is too indirect to follow"
            placed = _unread(text, Unresolved(reason, text, location))
        case ast.Name(id=name) if value is UNBOUND and name in scope.local_values:
            placed = element_of(scope.local_values[name], scope, filename, depth=hop)
        case ast.Name() if isinstance(value, _WRITTEN):
            placed = element_of(value.expr, value.scope, value.filename, depth=hop)
        case ast.Name(id=name) if value is UNBOUND and name in scope.local_unpacked:
            place = scope.local_unpacked[name]
            slots = _slots_of(place.value, scope, filename, depth=hop)
            placed = _pick(slots, place.index, place.count, text, location)
        case ast.Subscript(value=listed, slice=ast.Constant(value=int() as index)) if (
            not isinstance(value, _PLACED_PROGRAMS)
        ):
            slots = _slots_of(listed, scope, filename, depth=hop)
            placed = _pick(slots, index, None, text, location)
        case _:
            placed = None
    return placed


def _follows(element: ast.expr, value: object, scope: _Scope) -> bool:
    """Whether ``_placed_element`` reads ``element`` somewhere else (``value`` = what it
    resolves to)."""
    match element:
        case ast.Name(id=name) if value is UNBOUND:
            return name in scope.local_values or name in scope.local_unpacked
        case ast.Name():
            return isinstance(value, _WRITTEN)
        case ast.Subscript(slice=ast.Constant(value=int())):
            return not isinstance(value, _PLACED_PROGRAMS)
        case _:
            return False


# An element of a sequence of Programs (``ports[0]`` — read as the Program it is, not as a handler).
_PLACED_PROGRAMS = (_ProgramArg, _ProgramAnyOf)


def _pick(
    slots: Sequence[_Slot], index: int, count: int | None, text: str, location: Location
) -> HandlerEffects:
    """The handler at ``index`` of a list read as ``slots`` (``count`` = how many names it
    is unpacked into, when unpacked).  A place counted from the front (or, when the length
    is known, from the back) that only single handlers precede is exact; any other place
    lies inside a list that could not be read whole and is reported unread under that
    list's name."""
    size = len(slots)
    whole = [slot.handler for slot in slots if slot.whole_list]
    if not whole:
        if count is not None and count != size:
            reason = f"unpacks {count} names from a list of {size} handlers"
            return _unread(text, Unresolved(reason, text, location))
        if not -size <= index < size:
            reason = f"index {index} is outside a list of {size} handlers"
            return _unread(text, Unresolved(reason, text, location))
        return slots[index].handler
    front = index if index >= 0 else None
    back = -index if index < 0 else (count - index if count is not None else None)
    if front is not None and front < size and not any(s.whole_list for s in slots[: front + 1]):
        return slots[front].handler
    if (
        back is not None
        and 0 < back <= size
        and not any(s.whole_list for s in slots[size - back :])
    ):
        return slots[size - back].handler
    name = " | ".join(dict.fromkeys(handler.name for handler in whole))
    reason = f"element {index} of a handler list that could not be read whole"
    return _unread(
        name,
        *(item for handler in whole for item in handler.unresolved),
        Unresolved(reason, text, location),
    )


def _builder_function(value: Any) -> FunctionType | None:
    """A function that builds a handler list (not a class, not a handler value)."""
    if value is UNBOUND or isinstance(value, type) or _is_installer(value):
        return None
    return _function_of(value)


def _env_of(function: FunctionType, bound: _Bound, text: str, *, depth: int) -> list[_Slot]:
    """The handler list a builder called inside another list returns (unread when it cannot be)."""
    located = _locate(function, bound)
    if isinstance(located, Unresolved):
        return [_Slot(_unread(text, located), whole_list=True)]
    node, scope, filename = located.node, located.scope, located.filename
    returns = _returned_lists(node)
    location = _location_of(function)
    if not returns:
        reason = "builder does not return a handler list"
        unread = _unread(text, Unresolved(reason, function.__qualname__, location))
        return [_Slot(unread, whole_list=True)]
    return _one_stack(returns, scope, filename, text, location, depth=depth)


def _returned_lists(node: FunctionNode) -> list[ast.expr]:
    """What a builder can return: every ``return`` (a lambda's body)."""
    if isinstance(node, ast.Lambda):
        return [node.body]
    return [
        child.value
        for child in _body_nodes(node)
        if isinstance(child, ast.Return) and child.value is not None
    ]


def _one_stack(
    paths: Sequence[ast.expr],
    scope: _Scope,
    filename: str,
    text: str,
    location: Location,
    *,
    depth: int,
) -> list[_Slot]:
    """The handler list each of ``paths`` (the returns of a builder, the arms of a
    conditional) denotes, when they all denote the same one.  Which path runs is not
    known before running, so lists that differ are unread (reading one path alone —
    the last return — answered for handlers the other paths do not install)."""
    stacks = [_slots_of(path, scope, filename, depth=depth) for path in paths]
    if len({tuple(slot.handler.name for slot in stack) for stack in stacks}) == 1:
        return stacks[0]
    written = " | ".join(ast.unparse(path) for path in paths)
    reason = "builder returns different handler lists on its paths"
    return [_Slot(_unread(text, Unresolved(reason, written, location)), whole_list=True)]


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
    returns = _returned_lists(node)
    if not returns:
        raise ValueError(f"{function.__qualname__} does not return a handler list")
    location = _location_of(function)
    slots = _one_stack(returns, scope, filename, function.__qualname__, location, depth=0)
    return [slot.handler for slot in slots]


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
