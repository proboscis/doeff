"""The effects a doeff Program can perform, read from its source without running it.

Why this module exists (and not the name-matching Rust core for this job):

- An effect is any class that subclasses ``doeff_vm.EffectBase``, including the
  ones a project defines itself.  Whether a name denotes such a class is decided
  by importing the module that defines the Program and looking the name up —
  aliases, re-exports and ``Effect = EffectBase`` style assignments are then
  exact.  Nothing is run: no Program is executed, no handler is installed.
- Hy modules are read the way Hy reads them: forms are macro-expanded with Hy's
  own compiler (``defk``, ``<-`` and project macros that wrap them, such as a
  ``defservice`` that expands to ``defk``) into a Python AST.  The Hy analyzer in
  the Rust core has its own reader and cannot expand user macros.

What counts, starting from a Program function (``@do`` / ``defk`` / a plain
generator function):

- ``yield E(...)`` / ``yield from E(...)`` with ``E`` an ``EffectBase`` subclass →
  the effect ``E``.
- ``yield f(...)`` / ``yield from f(...)`` with ``f`` another Program function →
  ``f``'s effects, recorded with ``via`` = the call chain.
- a function that is not a generator but ``return``s such a call (a Program
  factory) is followed the same way.
- ``E(..., f(...), ...)`` where ``E`` is an effect and ``f`` a Program function →
  a *carried* Program (``Spawn``, ``Try``, ``RemoteJob`` …).  Whether a carrier
  runs its Program under the same handlers (``Spawn``) or elsewhere
  (``RemoteJob``) is the carrier's semantics, so carried Programs are reported
  separately with their own effect sets; callers choose which to fold in.
- doeff-vm control values (``Resume``, ``Transfer``, ``Pass`` …) are not effects.

Anything the reader cannot follow (a yielded local variable, a call whose target
is not importable) is reported in ``unresolved`` instead of being dropped.
"""

import ast
import importlib
import inspect
import sys
import types
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Location:
    file: str
    line: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class EffectUse:
    """One place an effect is performed.  ``via`` = Program functions from the target down."""

    effect: type
    location: Location
    via: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return qualified_name(self.effect)


@dataclass(frozen=True)
class CarriedProgram:
    """A Program handed to an effect or to another Program function.

    ``Spawn(child(...))`` / ``RemoteJob(task(...))``: ``carrier`` is the effect class.
    ``remote_job(task(...))`` (a helper Program that performs the carrier effect):
    ``carrier`` is that helper function.
    """

    carrier: Any
    program: "ProgramEffects"
    location: Location
    via: tuple[str, ...] = ()


@dataclass(frozen=True)
class Unresolved:
    """Something yielded or called that the reader could not follow."""

    reason: str
    text: str
    location: Location
    via: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProgramEffects:
    target: str
    effects: tuple[EffectUse, ...] = ()
    carried: tuple[CarriedProgram, ...] = ()
    unresolved: tuple[Unresolved, ...] = ()

    @property
    def effect_types(self) -> frozenset[type]:
        return frozenset(use.effect for use in self.effects)

    @property
    def effect_names(self) -> list[str]:
        return sorted({use.name for use in self.effects})

    def effect_types_with(self, include: Callable[[Any], bool]) -> frozenset[type]:
        """Own effects plus those of carried Programs whose carrier ``include`` accepts."""
        types_: set[type] = set(self.effect_types)
        for carried in self.carried:
            if include(carried.carrier):
                types_ |= carried.program.effect_types_with(include)
        return frozenset(types_)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "effects": sorted(self.effect_names),
            "uses": [
                {"effect": use.name, "at": str(use.location), "via": list(use.via)}
                for use in self.effects
            ],
            "carried": [
                {
                    "carrier": qualified_name(carried.carrier),
                    "at": str(carried.location),
                    "via": list(carried.via),
                    "program": carried.program.to_dict(),
                }
                for carried in self.carried
            ],
            "unresolved": [
                {
                    "reason": item.reason,
                    "text": item.text,
                    "at": str(item.location),
                    "via": list(item.via),
                }
                for item in self.unresolved
            ],
        }


