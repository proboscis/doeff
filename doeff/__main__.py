import argparse
import asyncio
import importlib
import importlib.util
import inspect
import json
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

from doeff import Program, RunResult
from doeff.analysis import EffectCallTree
from doeff.cli.profiling import is_profiling_enabled, print_profiling_status, profile
from doeff.cli.runbox import maybe_create_runbox_record
from doeff.kleisli import KleisliProgram
from doeff.run_services import (
    ProgramBuilder,
    ResolvedRunContext,
    RunCommand,
    RunContext,
    RunExecutionResult,
    RunServices,
    SymbolResolver,
    _call_interpreter,
    _call_tree_ascii,
    _discover_topmost_interpreter,
    _ensure_kleisli,
    _ensure_program,
    _ensure_transformer,
    _finalize_result,
    _import_symbol,
    _json_safe,
    _reported_exception,
    _resolve_attr,
    _run_result_report,
)
from doeff.rust_vm import RunResult as VmRunResult
from doeff.rust_vm import default_handlers
from doeff.rust_vm import run as vm_run
from doeff.types import capture_traceback

sync_handlers_preset = default_handlers()


def sync_run(
    program: Program[Any],
    handlers: list[Any] | None = None,
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> RunResult[Any]:
    selected_handlers = sync_handlers_preset if handlers is None else handlers
    return vm_run(
        program,
        handlers=selected_handlers,
        env=env,
        store=store,
        print_doeff_trace=False,
    )


class RunArgs(argparse.Namespace):
    command: str
    func: Callable[["RunArgs"], int]
    program: str | None
    code: str | None
    interpreter: str | None
    envs: list[str] | None
    apply: str | None
    transform: list[str] | None
    format: str
    report: bool
    report_verbose: bool
    no_runbox: bool
    script: str | None


def _render_run_output(context: ResolvedRunContext, execution: RunExecutionResult) -> None:
    final_value = execution.final_value
    run_result = execution.run_result

    if context.output_format == "json":
        payload = {
            "status": "ok",
            "program": context.program_path,
            "interpreter": context.interpreter_path,
            "envs": context.env_paths,
            "apply": context.apply_path,
            "transformers": context.transformer_paths,
            "result": _json_safe(final_value),
            "result_type": type(final_value).__name__,
        }
        if context.report and run_result is not None:
            payload["report"] = _run_result_report(run_result, verbose=context.report_verbose)
            if execution.call_tree_ascii is not None:
                payload["call_tree"] = execution.call_tree_ascii
        print(json.dumps(payload))
        return

    print(final_value)
    if context.report:
        if run_result is not None:
            print()
            print(_run_result_report(run_result, verbose=context.report_verbose))
        else:
            print(
                "\n(No run report available: interpreter did not return a RunResult)",
                file=sys.stderr,
            )


def handle_run_with_script(context: RunContext, script: str | None) -> int:
    """Execute program and run user script with injected variables.

    Args:
        context: Run context for program execution
        script: Script content or "-" to read from stdin

    Returns:
        Exit code (0 for success, 1 for error)
    """
    # Read script from stdin if "-" is specified
    if script == "-":
        script = sys.stdin.read()

    if not script or not script.strip():
        print("Error: No script provided", file=sys.stderr)
        return 1

    # Execute the program first
    command = RunCommand(context)
    resolved_context, execution = command.execute()

    # Extract values to inject
    # Get the prepared program (with envs, transforms, etc. applied)
    program = command._prepare_program(resolved_context)
    value = execution.final_value
    interpreter_obj = command._resolver.resolve(resolved_context.interpreter_path)

    # Create a namespace for the script execution
    script_globals = {
        "__name__": "__main__",
        "__builtins__": __builtins__,
        "program": program,
        "value": value,
        "interpreter": interpreter_obj,
        "RunResult": RunResult,
        "Program": Program,
        "run": vm_run,
        "default_handlers": default_handlers,
    }

    # Add any additional useful imports
    try:
        script_globals["sys"] = sys
        script_globals["json"] = json
    except NameError:
        pass

    # Execute the user script
    try:
        exec(script, script_globals)
    except Exception as exc:
        captured = capture_traceback(exc)
        if captured is not None:
            print(captured.format(condensed=False, max_lines=200), file=sys.stderr)
        else:
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

    # Create runbox record before execution (if runbox CLI is available)
    skip_runbox = args.no_runbox
    maybe_create_runbox_record(skip_runbox=skip_runbox)
    program: Program[Any] = execute_doeff_code(code, filename="<doeff-code>")

    context = RunContext(
        program_path=None,
        program_instance=program,
        interpreter_path=args.interpreter,
        env_paths=args.envs or [],
        apply_path=args.apply,
        transformer_paths=args.transform or [],
        output_format=args.format,
        report=args.report,
        report_verbose=args.report_verbose,
    )

    command = RunCommand(context)
    resolved_context, execution = command.execute()
    _render_run_output(resolved_context, execution)
    return 0


def handle_run(args: RunArgs) -> int:
    code_arg = args.code
    if code_arg is not None:
        return handle_run_code(args)

    if not args.program:
        print("Error: --program is required when not using -c", file=sys.stderr)
        return 1

    # Create runbox record before execution (if runbox CLI is available)
    skip_runbox = args.no_runbox
    maybe_create_runbox_record(skip_runbox=skip_runbox)
    context = RunContext(
        program_path=args.program,
        program_instance=None,
        interpreter_path=args.interpreter,
        env_paths=args.envs or [],
        apply_path=args.apply,
        transformer_paths=args.transform or [],
        output_format=args.format,
        report=args.report,
        report_verbose=args.report_verbose,
    )

    script = args.script
    if script == "-" or (script is not None and script.strip()):
        return handle_run_with_script(context, script)

    command = RunCommand(context)
    resolved_context, execution = command.execute()
    _render_run_output(resolved_context, execution)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doeff", description="Utilities for working with doeff programs"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Execute a Program via an interpreter",
        description=(
            "Execute a Program via an interpreter. Supports auto-discovery of interpreters "
            "and environments. Use --program for module paths or -c for inline code.\n\n"
            "Examples:\n"
            "  # Basic execution with explicit interpreter\n"
            "  doeff run --program myapp.program --interpreter myapp.interpreter\n\n"
            "  # With auto-discovery (finds # doeff: interpreter, default)\n"
            "  doeff run --program myapp.features.auth.login_program\n\n"
            "  # Inline code with -c (auto-discovers interpreter)\n"
            "  doeff run -c 'from doeff import Program; Program.pure(42)'\n\n"
            "  # Inline code with top-level yield (heredoc)\n"
            "  doeff run -c - <<'EOF'\n"
            "    from doeff import Ask, Tell\n"
            "  config = yield Ask('config')\n"
            "  yield Tell(f'Got config: {config}')\n"
            "  config\n"
            "  EOF"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    source_group = run_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--program", help="Fully-qualified path to the Program instance")
    source_group.add_argument(
        "-c",
        dest="code",
        metavar="CODE",
        help=(
            "Execute doeff code directly. Supports top-level yield statements. "
            "Use '-' to read from stdin."
        ),
    )
    run_parser.add_argument(
        "--interpreter",
        help="Callable that accepts the Program as its first argument (auto-discovered if not specified)",
    )
    run_parser.add_argument(
        "--env",
        action="append",
        dest="envs",
        help="Environment dict or Program[dict] to provide values (can be specified multiple times, auto-discovered if not specified)",
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
    run_parser.add_argument(
        "--report",
        action="store_true",
        help="Print the RunResult report (includes effect call tree).",
    )
    run_parser.add_argument(
        "--report-verbose",
        action="store_true",
        help="Use verbose mode when printing the RunResult report.",
    )
    run_parser.add_argument(
        "--no-runbox",
        action="store_true",
        help="Skip automatic runbox record capture (only affects runs when runbox CLI is installed).",
    )
    run_parser.add_argument(
        "script",
        nargs="?",
        help=(
            "Python script to execute after running the program. Use '-' to read from stdin "
            "(e.g., with heredoc: doeff run --program myapp.program - <<'PY' ... PY).\n\n"
            "Available variables in script:\n"
            "  - program: The executed Program (with envs/transforms applied)\n"
            "  - value: The final execution result\n"
            "  - interpreter: The interpreter function\n"
            "  - Program, RunResult: Type classes\n"
            "  - run, default_handlers: Execution functions\n"
            "  - sys, json: Standard library modules\n\n"
            "Example:\n"
            "  doeff run --program myapp.program - <<'PY'\n"
            "  print(f'Result: {value}')\n"
            "  result = run(program, handlers=default_handlers())\n"
            "  print(f'Re-run: {result.value}')\n"
            "  PY"
        ),
    )
    run_parser.set_defaults(func=handle_run)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = cast(RunArgs, parser.parse_args(list(argv) if argv is not None else None, RunArgs()))
    try:
        return args.func(args)
    except Exception as exc:
        from doeff.traceback import get_attached_doeff_traceback

        reported_exc = _reported_exception(exc)
        doeff_tb = get_attached_doeff_traceback(reported_exc)
        captured = capture_traceback(reported_exc)
        if args.format == "json":
            payload = {
                "status": "error",
                "error": reported_exc.__class__.__name__,
                "message": str(reported_exc),
            }
            if doeff_tb is not None:
                payload["traceback"] = doeff_tb.format_default()
            elif captured is not None:
                payload["traceback"] = captured.format(condensed=False, max_lines=200)
            print(json.dumps(payload))
        elif doeff_tb is not None:
            print(doeff_tb.format_default(), file=sys.stderr)
        elif captured is not None:
            print(captured.format(condensed=False, max_lines=200), file=sys.stderr)
        else:
            print(f"Error: {reported_exc}", file=sys.stderr)
        return 1


"""
Feature Update Plan:

- We want to add some way to specify the `default` for `interpreter` and `env`
# Default Interpreter
given a program's module path, we find the default interpreter path to use if not specified for doeff run.

# Interpreter
- We treat a function with a single positional argument of type Program as an interpreter.
- An interpreter must have `# doeff: interpreter` in its docstring.
- We find default interpreter by recursively searching from the top-level module down to the module containing the program.
- If multiple is found, then we use the closest one to the program's module.
- An interpreter which can be used as default must have `default` after `doeff: ...` in its docstring.
Example:
```
# doeff run some.module.a.b.c.program
# some.module.__init__.py
def my_interpreter(prog: Program[Any])->Any:
    \"""
    doeff: interpreter, default
    \"""

# some.module.a.__init__.py
def another_interpreter(prog: Program[Any])->Any:
    \"""
    doeff: interpreter, default
    \"""
# this another_interpreter should be used for some.module.a.b.c.program because it's closer.

# Env Specification
For doeff run, we want to specify the env to use.
`--env some.module.env` where `some.module.env` is a dict-like object.
This is equivalent to wrapping the program with Local effect.
For example:
`doeff run --program some.module.a.b.c.program --env some.module.env`
```python
# some/module/env.py

default_env:Program[dict] = Program.pure(dict(
    some_kleisli_service=do_something,
    config_value = 42
    ...
))

# some/module/a/b/c.py
program: Program[...] = ...
...
Then it is equivalent to:
```
program:Program[Any] = _import_symbol("some.module.a.b.c:program")
env_p:Program[dict] = _import_symbol("some.module.env:default_env")
user_interpreter(Local(env, program))
```
This means Local effect must be updated to accept Program[dict] as its first argument, in addition to dict.

# Implementation
- default value search feature should be implemented with rust with pyo3 for performance, to be called from python CLI

# Doeff-Indexer
- We already have doeff-indexer that finds # doeff: ... from func name, but not from docstring. we need to update this indexer as well.
also, if that indexer can be used from python, we should use it rather than re-implementing the logic in python.

"""

if __name__ == "__main__":
    sys.exit(main())
