"""Which effects a handler handles, which effects its clauses perform, and whether an
env (an ordered handler stack) covers every effect a Program performs.

Read from source like :mod:`doeff_effect_analyzer.program_effects` (Hy is
macro-expanded first, names resolve by importing the defining module; nothing is
run).

A handler clause is recognised by its shape, which ``defhandler`` (doeff-hy) and
hand-written Python handlers share: a function of two or more parameters whose
body tests ``isinstance(effect, T)`` on one of them (``T`` a class or a tuple of classes; negated
tests and ``match effect: case T(...)`` are understood too).  The branch taken
when the test holds is the clause for ``T``; the effects it performs (followed
through Program calls) are what the clause emits outward.

``analyze_handler`` accepts a handler factory (``(defhandler h [args] ...)``,
``def reader(env): ...``), a clause function, or a handler value made by
``defhandler`` (``__doeff_handler_data__``).  ``analyze_env`` reads an env
builder — a function that returns a list literal of handlers, outermost first —
and ``check_coverage`` runs a Program's effects through it from the innermost
handler outward.
"""

import ast
import types
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from doeff_effect_analyzer.program_effects import (
    UNBOUND,
    Location,
    ProgramEffects,
    Unresolved,
    _body_nodes,
    _Facts,
    _find_function,
    _function_of,
    _is_effect_class,
    _location_of,
    _module_source,
    _Reader,
    _report_facts,
    _Scope,
    _scope_of,
    qualified_name,
    resolve_target,
)


@dataclass(frozen=True)
class Clause:
    handles: type
    emits: ProgramEffects
    location: Location


@dataclass(frozen=True)
class HandlerEffects:
    name: str
    clauses: tuple[Clause, ...] = ()
    unresolved: tuple[Unresolved, ...] = ()

    @property
    def handled(self) -> frozenset[type]:
        return frozenset(clause.handles for clause in self.clauses)

    @property
    def known(self) -> bool:
        """False when no clause could be read (the handler's coverage is unknown)."""
        return bool(self.clauses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handler": self.name,
            "clauses": [
                {
                    "handles": qualified_name(clause.handles),
                    "at": str(clause.location),
                    "emits": list(clause.emits.effect_names),
                }
                for clause in self.clauses
            ],
            "unresolved": [
                {"reason": item.reason, "text": item.text, "at": str(item.location)}
                for item in self.unresolved
            ],
        }


@dataclass(frozen=True)
class Gap:
    """An effect no handler in the env handles.  ``origin`` = who performs it."""

    effect: type
    origin: str

    def __str__(self) -> str:
        return f"{self.effect.__name__} (performed by {self.origin})"


@dataclass(frozen=True)
class Coverage:
    gaps: tuple[Gap, ...]
    unknown_handlers: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """No gap, and every handler in the env was read (none could hide a gap)."""
        return not self.gaps and not self.unknown_handlers


# --------------------------------------------------------------------------- handlers


def analyze_handler(target: Any, *, name: str | None = None) -> HandlerEffects:
    obj = resolve_target(target) if isinstance(target, str) else target
    label = name or (target if isinstance(target, str) else _label_of(obj))
    return _analyze_handler(obj, label, depth=0)


_MAX_FACTORY_HOPS = 3


def _analyze_handler(obj: Any, label: str, *, depth: int) -> HandlerEffects:
    function = _handler_function(obj)
    if function is None:
        return HandlerEffects(
            label,
            unresolved=(Unresolved("not a handler or handler factory", repr(obj), _nowhere()),),
        )
    module = _module_of(function)
    if module is None:
        return HandlerEffects(
            label,
            unresolved=(
                Unresolved("module not imported", function.__module__, _location_of(function)),
            ),
        )
    source = _module_source(module)
    root = _find_function(source.tree, function)
    if root is None:
        return HandlerEffects(
            label,
            unresolved=(
                Unresolved(
                    "definition not found in source", function.__qualname__, _location_of(function)
                ),
            ),
        )
    reader = _Reader()
    clauses: list[Clause] = []
    unresolved: list[Unresolved] = []
    for clause_fn, enclosing in _clause_functions(root):
        scope = _scope_with_enclosing(clause_fn, enclosing, module)
        read = _read_clauses(reader, clause_fn, scope, source.filename, label)
        clauses.extend(read.clauses)
        unresolved.extend(read.unresolved)
    if not clauses and depth < _MAX_FACTORY_HOPS:
        # A factory that returns a dispatch function (by reference or as
        # functools.partial(dispatch, ...)), or a handler that returns
        # dispatch(..., effect, k) — read the dispatch function.
        for returned in _returned_handlers(root, _scope_of(root, module)):
            followed = _analyze_handler(returned, label, depth=depth + 1)
            if followed.known:
                return followed
    if not clauses:
        unresolved.append(
            Unresolved("no isinstance(effect, T) clause found", label, _location_of(function))
        )
    return HandlerEffects(label, tuple(clauses), tuple(unresolved))


