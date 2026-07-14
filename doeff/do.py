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
from typing import Any, ParamSpec, overload

from doeff.program import Apply, Expand, Pure

P = ParamSpec("P")


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
        self.function_depth += 1
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
        self.protected_depth += 1
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
    return _call_leaf_name(node.func) in {"Resume", "ResumeThrow"}


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


def _analyze_resume_yields(fn: Callable[..., Any], *, non_tail: bool) -> tuple[int, ...]:
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

    def _remember(result: tuple[int, ...]) -> tuple[int, ...]:
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


@overload
def do(fn: Callable[P, Generator[Any, Any, Any]], /) -> Callable[P, Expand]: ...


@overload
def do(
    *,
    non_tail: bool = False,
) -> Callable[[Callable[P, Generator[Any, Any, Any]]], Callable[P, Expand]]: ...


def do(
    fn: Callable[P, Generator[Any, Any, Any]] | None = None,
    /,
    *,
    non_tail: bool = False,
) -> Callable[P, Expand] | Callable[[Callable[P, Generator[Any, Any, Any]]], Callable[P, Expand]]:
    """Wrap a generator function so calling it returns a DoExpr tree."""

    def decorate(fn: Callable[P, Generator[Any, Any, Any]]) -> Callable[P, Expand]:
        tail_resume_lines = _analyze_resume_yields(fn, non_tail=non_tail)

        from doeff_vm import Callable as VMCallable
        from doeff_vm import IRStream

        def _make_stream(result):
            if inspect.isgenerator(result):
                return IRStream(result, tail_resume_lines)

            def value_gen():
                if False:
                    yield
                return result

            return IRStream(value_gen())

        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Expand:
            def thunk():
                return _make_stream(fn(*args, **kwargs))

            return Expand(Apply(Pure(VMCallable(thunk)), []))

        return wrapper

    if fn is None:
        return decorate

    return decorate(fn)
