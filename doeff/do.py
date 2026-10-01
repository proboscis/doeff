"""
The @do decorator — converts a generator function into a program factory.

    @do
    def my_program(x) -> EffectGenerator[int]:
        result = yield some_effect
        return result + x

    prog = my_program(42)  # returns Program[int] (not executed yet)
    result = run(prog)     # execute
"""
import ast
import inspect
import tokenize
import warnings
from collections.abc import Callable, Generator
from functools import wraps
from textwrap import dedent
from typing import Any, Never, ParamSpec, TypeVar, overload

from doeff.program import Expand

P = ParamSpec("P")
_E = TypeVar("_E")
_T = TypeVar("_T")


class _ResumeYieldAnalysis(ast.NodeVisitor):
    def __init__(self, source_start_line: int) -> None:
        self.source_start_line = source_start_line
        self.function_depth = 0
        self.protected_depth = 0
        self.tail_resume_lines: set[int] = set()
        self.non_tail_resume_lines: set[int] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.function_depth > 0:
            return
        self.function_depth += 1  # noqa: DOEFF002 - int counter, not a mutable container
        self._visit_statement_block(node.body)
        self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self.function_depth > 0:
            return
        self.function_depth += 1
        self._visit_statement_block(node.body)
        self.function_depth -= 1

    def visit_Return(self, node: ast.Return) -> None:
        if (
            self.protected_depth == 0
            and isinstance(node.value, ast.Yield)
            and _is_resume_call(node.value.value)
        ):
            self.tail_resume_lines.update(self._absolute_lines(node.value))
            return
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        if _is_resume_call(node.value):
            self.non_tail_resume_lines.add(self._absolute_line(node))
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_statement_block(node.body)
        self._visit_statement_block(node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_statement_block(node.body)
        self._visit_statement_block(node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit(node.target)
        self.visit(node.iter)
        self._visit_statement_block(node.body)
        self._visit_statement_block(node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_statement_block(node.body)
        self._visit_statement_block(node.orelse)

    def visit_Try(self, node: ast.Try) -> None:
        self.protected_depth += 1  # noqa: DOEFF002 - int counter, not a mutable container
        self.generic_visit(node)
        self.protected_depth -= 1

    def visit_With(self, node: ast.With) -> None:
        self.protected_depth += 1
        self.generic_visit(node)
        self.protected_depth -= 1

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.protected_depth += 1
        self.generic_visit(node)
        self.protected_depth -= 1

    def _absolute_line(self, node: ast.AST) -> int:
        lineno = int(getattr(node, "lineno", 0))
        return self.source_start_line + lineno - 1

    def _absolute_lines(self, node: ast.AST) -> range:
        lineno = int(getattr(node, "lineno", 0))
        end_lineno = int(getattr(node, "end_lineno", lineno))
        start = self._absolute_line(node)
        end = self.source_start_line + end_lineno - 1
        return range(start, end + 1)

    def _visit_statement_block(self, statements: list[ast.stmt]) -> None:
        index = 0
        while index < len(statements):
            if index + 1 < len(statements):
                yield_node = self._tail_assignment_resume_yield(
                    statements[index],
                    statements[index + 1],
                )
                if yield_node is not None:
                    self.tail_resume_lines.update(self._absolute_lines(yield_node))
                    index += 2
                    continue
            self.visit(statements[index])
            index += 1

    def _tail_assignment_resume_yield(
        self,
        first: ast.stmt,
        second: ast.stmt,
    ) -> ast.Yield | None:
        if self.protected_depth != 0:
            return None
        if not isinstance(second, ast.Return) or not isinstance(second.value, ast.Name):
            return None

        assigned_name: str | None = None
        assigned_value: ast.expr | None = None
        if isinstance(first, ast.Assign) and len(first.targets) == 1:
            target = first.targets[0]
            if isinstance(target, ast.Name):
                assigned_name = target.id
                assigned_value = first.value
        elif isinstance(first, ast.AnnAssign) and isinstance(first.target, ast.Name):
            assigned_name = first.target.id
            assigned_value = first.value

        if assigned_name != second.value.id or not isinstance(assigned_value, ast.Yield):
            return None
        if not _is_resume_call(assigned_value.value):
            return None
        return assigned_value


def _is_resume_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return _call_leaf_name(node.func) in {"Resume", "ResumeThrow", "typed_resume"}


def _call_leaf_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# Keyed by (code object id via the object itself, non_tail).  `do()` is applied
# every time a handler/closure is CONSTRUCTED, which in Hy apps happens per
# handler instantiation (sometimes per request / per reconcile step) — without
# a cache every construction re-reads the source file and re-runs the CPython
# PEG parser.  Live incident 2026-07-04: a BFF serving ~114k requests spent
# essentially all its CPU inside ast.parse/getsourcelines from this diagnostic,
# starving the co-resident reconcile loop into wire timeouts.  The analysis
# depends only on the code object, so caching is behavior-identical (the
# non-tail warning fires once per code object instead of once per construction,
# which is strictly less noisy).
_RESUME_ANALYSIS_CACHE: dict[tuple[int, bool], tuple[int, ...]] = {}
_RESUME_ANALYSIS_CACHE_KEEPALIVE: list[Any] = []


def _analyze_resume_yields(fn: Callable[..., Any], *, non_tail: bool) -> tuple[int, ...]:  # noqa: DOEFF006 - immutable line-number set for IRStream
    # tail-resume analysis is purely a warning/diagnostic optimization. If we
    # cannot recover Python source for `fn` (e.g. Hy-defined handlers, lambdas
    # generated at runtime, frozen functions), silently skip — the runtime
    # behavior of the @do wrapper is unaffected.
    code = getattr(fn, "__code__", None)
    cache_key = None
    if code is not None:
        cache_key = (id(code), non_tail)
        cached = _RESUME_ANALYSIS_CACHE.get(cache_key)
        if cached is not None:
            return cached
        # Hy (and any non-.py) sources can never satisfy ast.parse — skip
        # before paying for the file read and the parse attempt.
        filename = getattr(code, "co_filename", "")
        if not filename.endswith(".py"):
            _RESUME_ANALYSIS_CACHE[cache_key] = ()
            _RESUME_ANALYSIS_CACHE_KEEPALIVE.append(code)
            return ()

    def _remember(result: tuple[int, ...]) -> tuple[int, ...]:  # noqa: DOEFF006 - same tuple as the cache value
        if cache_key is not None:
            _RESUME_ANALYSIS_CACHE[cache_key] = result
            # id(code) keys are only stable while the code object lives; keep
            # it alive so a recycled id cannot alias a different function.
            _RESUME_ANALYSIS_CACHE_KEEPALIVE.append(code)
        return result

    try:
        source_lines, start_line = inspect.getsourcelines(fn)
    except (OSError, TypeError, tokenize.TokenError, SyntaxError):
        return _remember(())

    try:
        module = ast.parse(dedent("".join(source_lines)))
    except (SyntaxError, ValueError):
        return _remember(())

    function_node = next(
        (
            node
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn.__name__
        ),
        None,
    )
    if function_node is None:
        return _remember(())

    visitor = _ResumeYieldAnalysis(start_line)
    visitor.visit(function_node)
    if visitor.non_tail_resume_lines and not non_tail:
        sorted_lines = sorted(visitor.non_tail_resume_lines)
        lines = ", ".join(str(line) for line in sorted_lines)
        warnings.warn_explicit(
            "non-tail Resume/ResumeThrow in @do handler keeps the handler generator "
            "frame and its locals live until the resumed continuation returns; use "
            "@do(non_tail=True) to acknowledge this, or Transfer/TransferThrow when "
            f"the handler is done after resuming (line(s): {lines})",
            RuntimeWarning,
            fn.__code__.co_filename,
            sorted_lines[0],
        )

    return _remember(tuple(sorted(visitor.tail_resume_lines)))


# 生成器の関数は上の overload に先に当たり(overload は上から順に選ばれる)、下の overload は yield の無い関数だけに
# 当たる。Python の型には「生成器でない関数」を書く口が無いので、pyright は 2 つが重なると見る — 重なりは順で解ける
# (検 = tests/test_static_typing.py・packages/doeff-hy/tests/test_static_check_for_do.py・agora-redesign #2321)。
@overload
def do(fn: Callable[P, Generator[_E, Any, _T]], /) -> Callable[P, Expand[_T, _E]]: ...  # pyright: ignore[reportOverlappingOverload]  # 順で解ける重なり(上の註)


@overload
def do(fn: Callable[P, _T], /) -> Callable[P, Expand[_T, Never]]: ...


@overload
def do(
    *,
    non_tail: bool = False,
) -> Callable[[Callable[P, Generator[_E, Any, _T]]], Callable[P, Expand[_T, _E]]]: ...


def do(
    fn: Callable[P, Any] | None = None,
    /,
    *,
    non_tail: bool = False,
) -> Callable[P, Expand] | Callable[[Callable[P, Generator[Any, Any, Any]]], Callable[P, Expand]]:
    """Wrap a generator function so calling it returns a DoExpr tree.

    Typing: ``@do`` keeps the parameters, the effects and the result of the
    body. For ``def f(x: int) -> Generator[ReadShared | WriteShared, Any, bool]``,
    ``f`` is ``Callable[[int], Expand[bool, ReadShared | WriteShared]]``; a caller
    runs it with ``ok = yield from f(1)`` (``ok: bool``) and must itself declare
    ``ReadShared | WriteShared`` among the effects it yields. Yielding an effect
    that the body's annotation does not list is a type error.

    A function without ``yield`` is accepted too: the VM calls it and its return value
    becomes the program's value (``program_factory`` — ``DoFunction`` records whether ``fn``
    is a generator function), so ``f`` is ``Callable[P, Expand[T, Never]]``. This is the
    one type of ``do``: doeff-hy's type-check expansion (doeff_hy/static_types.pyi) re-exports
    it instead of declaring a second ``do``, so a module that imports ``do`` as ``_doeff_do``
    itself (the for/do contract) does not give the name a second, different type
    (agora-redesign #2321).
    """

    def decorate(fn: Callable[P, Any]) -> Callable[P, Expand]:
        return program_factory(fn, _analyze_resume_yields(fn, non_tail=non_tail))

    if fn is None:
        return decorate

    return decorate(fn)


def program_factory(
    fn: Callable[P, Any],
    tail_resume_lines: tuple[int, ...],  # noqa: DOEFF006 - immutable line-number set for IRStream
) -> Callable[P, Expand]:
    """The runtime shape shared by ``@do`` and ``@effectful``.

    Calling the result builds one ``Call`` node (an ``Expand``) holding the definition and
    the arguments; the VM calls ``fn`` and runs the generator it returns (a non-generator
    result becomes the program's value). The definition (``DoFunction``) is built once
    here, so a call allocates a single node — not the ``Expand(Apply(Pure(Callable(thunk))))``
    chain and its closure (agora-redesign #844).
    """
    from doeff_vm import Call, DoFunction

    definition = DoFunction(fn, list(tail_resume_lines), inspect.isgeneratorfunction(fn))

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Expand:
        return Call(definition, args, kwargs)

    # Installed as a handler, the VM calls `fn` directly and runs the generator as
    # the handler's stream (same end state as evaluating the Expand above, without
    # building it per effect) — doeff_vm._effect_types.handler_spec.
    # @wraps copied fn.__dict__; a spec cached on fn (double @do) must not describe us.
    wrapper.__dict__.pop("__doeff_handler_spec__", None)
    wrapper.__doeff_generator_function__ = fn  # type: ignore[attr-defined]
    wrapper.__doeff_tail_resume_lines__ = tail_resume_lines  # type: ignore[attr-defined]
    return wrapper
