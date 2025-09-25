from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from doeff import Program, ProgramInterpreter, RunResult
from doeff.kleisli import KleisliProgram
from doeff.types import capture_traceback


@dataclass
class RunContext:
    program_path: str
    interpreter_path: str
    apply_path: Optional[str]
    transformer_paths: list[str]
    output_format: str


def _import_symbol(path: str) -> Any:
    if ":" in path:
        module_name, attr_path = path.split(":", 1)
        module = importlib.import_module(module_name)
        return _resolve_attr(module, attr_path)
    parts = path.split(".")
    if len(parts) < 2:
        raise ValueError(
            f"'{path}' is not a fully-qualified symbol. Use module.symbol format."
        )
    module_name = ".".join(parts[:-1])
    attr_name = parts[-1]
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def _resolve_attr(obj: Any, attr_path: str) -> Any:
    current = obj
    for attr in attr_path.split("."):
        current = getattr(current, attr)
    return current


def _ensure_program(obj: Any, description: str) -> Program[Any]:
    if isinstance(obj, Program):
        return obj
    if callable(obj):
        produced = obj()
        if isinstance(produced, Program):
            return produced
    raise TypeError(f"{description} did not resolve to a Program instance.")


def _ensure_kleisli(obj: Any, description: str) -> Callable[[Program[Any]], Program[Any]]:
    if isinstance(obj, KleisliProgram):
        return lambda prog: obj(prog)
    if callable(obj):
        return lambda prog: _ensure_program(obj(prog), description)
    raise TypeError(f"{description} is not callable and cannot transform a Program.")


def _ensure_transformer(obj: Any, description: str) -> Callable[[Program[Any]], Program[Any]]:
    if callable(obj):
        def _wrapper(prog: Program[Any]) -> Program[Any]:
            result = obj(prog)
            return _ensure_program(result, description)
        return _wrapper
    raise TypeError(f"{description} is not callable and cannot transform a Program.")


def _call_interpreter(func: Callable[..., Any], program: Program[Any]) -> Any:
    signature = inspect.signature(func)
    bound: inspect.BoundArguments
    try:
        bound = signature.bind_partial(program)
    except TypeError as exc:
        raise TypeError(
            "Interpreter must accept a Program as its first positional argument"
        ) from exc
    # Ensure missing required parameters
    bound.apply_defaults()
    for param in signature.parameters.values():
        if param.kind in {param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD}:
            if param.default is inspect._empty and param.name not in bound.arguments:
                raise TypeError(
                    f"Interpreter '{func.__name__}' requires argument '{param.name}' which is not provided."
                )
    result = func(*bound.args, **bound.kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _finalize_result(value: Any) -> Any:
    if isinstance(value, Program):
        interpreter = ProgramInterpreter()
        run_result = asyncio.run(interpreter.run(value))
        return _unwrap_run_result(run_result)
    if isinstance(value, RunResult):
        return _unwrap_run_result(value)
    return value


def _unwrap_run_result(result: RunResult[Any]) -> Any:
    try:
        return result.value
    except Exception as exc:  # noqa: BLE001 - surface error context
        raise RuntimeError("Program execution failed") from exc


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def handle_run(args: argparse.Namespace) -> int:
    context = RunContext(
        program_path=args.program,
        interpreter_path=args.interpreter,
        apply_path=args.apply,
        transformer_paths=args.transform or [],
        output_format=args.format,
    )

    program_obj = _import_symbol(context.program_path)
    program = _ensure_program(program_obj, "--program")

    if context.apply_path:
        kleisli_obj = _import_symbol(context.apply_path)
        kleisli = _ensure_kleisli(kleisli_obj, "--apply")
        program = kleisli(program)

    if context.transformer_paths:
        for transform_path in context.transformer_paths:
            transformer_obj = _import_symbol(transform_path)
            transformer = _ensure_transformer(transformer_obj, f"transformer {transform_path}")
            program = transformer(program)

    interpreter_obj = _import_symbol(context.interpreter_path)
    if isinstance(interpreter_obj, ProgramInterpreter):
        result = asyncio.run(interpreter_obj.run(program))
        final_value = _unwrap_run_result(result)
    else:
        interpreter_callable = interpreter_obj
        if not callable(interpreter_callable):
            raise TypeError("--interpreter must resolve to a callable or ProgramInterpreter instance")
        result = _call_interpreter(interpreter_callable, program)
        final_value = _finalize_result(result)

    if context.output_format == "json":
        payload = {
            "status": "ok",
            "program": context.program_path,
            "interpreter": context.interpreter_path,
            "apply": context.apply_path,
            "transformers": context.transformer_paths,
            "result": _json_safe(final_value),
            "result_type": type(final_value).__name__,
        }
        print(json.dumps(payload))
    else:
        print(final_value)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doeff", description="Utilities for working with doeff programs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Execute a Program via an interpreter")
    run_parser.add_argument("--program", required=True, help="Fully-qualified path to the Program instance")
    run_parser.add_argument(
        "--interpreter", required=True, help="Callable that accepts the Program as its first argument"
    )
    run_parser.add_argument(
        "--apply",
        help="Optional KleisliProgram to apply before execution (expects the Program as its first argument)",
    )
    run_parser.add_argument(
        "--transform",
        action="append",
        help="Optional Program transformer(s) to apply sequentially (Program -> Program)",
    )
    run_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    run_parser.set_defaults(func=handle_run)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        captured = capture_traceback(exc)
        if getattr(args, "format", "text") == "json":
            payload = {
                "status": "error",
                "error": exc.__class__.__name__,
                "message": str(exc),
            }
            if captured is not None:
                payload["traceback"] = captured.format(condensed=False, max_lines=200)
            print(json.dumps(payload))
        else:
            if captured is not None:
                print(captured.format(condensed=False, max_lines=200), file=sys.stderr)
            else:
                print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
