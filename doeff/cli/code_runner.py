from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any


def _transform_last_expr_to_return(body: list[ast.stmt]) -> list[ast.stmt]:
    if not body:
        return body

    last_stmt = body[-1]
    if isinstance(last_stmt, ast.Expr):
        lift_call = ast.Call(
            func=ast.Attribute(
                value=ast.Name(id="Program", ctx=ast.Load()),
                attr="lift",
                ctx=ast.Load(),
            ),
            args=[last_stmt.value],
            keywords=[],
        )
        yield_expr = ast.Yield(value=lift_call)
        return_stmt = ast.Return(value=yield_expr)
        ast.copy_location(return_stmt, last_stmt)
        return body[:-1] + [return_stmt]

    return body


def _wrap_in_do_function(tree: ast.Module) -> ast.Module:
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
        names=[ast.alias(name="do", asname=None), ast.alias(name="Program", asname=None)],
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


@dataclass
class TransformResult:
    code: Any
    original_source: str


def transform_doeff_code(
    source: str,
    filename: str = "<doeff-code>",
) -> TransformResult:
    tree = ast.parse(source, filename=filename, mode="exec")
    transformed_tree = _wrap_in_do_function(tree)

    code = compile(
        transformed_tree,
        filename,
        "exec",
        dont_inherit=True,
    )

    return TransformResult(code=code, original_source=source)


def execute_doeff_code(  # nosemgrep: doeff-no-typing-any-in-public-api
    source: str,
    filename: str = "<doeff-code>",
    extra_globals: dict[str, Any] | None = None,
) -> Any:
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