def qualified_name(obj: Any) -> str:
    return f"{obj.__module__}.{obj.__qualname__}"


# --------------------------------------------------------------------------- source


@dataclass(frozen=True)
class _ModuleSource:
    module: types.ModuleType
    tree: ast.Module
    filename: str


_MODULE_CACHE: dict[str, _ModuleSource] = {}


def module_ast(module: types.ModuleType) -> ast.Module:
    """The module's AST; Hy modules are macro-expanded with Hy's compiler first."""
    return _module_source(module).tree


def _module_source(module: types.ModuleType) -> _ModuleSource:
    cached = _MODULE_CACHE.get(module.__name__)
    if cached is not None and cached.module is module:
        return cached
    filename = getattr(module, "__file__", None)
    if not filename:
        raise ValueError(f"module {module.__name__} has no source file")
    source = Path(filename).read_text(encoding="utf-8")
    if filename.endswith(".hy"):
        tree = _compile_hy(source, filename, module.__name__)
    else:
        tree = ast.parse(source, filename=filename)
    loaded = _ModuleSource(module=module, tree=tree, filename=filename)
    _MODULE_CACHE[module.__name__] = loaded
    return loaded


def _compile_hy(source: str, filename: str, module_name: str) -> ast.Module:
    import hy
    import hy.compiler

    # A fresh module object: expansion registers macros on it and must not
    # disturb the imported module whose globals resolve names.
    scratch = types.ModuleType(module_name)
    scratch.__file__ = filename
    compiled = hy.compiler.hy_compile(
        hy.read_many(source, filename=filename), scratch, filename=filename, source=source
    )
    if not isinstance(compiled, ast.Module):
        raise TypeError(f"{filename}: Hy compiled to {type(compiled)!r}, expected a module")
    return compiled


def resolve_target(spec: str) -> Any:
    """``package.module:attr[.attr…]`` → the object (imports the module)."""
    if ":" not in spec:
        raise ValueError(f"target must be 'module:attr', got {spec!r}")
    module_name, attr_path = spec.split(":", 1)
    _ensure_hy_importable()
    obj: Any = importlib.import_module(module_name)
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    return obj


def _ensure_hy_importable() -> None:
    try:
        import hy  # noqa: F401 - registers the .hy import hook
    except ImportError:
        return


# --------------------------------------------------------------------------- classify


def _effect_base() -> type:
    from doeff_vm import EffectBase

    return EffectBase


def _is_effect_class(obj: Any) -> bool:
    return isinstance(obj, type) and issubclass(obj, _effect_base())


def _is_control_class(obj: Any) -> bool:
    return isinstance(obj, type) and obj.__module__.split(".")[0] == "doeff_vm"


def _function_of(obj: Any) -> types.FunctionType | None:
    """The plain function behind a Program function (``@do``/``defk`` wrappers unwrapped)."""
    if isinstance(obj, types.MethodType):
        obj = obj.__func__
    if not callable(obj) or isinstance(obj, type):
        return None
    unwrapped = inspect.unwrap(obj)
    if isinstance(unwrapped, types.FunctionType):
        return unwrapped
    return None


class _Unbound:
    """What a name resolves to when the reader cannot bind it to an object."""

    def __repr__(self) -> str:
        return "UNBOUND"


UNBOUND: Any = _Unbound()


