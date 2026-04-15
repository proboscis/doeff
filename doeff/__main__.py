"""doeff CLI entry point.

Usage:
    doeff run --program myapp.module.program
    doeff run --program myapp.program --interpreter myapp.interpreter
    doeff run --program myapp.program --env myapp.env
    doeff run --program myapp.program --set name=Alice --set count=10
    doeff run -c 'from doeff import Ask; yield Ask("key")'
    doeff run --program myapp.program --apply myapp.transforms.wrap
    doeff run --program myapp.program -- script.py
"""

import argparse
import json
import sys
from collections.abc import Iterable
from typing import Any, Callable, cast

from doeff.cli.profiling import print_profiling_status
from doeff.cli.runbox import maybe_create_runbox_record
from doeff.cli.run_services import (
    RunContext,
    execute,
    import_symbol,
    resolve_context,
)


class RunArgs(argparse.Namespace):
    command: str
    func: Callable[["RunArgs"], int]
    program: str | None
    code: str | None
    interpreter: str | None
    envs: list[str] | None
    set_vars: list[str] | None
    apply: list[str] | None
    transform: list[str] | None
    format: str
    no_runbox: bool
    script: str | None


def _parse_set_vars(set_vars: list[str] | None) -> dict[str, tuple[str, Any]]:
    """Parse --set KEY=VALUE args into a dict of ``(raw, resolved)`` pairs.

    If VALUE is wrapped in braces like ``{myapp.module.symbol}``, the symbol
    is imported and returned as *resolved*; the original brace string is kept
    as *raw* so that :class:`DoeffRunContext` can reconstruct the CLI command.
    For plain strings, raw and resolved are identical.
    """
    if not set_vars:
        return {}
    result: dict[str, tuple[str, Any]] = {}
    for item in set_vars:
        if "=" not in item:
            raise ValueError(f"Invalid --set format: {item!r} (expected KEY=VALUE)")
        key, value = item.split("=", 1)
        if not key:
            raise ValueError(f"Invalid --set format: {item!r} (empty key)")
        if value.startswith("{") and value.endswith("}"):
            symbol_path = value[1:-1]
            if not symbol_path:
                raise ValueError(f"Invalid --set format: {item!r} (empty import path)")
            result[key] = (value, import_symbol(symbol_path))
        else:
            result[key] = (value, value)
    return result


def _render_output(resolved: Any, value: Any, fmt: str) -> None:
    if fmt == "json":
        payload = {
            "status": "ok",
            "program": resolved.program_path,
            "interpreter": resolved.interpreter_path,
            "envs": resolved.env_paths,
            "result": _json_safe(value),
            "result_type": type(value).__name__,
        }
        print(json.dumps(payload))
    else:
        print(value)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _reported_exception(exc: BaseException) -> BaseException:
    """Unwrap chained exceptions to find the most relevant one."""
    seen = set()
    current = exc
    while current.__cause__ is not None and id(current.__cause__) not in seen:
        seen.add(id(current))
        current = current.__cause__
    return current


def handle_run_with_script(resolved: Any, value: Any, script: str) -> int:
    """Execute a user script with injected variables after program run."""
    if script == "-":
        script = sys.stdin.read()
    if not script or not script.strip():
        print("Error: No script provided", file=sys.stderr)
        return 1

    script_globals = {
        "__name__": "__main__",
        "__builtins__": __builtins__,
        "program": resolved.program_instance,
        "value": value,
        "interpreter": import_symbol(resolved.interpreter_path),
        "sys": sys,
        "json": json,
    }

    try:
        exec(script, script_globals)
    except Exception as exc:
        print(f"Error executing script: {exc}", file=sys.stderr)
        return 1
    return 0


def handle_run_code(args: RunArgs) -> int:
    from doeff.cli.code_runner import execute_doeff_code

    code = args.code
    if code == "-":
        code = sys.stdin.read()
    if not code or not code.strip():
        print("Error: No code provided", file=sys.stderr)
        return 1

    maybe_create_runbox_record(skip_runbox=args.no_runbox)
    program = execute_doeff_code(code, filename="<doeff-code>")

    ctx = RunContext(
        program_path=None,
        program_instance=program,
        interpreter_path=args.interpreter,
        env_paths=args.envs or [],
        set_vars=_parse_set_vars(args.set_vars),
        apply_paths=args.apply or [],
        transformer_paths=args.transform or [],
        output_format=args.format,
    )
    resolved = resolve_context(ctx)
    value = execute(resolved)
    _render_output(resolved, value, args.format)
    return 0


def handle_run(args: RunArgs) -> int:
    if args.code is not None:
        return handle_run_code(args)

    if not args.program:
        print("Error: --program is required when not using -c", file=sys.stderr)
        return 1

    maybe_create_runbox_record(skip_runbox=args.no_runbox)

    ctx = RunContext(
        program_path=args.program,
        program_instance=None,
        interpreter_path=args.interpreter,
        env_paths=args.envs or [],
        set_vars=_parse_set_vars(args.set_vars),
        apply_paths=args.apply or [],
        transformer_paths=args.transform or [],
        output_format=args.format,
    )

    resolved = resolve_context(ctx)

    script = args.script
    if script == "-" or (script is not None and script.strip()):
        value = execute(resolved)
        return handle_run_with_script(resolved, value, script)

    value = execute(resolved)
    _render_output(resolved, value, args.format)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doeff", description="Run doeff programs"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Execute a Program via an interpreter",
        description=(
            "Execute a Program via an interpreter. Supports auto-discovery.\n\n"
            "Examples:\n"
            "  doeff run --program myapp.program\n"
            "  doeff run --program myapp.program --interpreter myapp.interpreter\n"
            "  doeff run -c 'from doeff import Ask; yield Ask(\"key\")'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    source_group = run_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--program", help="Fully-qualified path to the Program instance")
    source_group.add_argument(
        "-c", dest="code", metavar="CODE",
        help="Execute doeff code directly. Use '-' to read from stdin.",
    )
    run_parser.add_argument("--interpreter", help="Callable that accepts the Program")
    run_parser.add_argument(
        "--env", action="append", dest="envs",
        help="Environment dict or Program[dict] (can repeat)",
    )
    run_parser.add_argument(
        "--set", action="append", dest="set_vars", metavar="KEY=VALUE",
        help="Set an env key-value pair (can repeat). Use {path} to import a symbol, e.g. --set model={myapp.gpt4}",
    )
    run_parser.add_argument(
        "--apply", action="append",
        help="Kleisli arrow T -> Program[U] to apply before execution (can repeat)",
    )
    run_parser.add_argument(
        "--transform", action="append",
        help="Program -> Program transformer (can repeat)",
    )
    run_parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text)",
    )
    run_parser.add_argument(
        "--no-runbox", action="store_true",
        help="Skip automatic runbox record capture",
    )
    run_parser.add_argument(
        "script", nargs="?",
        help="Python script to execute after running. Use '-' for stdin.",
    )
    run_parser.set_defaults(func=handle_run)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    print_profiling_status()
    parser = build_parser()
    args = cast(RunArgs, parser.parse_args(list(argv) if argv is not None else None, RunArgs()))
    try:
        return args.func(args)
    except Exception as exc:
        reported = _reported_exception(exc)

        # Try to show doeff traceback if available
        tb = getattr(reported, "__doeff_traceback__", None)
        if tb is not None:
            from doeff.traceback import format_default
            format_default(reported)  # prints to stderr

        if args.format == "json":
            payload = {
                "status": "error",
                "error": reported.__class__.__name__,
                "message": str(reported),
            }
            print(json.dumps(payload))
        else:
            print(f"Error: {reported}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