def _returned_handlers(root: "FunctionNode", scope: _Scope) -> Iterator[Any]:
    import functools

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
            # functools.partial(dispatch, ...) → dispatch; dispatch(..., effect, k) → dispatch
            expr = expr.args[0] if head is functools.partial and expr.args else expr.func
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


def _handler_function(obj: Any) -> types.FunctionType | None:
    data = getattr(obj, "__doeff_handler_data__", None)
    if data is not None:
        function = _function_of(data)
        if function is not None:
            return function
    return _function_of(obj)


def _module_of(function: types.FunctionType) -> types.ModuleType | None:
    import sys

    return sys.modules.get(function.__module__)


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


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


def _scope_with_enclosing(
    node: FunctionNode, enclosing: tuple[FunctionNode, ...], module: types.ModuleType
) -> _Scope:
    scope = _scope_of(node, module)
    names = set(scope.local_names)
    for outer in enclosing:
        names |= _scope_of(outer, module).local_names
    imports = dict(scope.local_imports)
    for outer in enclosing:
        imports = {**_scope_of(outer, module).local_imports, **imports}
    return _Scope(
        module=module,
        local_names=frozenset(names - set(imports)),
        local_calls=scope.local_calls,
        local_imports=imports,
    )


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


# --------------------------------------------------------------------------- env


def analyze_env(builder: Any) -> list[HandlerEffects]:
    """Handlers of an env builder (``(defn env [config ctx] [(h1) (h2 x) h3])``), outer first.

    The builder's last ``return`` of a list literal is read; each element is a
    handler factory call or a handler value.
    """
    obj = resolve_target(builder) if isinstance(builder, str) else builder
    function = _function_of(obj)
    if function is None:
        raise TypeError(f"{builder!r} is not a function")
    module = _module_of(function)
    if module is None:
        raise ValueError(f"module {function.__module__} is not imported")
    source = _module_source(module)
    node = _find_function(source.tree, function)
    if node is None:
        raise ValueError(f"definition of {function.__qualname__} not found in source")
    returned = [
        child.value
        for child in _body_nodes(node)
        if isinstance(child, ast.Return) and isinstance(child.value, ast.List)
    ]
    if not returned:
        raise ValueError(f"{function.__qualname__} does not return a list literal of handlers")
    scope = _scope_of(node, module)
    handlers: list[HandlerEffects] = []
    for element in returned[-1].elts:
        text = ast.unparse(element)
        head = element.func if isinstance(element, ast.Call) else element
        value = scope.resolve(head)
        if value is UNBOUND:
            handlers.append(
                HandlerEffects(
                    text,
                    unresolved=(
                        Unresolved(
                            "handler is not a module-level name",
                            text,
                            Location(source.filename, getattr(element, "lineno", 0)),
                        ),
                    ),
                )
            )
            continue
        handlers.append(_element_handler(element, value, scope, text))
    return handlers


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


# --------------------------------------------------------------------------- coverage


def check_coverage(
    effects: Iterable[type] | ProgramEffects,
    env: Sequence[HandlerEffects],
    *,
    origin: str = "program",
) -> Coverage:
    """Run ``effects`` through ``env`` (outermost first) from the innermost handler out.

    A handled effect is replaced by the effects its clause performs; whatever is
    left after the outermost handler is a gap.  A clause keyed on a parent class
    also handles its subclasses (``isinstance`` semantics).
    """
    if isinstance(effects, ProgramEffects):
        origin = effects.target if origin == "program" else origin
        effects = effects.effect_types
    pending: dict[type, str] = dict.fromkeys(effects, origin)
    for handler in reversed(env):
        emitted: dict[type, str] = {}
        for effect in list(pending):
            clause = next((c for c in handler.clauses if issubclass(effect, c.handles)), None)
            if clause is None:
                continue
            del pending[effect]
            for out in clause.emits.effect_types:
                emitted.setdefault(out, handler.name)
        for out, by in emitted.items():
            pending.setdefault(out, by)
    gaps = sorted(
        (Gap(e, o) for e, o in pending.items()), key=lambda g: (g.effect.__name__, g.origin)
    )
    unknown = tuple(handler.name for handler in env if not handler.known)
    return Coverage(gaps=tuple(gaps), unknown_handlers=unknown)