@dataclass(frozen=True)
class _Scope:
    """Name resolution inside one function body."""

    module: types.ModuleType
    local_names: frozenset[str]
    local_calls: dict[str, ast.Call]
    local_imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)

    def resolve(self, expr: ast.expr) -> Any:
        """The object a Name / Attribute chain denotes (rooted at a module global or
        at a name the function imports itself), or ``UNBOUND``."""
        if isinstance(expr, ast.Name):
            return self._resolve_name(expr.id)
        if isinstance(expr, ast.Attribute):
            base = self.resolve(expr.value)
            if base is not UNBOUND and hasattr(base, expr.attr):
                return getattr(base, expr.attr)
        return UNBOUND

    def _resolve_name(self, name: str) -> Any:
        if name in self.local_imports:
            return _import_binding(*self.local_imports[name])
        if name in self.local_names:
            return UNBOUND
        for table in (vars(self.module), _builtins_of(self.module)):
            if name in table:
                return table[name]
        return UNBOUND


def _builtins_of(module: types.ModuleType) -> dict[str, Any]:
    builtins = vars(module).get("__builtins__")
    if isinstance(builtins, dict):
        return builtins
    if isinstance(builtins, types.ModuleType):
        return vars(builtins)
    return {}


@dataclass
class _Facts:
    """What one function body does, before following calls."""

    effects: list[tuple[type, Location]] = field(default_factory=list)
    calls: list[tuple[types.FunctionType, Location]] = field(default_factory=list)
    carried: list[tuple[Any, types.FunctionType, Location]] = field(default_factory=list)
    unresolved: list[tuple[str, str, Location]] = field(default_factory=list)


def _body_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> Iterator[ast.AST]:
    """Nodes of the function body, not descending into nested functions/classes/lambdas."""
    body: list[ast.AST] = (
        [function.body] if isinstance(function, ast.Lambda) else list(function.body)
    )
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _scope_of(
    function: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, module: types.ModuleType
) -> _Scope:
    arguments = function.args
    names = {arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)}
    if arguments.vararg:
        names.add(arguments.vararg.arg)
    if arguments.kwarg:
        names.add(arguments.kwarg.arg)
    assigned_calls: dict[str, list[ast.Call]] = {}
    imports: dict[str, tuple[str, str | None]] = {}
    for node in _body_nodes(function):
        names |= _bound_names(node)
        imports.update(_import_bindings(node))
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
        ):
            assigned_calls.setdefault(node.targets[0].id, []).append(node.value)
    # A local bound exactly once to a call can be followed through that call.
    local_calls = {name: calls[0] for name, calls in assigned_calls.items() if len(calls) == 1}
    return _Scope(
        module=module,
        local_names=frozenset(names - set(imports)),
        local_calls=local_calls,
        local_imports=imports,
    )


