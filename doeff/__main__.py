from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import inspect
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from doeff import Program, ProgramInterpreter, RunResult
from doeff.analysis import EffectCallTree
from doeff.cli.profiling import is_profiling_enabled, print_profiling_status, profile
from doeff.kleisli import KleisliProgram
from doeff.types import capture_traceback


@dataclass
class RunContext:
    program_path: str
    interpreter_path: str | None
    env_paths: list[str]
    apply_path: str | None
    transformer_paths: list[str]
    output_format: str
    report: bool
    report_verbose: bool


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
    from doeff.types import Program

    if isinstance(obj, Program):
        return obj  # type: ignore[return-value]
    if callable(obj):
        produced = obj()
        if isinstance(produced, Program):
            return produced  # type: ignore[return-value]
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
        if (
            param.kind in {param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD}
            and param.default is inspect._empty
            and param.name not in bound.arguments
        ):
            raise TypeError(
                f"Interpreter '{func.__name__}' requires argument '{param.name}' which is not provided."
            )
    result = func(*bound.args, **bound.kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _finalize_result(value: Any) -> tuple[Any, RunResult[Any] | None]:
    from doeff.program import Program as ProgramType

    if isinstance(value, ProgramType):
        interpreter = ProgramInterpreter()
        run_result = interpreter.run(value)
        return _unwrap_run_result(run_result), run_result
    if isinstance(value, RunResult):
        return _unwrap_run_result(value), value
    return value, None


def _unwrap_run_result(result: RunResult[Any]) -> Any:
    try:
        return result.value
    except Exception as exc:
        raise RuntimeError("Program execution failed") from exc


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def _call_tree_ascii(run_result: RunResult[Any]) -> str | None:
    observations = getattr(run_result.context, "effect_observations", None)
    if not observations:
        return None

    tree = EffectCallTree.from_observations(observations)
    ascii_tree = tree.visualize_ascii()
    if ascii_tree == "(no effects)":
        return None
    return ascii_tree


def handle_run(args: argparse.Namespace) -> int:
    from doeff.cli.discovery import (
        IndexerBasedDiscovery,
        StandardEnvMerger,
        StandardSymbolLoader,
    )
    from doeff.effects import Local

    with profile("CLI discovery and execution"):
        print_profiling_status()
        context = RunContext(
            program_path=args.program,
            interpreter_path=args.interpreter,
            env_paths=args.envs or [],
            apply_path=args.apply,
            transformer_paths=args.transform or [],
            output_format=args.format,
            report=getattr(args, "report", False),
            report_verbose=getattr(args, "report_verbose", False),
        )

        # Initialize discovery services
        with profile("Initialize discovery services", indent=1):
            loader = StandardSymbolLoader()
            discovery = IndexerBasedDiscovery(symbol_loader=loader)
            merger = StandardEnvMerger(symbol_loader=loader)

        # Auto-discover interpreter if not specified
        if context.interpreter_path is None:
            with profile("Auto-discover interpreter", indent=1):
                discovered_interp = discovery.find_default_interpreter(context.program_path)
                if discovered_interp is None:
                    raise RuntimeError(
                        f"No default interpreter found for {context.program_path}. "
                        "Please specify --interpreter or add '# doeff: interpreter, default' marker to an interpreter function."
                    )
                if is_profiling_enabled():
                    print(f"[DOEFF][DISCOVERY] Interpreter: {discovered_interp}", file=sys.stderr)
                context = replace(context, interpreter_path=discovered_interp)

        # Auto-discover envs if not specified
        if not context.env_paths:
            with profile("Auto-discover environments", indent=1):
                discovered_envs = discovery.discover_default_envs(context.program_path)
                if is_profiling_enabled():
                    if discovered_envs:
                        print(f"[DOEFF][DISCOVERY] Environments ({len(discovered_envs)}):", file=sys.stderr)
                        for env_path in discovered_envs:
                            print(f"[DOEFF][DISCOVERY]   - {env_path}", file=sys.stderr)
                    else:
                        print("[DOEFF][DISCOVERY] Environments: none found", file=sys.stderr)
                context = replace(context, env_paths=discovered_envs)

        # Check for ~/.doeff.py and load __default_env__ if present
        default_env_path = None
        from pathlib import Path
        doeff_config_file = Path.home() / ".doeff.py"

        if doeff_config_file.exists():
            with profile("Load ~/.doeff.py", indent=1):
                # Load the module dynamically
                spec = importlib.util.spec_from_file_location("_doeff_config", doeff_config_file)
                if spec and spec.loader:
                    config_module = importlib.util.module_from_spec(spec)
                    sys.modules["_doeff_config"] = config_module
                    spec.loader.exec_module(config_module)

                    if hasattr(config_module, "__default_env__"):
                        default_env_path = "_doeff_config.__default_env__"
                        if is_profiling_enabled():
                            print(f"[DOEFF][DISCOVERY] Found __default_env__ in ~/.doeff.py", file=sys.stderr)

        with profile("Load program", indent=1):
            program_obj = _import_symbol(context.program_path)
            program = _ensure_program(program_obj, "--program")

        # Merge and inject environments if any
        env_sources = []
        if default_env_path:
            env_sources.append(default_env_path)
        env_sources.extend(context.env_paths)

        if env_sources:
            merged_env_program = merger.merge_envs(env_sources)
            # Run the merged env to get the dict
            temp_interpreter = ProgramInterpreter()
            env_result = temp_interpreter.run(merged_env_program)
            merged_env_dict = env_result.value
            # Wrap program with Local effect to inject environment
            local_effect = Local(merged_env_dict, program)
            program = Program.from_effect(local_effect)

        if context.apply_path:
            with profile(f"Apply kleisli {context.apply_path}", indent=1):
                kleisli_obj = _import_symbol(context.apply_path)
                kleisli = _ensure_kleisli(kleisli_obj, "--apply")
                program = kleisli(program)
                if is_profiling_enabled():
                    print(f"[DOEFF][DISCOVERY] Applied kleisli: {context.apply_path}", file=sys.stderr)

        if context.transformer_paths:
            for transform_path in context.transformer_paths:
                with profile(f"Apply transform {transform_path}", indent=1):
                    transformer_obj = _import_symbol(transform_path)
                    transformer = _ensure_transformer(transformer_obj, f"transformer {transform_path}")
                    program = transformer(program)
                    if is_profiling_enabled():
                        print(f"[DOEFF][DISCOVERY] Applied transform: {transform_path}", file=sys.stderr)

        run_result: RunResult[Any] | None = None

        with profile("Load and run interpreter", indent=1):
            interpreter_obj = _import_symbol(context.interpreter_path)
            if isinstance(interpreter_obj, ProgramInterpreter):
                run_result = interpreter_obj.run(program)
                final_value = _unwrap_run_result(run_result)
            else:
                interpreter_callable = interpreter_obj
                if not callable(interpreter_callable):
                    raise TypeError("--interpreter must resolve to a callable or ProgramInterpreter instance")
                result = _call_interpreter(interpreter_callable, program)
                final_value, run_result = _finalize_result(result)

    call_tree_ascii = _call_tree_ascii(run_result) if run_result is not None else None

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
            payload["report"] = run_result.display(verbose=context.report_verbose)
            if call_tree_ascii is not None:
                payload["call_tree"] = call_tree_ascii
        print(json.dumps(payload))
    else:
        print(final_value)
        if context.report:
            if run_result is not None:
                print()  # Blank line before report
                print(run_result.display(verbose=context.report_verbose))
            else:
                print(
                    "\n(No run report available: interpreter did not return a RunResult)",
                    file=sys.stderr,
                )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doeff", description="Utilities for working with doeff programs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Execute a Program via an interpreter")
    run_parser.add_argument("--program", required=True, help="Fully-qualified path to the Program instance")
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
    run_parser.set_defaults(func=handle_run)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return args.func(args)
    except Exception as exc:
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
        elif captured is not None:
            print(captured.format(condensed=False, max_lines=200), file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
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
user_interpreter(Program.from_effect(Local(env, program)))
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
