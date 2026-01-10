"""Code runner for doeff -c flag with top-level yield support."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any


class YieldDetector(ast.NodeVisitor):
    """Detect yield statements at module level (not inside nested functions)."""

    def __init__(self) -> None:
        self.has_yield = False
        self.depth = 0

    def visit_Yield(self, node: ast.Yield) -> None:
        if self.depth == 0:
            self.has_yield = True
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # Skip nested functions

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass  # Skip async functions

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass  # Skip classes


def _has_toplevel_yield(tree: ast.Module) -> bool:
    detector = YieldDetector()
    detector.visit(tree)
    return detector.has_yield


def _transform_last_expr_to_return(body: list[ast.stmt]) -> list[ast.stmt]:
    if not body:
        return body

    last_stmt = body[-1]
    if isinstance(last_stmt, ast.Expr):
        return_stmt = ast.Return(value=last_stmt.value)
        ast.copy_location(return_stmt, last_stmt)
        return body[:-1] + [return_stmt]

    return body


def _wrap_in_do_function(tree: ast.Module, filename: str = "<doeff-code>") -> ast.Module:
    """Wrap module body in @do decorated generator function.

    User code with top-level yields:
        config = yield Ask("config")
        fix_issue("X", config)

    Becomes:
        @do
        def __doeff_main__():
            config = yield Ask("config")
            return fix_issue("X", config)
        __doeff_result__ = __doeff_main__()
    """
    imports: list[ast.stmt] = []
    body: list[ast.stmt] = []

    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            imports.append(stmt)
        else:
            body.append(stmt)

    transformed_body = _transform_last_expr_to_return(body)

    func_def = ast.FunctionDef(
        name="__doeff_main__",
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=transformed_body if transformed_body else [ast.Pass()],
        decorator_list=[ast.Name(id="do", ctx=ast.Load())],
        returns=None,
    )
    ast.fix_missing_locations(func_def)

    do_import = ast.ImportFrom(
        module="doeff",
        names=[ast.alias(name="do", asname=None)],
        level=0,
    )
    ast.fix_missing_locations(do_import)

    call_main = ast.Assign(
        targets=[ast.Name(id="__doeff_result__", ctx=ast.Store())],
        value=ast.Call(
            func=ast.Name(id="__doeff_main__", ctx=ast.Load()),
            args=[],
            keywords=[],
        ),
    )
    ast.fix_missing_locations(call_main)

    new_body = [do_import] + imports + [func_def, call_main]
    new_tree = ast.Module(body=new_body, type_ignores=[])
    ast.fix_missing_locations(new_tree)

    return new_tree


def _wrap_last_expr_as_result(tree: ast.Module) -> ast.Module:
    """For code without yield, assign last expression to __doeff_result__."""
    if not tree.body:
        return tree

    last_stmt = tree.body[-1]
    if isinstance(last_stmt, ast.Expr):
        assign = ast.Assign(
            targets=[ast.Name(id="__doeff_result__", ctx=ast.Store())],
            value=last_stmt.value,
        )
        ast.copy_location(assign, last_stmt)
        new_body = tree.body[:-1] + [assign]
        new_tree = ast.Module(body=new_body, type_ignores=[])
        ast.fix_missing_locations(new_tree)
        return new_tree

    return tree


@dataclass
class TransformResult:
    """Result of code transformation."""

    code: Any
    has_yield: bool
    original_source: str


def transform_doeff_code(
    source: str,
    filename: str = "<doeff-code>",
) -> TransformResult:
    """Transform doeff code for execution.

    If code contains top-level yields, wraps in @do function.
    Last expression becomes the return/result value.
    """
    tree = ast.parse(source, filename=filename, mode="exec")
    has_yield = _has_toplevel_yield(tree)

    if has_yield:
        transformed_tree = _wrap_in_do_function(tree, filename)
    else:
        transformed_tree = _wrap_last_expr_as_result(tree)

    code = compile(
        transformed_tree,
        filename,
        "exec",
        dont_inherit=True,
    )

    return TransformResult(
        code=code,
        has_yield=has_yield,
        original_source=source,
    )


def execute_doeff_code(
    source: str,
    filename: str = "<doeff-code>",
    extra_globals: dict[str, Any] | None = None,
) -> Any:
    """Execute doeff code and return the resulting Program or value."""
    transform_result = transform_doeff_code(source, filename)

    exec_globals: dict[str, Any] = {
        "__name__": "__main__",
        "__file__": filename,
        "__builtins__": __builtins__,
    }

    if extra_globals:
        exec_globals.update(extra_globals)

    exec(transform_result.code, exec_globals)

    return exec_globals.get("__doeff_result__")