def _bound_names(node: ast.AST) -> set[str]:
    """Names a body node binds locally (assignment targets, nested defs)."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return {node.id}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    return set()


def _import_bindings(node: ast.AST) -> dict[str, tuple[str, str | None]]:
    """``import`` inside a body: local name → (module, attribute or None)."""
    if isinstance(node, ast.Import):
        return {
            (alias.asname or alias.name.split(".")[0]): (
                alias.name if alias.asname else alias.name.split(".")[0],
                None,
            )
            for alias in node.names
        }
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return {(alias.asname or alias.name): (node.module, alias.name) for alias in node.names}
    return {}


def _import_binding(module_name: str, attr: str | None) -> Any:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return UNBOUND
    if attr is None:
        return module
    if hasattr(module, attr):
        return getattr(module, attr)
    try:
        return importlib.import_module(f"{module_name}.{attr}")
    except ImportError:
        return UNBOUND


class _Reader:
    def __init__(self) -> None:
        self._facts: dict[types.FunctionType, _Facts] = {}

    def facts(self, function: types.FunctionType) -> _Facts:
        known = self._facts.get(function)
        if known is not None:
            return known
        facts = _Facts()
        self._facts[function] = facts  # recursion sees an (empty) entry, not a loop
        located = _locate(function)
        if isinstance(located, Unresolved):
            facts.unresolved.append((located.reason, located.text, located.location))
            return facts
        node, scope, filename = located
        self.collect(_body_nodes(node), scope, filename, facts, generator=_is_generator(node))
        return facts

    def collect(
        self,
        nodes: Iterable[ast.AST],
        scope: "_Scope",
        filename: str,
        facts: _Facts,
        *,
        generator: bool,
    ) -> None:
        for child in nodes:
            if isinstance(child, (ast.Yield, ast.YieldFrom)) and child.value is not None:
                self._performed(child.value, scope, filename, facts)
            elif not generator and isinstance(child, ast.Return) and child.value is not None:
                self._returned(child.value, scope, filename, facts)

    def is_program(self, function: types.FunctionType) -> bool:
        """A generator function, or a factory that returns a Program / effect."""
        if inspect.isgeneratorfunction(function):
            return True
        facts = self.facts(function)
        return bool(facts.effects or facts.calls)

    def _performed(self, expr: ast.expr, scope: _Scope, filename: str, facts: _Facts) -> None:
        location = Location(filename, getattr(expr, "lineno", 0))
        if isinstance(expr, ast.IfExp):
            self._performed(expr.body, scope, filename, facts)
            self._performed(expr.orelse, scope, filename, facts)
            return
        if isinstance(expr, ast.Constant) and expr.value is None:
            return
        call = self._as_call(expr, scope)
        if call is None:
            facts.unresolved.append(
                ("yielded a value that is not a call", ast.unparse(expr), location)
            )
            return
        target = scope.resolve(call.func)
        if target is UNBOUND:
            facts.unresolved.append(
                (
                    "yielded a call whose target is not a module-level name",
                    ast.unparse(call.func),
                    location,
                )
            )
        elif _is_effect_class(target):
            facts.effects.append((target, location))
            self._carried_arguments(target, call, scope, filename, facts)
        elif (function := _function_of(target)) is not None and not _is_control_class(target):
            facts.calls.append((function, location))
            self._carried_arguments(target, call, scope, filename, facts)
        elif not _is_control_class(target):
            facts.unresolved.append(
                (
                    "yielded a call to something that is neither an effect nor a Program function",
                    ast.unparse(call.func),
                    location,
                )
            )

    def _returned(self, expr: ast.expr, scope: _Scope, filename: str, facts: _Facts) -> None:
        """A Program factory's ``return f(...)`` / ``return E(...)``; other returns are data."""
        if isinstance(expr, ast.IfExp):
            self._returned(expr.body, scope, filename, facts)
            self._returned(expr.orelse, scope, filename, facts)
            return
        call = self._as_call(expr, scope)
        if call is None:
            return
        target = scope.resolve(call.func)
        if target is UNBOUND:
            return
        if _is_effect_class(target) or _function_of(target) is not None:
            self._performed(call, scope, filename, facts)

    def _carried_arguments(
        self, carrier: Any, call: ast.Call, scope: _Scope, filename: str, facts: _Facts
    ) -> None:
        for argument in (*call.args, *(keyword.value for keyword in call.keywords)):
            inner = self._as_call(argument, scope)
            if inner is None:
                continue
            target = scope.resolve(inner.func)
            if target is UNBOUND or _is_effect_class(target):
                continue
            function = _function_of(target)
            if function is not None and self.is_program(function):
                facts.carried.append(
                    (carrier, function, Location(filename, getattr(argument, "lineno", 0)))
                )

    @staticmethod
    def _as_call(expr: ast.expr, scope: _Scope) -> ast.Call | None:
        if isinstance(expr, ast.Call):
            return expr
        if isinstance(expr, ast.Name) and expr.id in scope.local_calls:
            return scope.local_calls[expr.id]
        return None


def _is_generator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _body_nodes(node))


def _locate(
    function: types.FunctionType,
) -> "tuple[ast.FunctionDef | ast.AsyncFunctionDef, _Scope, str] | Unresolved":
    """The def of ``function`` in its module's (expanded) AST, with its name scope."""
    module = sys.modules.get(function.__module__)
    if module is None:
        return Unresolved("module not imported", function.__module__, _location_of(function))
    try:
        source = _module_source(module)
    except (OSError, ValueError, TypeError, SyntaxError) as error:
        return Unresolved("source unavailable", str(error), _location_of(function))
    node = _find_function(source.tree, function)
    if node is None:
        return Unresolved(
            "definition not found in source", function.__qualname__, _location_of(function)
        )
    return node, _scope_of(node, module), source.filename


def _location_of(function: types.FunctionType) -> Location:
    return Location(function.__code__.co_filename, function.__code__.co_firstlineno)


def _find_function(
    tree: ast.Module, function: types.FunctionType
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The def of ``function`` in ``tree`` (qualname path; the closest line on ties)."""
    parts = [part for part in function.__qualname__.split(".") if part != "<locals>"]
    candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def walk(body: list[ast.stmt], depth: int) -> None:
        for statement in body:
            nested = _nested_statements(statement)
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if statement.name != parts[depth]:
                    walk(nested, depth)
                    continue
                if depth == len(parts) - 1:
                    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        candidates.append(statement)
                else:
                    walk(statement.body, depth + 1)
            else:
                walk(nested, depth)

    walk(tree.body, 0)
    if not candidates:
        return None
    first_line = function.__code__.co_firstlineno

    def distance(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        lines = [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
        return min(abs(line - first_line) for line in lines)

    return min(candidates, key=distance)


def _nested_statements(statement: ast.stmt) -> list[ast.stmt]:
    """Statement blocks inside compound statements (if/try/with …), not defs."""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return []
    out: list[ast.stmt] = []
    for name in ("body", "orelse", "finalbody"):
        block = getattr(statement, name, None)
        if isinstance(block, list):
            out.extend(item for item in block if isinstance(item, ast.stmt))
    for handler in getattr(statement, "handlers", []) or []:
        out.extend(handler.body)
    for case in getattr(statement, "cases", []) or []:
        out.extend(case.body)
    return out


# --------------------------------------------------------------------------- report


def analyze_program(target: Any) -> ProgramEffects:
    """Effects of a Program function (object, or ``"module:attr"``), following calls.

    The target's module is imported (to resolve names); no Program is run.
    """
    obj = resolve_target(target) if isinstance(target, str) else target
    function = _function_of(obj)
    if function is None:
        raise TypeError(f"{target!r} is not a Program function (@do / defk / generator)")
    return _report(
        _Reader(),
        function,
        label=str(target) if isinstance(target, str) else qualified_name(function),
    )


def _report(reader: _Reader, root: types.FunctionType, *, label: str) -> ProgramEffects:
    return _report_facts(reader, reader.facts(root), label=label, root=root)


def _report_facts(
    reader: _Reader,
    start: _Facts,
    *,
    label: str,
    root: types.FunctionType | None = None,
) -> ProgramEffects:
    """Effects of ``start`` and of every Program function it calls (transitively)."""
    effects: list[EffectUse] = []
    carried: list[CarriedProgram] = []
    unresolved: list[Unresolved] = []
    seen: set[types.FunctionType] = set() if root is None else {root}

    def absorb(facts: _Facts, via: tuple[str, ...]) -> None:
        effects.extend(EffectUse(effect, location, via) for effect, location in facts.effects)
        unresolved.extend(
            Unresolved(reason, text, location, via) for reason, text, location in facts.unresolved
        )
        for carrier, program, location in facts.carried:
            carried.append(
                CarriedProgram(
                    carrier=carrier,
                    program=_report(reader, program, label=qualified_name(program)),
                    location=location,
                    via=via,
                )
            )
        for callee, _location in facts.calls:
            if callee in seen:
                continue
            seen.add(callee)
            absorb(reader.facts(callee), (*via, qualified_name(callee)))

    absorb(start, ())
    return ProgramEffects(
        target=label,
        effects=tuple(effects),
        carried=tuple(carried),
        unresolved=tuple(unresolved),
    )
