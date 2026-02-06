from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import inspect
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from doeff import Program, RunResult
from doeff.analysis import EffectCallTree
from doeff.cesk.run import sync_handlers_preset, sync_run
from doeff.cli.profiling import is_profiling_enabled, print_profiling_status, profile
from doeff.cli.runbox import maybe_create_runbox_record
from doeff.kleisli import KleisliProgram
from doeff.types import capture_traceback


@dataclass
class RunContext:
    program_path: str | None
    program_instance: Program[Any] | None
    interpreter_path: str | None
    env_paths: list[str]
    apply_path: str | None
    transformer_paths: list[str]
    output_format: str
    report: bool
    report_verbose: bool


@dataclass
class ResolvedRunContext:
    program_path: str | None
    program_instance: Program[Any] | None
    interpreter_path: str
    env_paths: list[str]
    apply_path: str | None
    transformer_paths: list[str]
    output_format: str
    report: bool
    report_verbose: bool


@dataclass
class RunExecutionResult:
    final_value: Any
    run_result: RunResult[Any] | None
    call_tree_ascii: str | None


class SymbolResolver:
    """Helper for importing symbols while caching module lookups."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def resolve(self, path: str) -> Any:
        if path not in self._cache:
            self._cache[path] = _import_symbol(path)
        return self._cache[path]

    def program(self, path: str, description: str) -> Program[Any]:
        obj = self.resolve(path)
        return _ensure_program(obj, description)

    def kleisli(self, path: str, description: str) -> Callable[[Program[Any]], Program[Any]]:
        obj = self.resolve(path)
        return _ensure_kleisli(obj, description)

    def transformer(self, path: str, description: str) -> Callable[[Program[Any]], Program[Any]]:
        obj = self.resolve(path)
        return _ensure_transformer(obj, description)


class RunServices:
    def __init__(self) -> None:
        from doeff.cli.discovery import (
            IndexerBasedDiscovery,
            StandardEnvMerger,
            StandardSymbolLoader,
        )

        loader = StandardSymbolLoader()
        self.symbol_loader = loader
        self.discovery = IndexerBasedDiscovery(symbol_loader=loader)
        self.merger = StandardEnvMerger(symbol_loader=loader)


class ProgramBuilder:
    def __init__(self, resolver: SymbolResolver, merger: Any) -> None:
        self._resolver = resolver
        self._merger = merger

    def load(self, context: ResolvedRunContext) -> Program[Any]:
        if context.program_instance is not None:
            return context.program_instance
        if context.program_path is None:
            raise ValueError("Either program_path or program_instance must be provided")
        return self._resolver.program(context.program_path, "--program")

    def inject_envs(
        self, program: Program[Any], env_sources: list[str], *, report_verbose: bool
    ) -> Program[Any]:
        if not env_sources:
            return program

        from doeff.effects import Local

        merged_env_program = self._merger.merge_envs(env_sources)
        try:
            merged_env_dict = sync_run(merged_env_program, sync_handlers_preset).value
        except Exception as exc:
            print("[DOEFF][DISCOVERY] Environment merge failed:", file=sys.stderr)
            print(repr(exc), file=sys.stderr)
            raise
        local_effect = Local(merged_env_dict, program)
        return local_effect

    def apply_kleisli(self, program: Program[Any], context: ResolvedRunContext) -> Program[Any]:
        if not context.apply_path:
            return program
        kleisli = self._resolver.kleisli(context.apply_path, "--apply")
        return kleisli(program)

    def apply_transformer(self, program: Program[Any], transform_path: str) -> Program[Any]:
        transformer = self._resolver.transformer(transform_path, f"transformer {transform_path}")
        return transformer(program)


class RunCommand:
    def __init__(self, context: RunContext) -> None:
        self._initial_context = context
        self._resolver = SymbolResolver()
        self._services: RunServices | None = None
        self._builder: ProgramBuilder | None = None

    def execute(self) -> tuple[ResolvedRunContext, RunExecutionResult]:
        with profile("CLI discovery and execution"):
            print_profiling_status()
            _ = self.services  # Ensure services are initialized within the profiling block
            resolved_context = self._resolve_context(self._initial_context)
            program = self._prepare_program(resolved_context)
            run_result, final_value = self._run_program(resolved_context, program)

        call_tree_ascii = _call_tree_ascii(run_result) if run_result is not None else None
        return resolved_context, RunExecutionResult(final_value, run_result, call_tree_ascii)

    def _resolve_context(self, context: RunContext) -> ResolvedRunContext:
        interpreter_path = context.interpreter_path
        env_paths = list(context.env_paths)

        if interpreter_path is None:
            if context.program_path is not None:
                interpreter_path = self._auto_discover_interpreter(context.program_path)
            else:
                discovered = _discover_topmost_interpreter()
                if discovered is None:
                    raise RuntimeError(
                        "No default interpreter found. "
                        "Please specify --interpreter or add '# doeff: interpreter, default' marker."
                    )
                interpreter_path = discovered

        if not env_paths and context.program_path is not None:
            env_paths = self._auto_discover_envs(context.program_path)

        return ResolvedRunContext(
            program_path=context.program_path,
            program_instance=context.program_instance,
            interpreter_path=interpreter_path,
            env_paths=env_paths,
            apply_path=context.apply_path,
            transformer_paths=context.transformer_paths,
            output_format=context.output_format,
            report=context.report,
            report_verbose=context.report_verbose,
        )

    def _prepare_program(self, context: ResolvedRunContext) -> Program[Any]:
        with profile("Load program", indent=1):
            program = self.builder.load(context)
        env_sources = self._resolve_env_sources(context)

        if context.apply_path:
            with profile(f"Apply kleisli {context.apply_path}", indent=1):
                program = self.builder.apply_kleisli(program, context)
                if is_profiling_enabled():
                    print(
                        f"[DOEFF][DISCOVERY] Applied kleisli: {context.apply_path}",
                        file=sys.stderr,
                    )

        for transform_path in context.transformer_paths:
            with profile(f"Apply transform {transform_path}", indent=1):
                program = self.builder.apply_transformer(program, transform_path)
                if is_profiling_enabled():
                    print(
                        f"[DOEFF][DISCOVERY] Applied transform: {transform_path}",
                        file=sys.stderr,
                    )

        if env_sources:
            with profile("Merge environments", indent=1):
                program = self.builder.inject_envs(
                    program, env_sources, report_verbose=context.report_verbose
                )

        return program

    def _run_program(
        self, context: ResolvedRunContext, program: Program[Any]
    ) -> tuple[RunResult[Any] | None, Any]:
        with profile("Load and run interpreter", indent=1):
            interpreter_obj = self._resolver.resolve(context.interpreter_path)

            if not callable(interpreter_obj):
                raise TypeError("--interpreter must resolve to a callable")

            result = _call_interpreter(interpreter_obj, program)
            final_value, run_result = _finalize_result(result)
            return run_result, final_value

    def _auto_discover_interpreter(self, program_path: str) -> str:
        with profile("Auto-discover interpreter", indent=1):
            discovered = self.services.discovery.find_default_interpreter(program_path)
            if discovered is None:
                raise RuntimeError(
                    f"No default interpreter found for {program_path}. "
                    "Please specify --interpreter or add '# doeff: interpreter, default' marker to an interpreter function."
                )
            if is_profiling_enabled():
                print(f"[DOEFF][DISCOVERY] Interpreter: {discovered}", file=sys.stderr)
            return discovered

    def _auto_discover_envs(self, program_path: str) -> list[str]:
        with profile("Auto-discover environments", indent=1):
            discovered_envs = self.services.discovery.discover_default_envs(program_path)
            if is_profiling_enabled():
                if discovered_envs:
                    print(
                        f"[DOEFF][DISCOVERY] Environments ({len(discovered_envs)}):",
                        file=sys.stderr,
                    )
                    for env_path in discovered_envs:
                        print(f"[DOEFF][DISCOVERY]   - {env_path}", file=sys.stderr)
                else:
                    print("[DOEFF][DISCOVERY] Environments: none found", file=sys.stderr)
            return discovered_envs

    def _resolve_env_sources(self, context: ResolvedRunContext) -> list[str]:
        sources: list[str] = []
        default_env_path = self._load_default_env()
        if default_env_path:
            sources.append(default_env_path)
        sources.extend(context.env_paths)
        return sources

    def _load_default_env(self) -> str | None:
        from pathlib import Path

        doeff_config_file = Path.home() / ".doeff.py"
        if not doeff_config_file.exists():
            print("[DOEFF][DISCOVERY] Warning: ~/.doeff.py not found", file=sys.stderr)
            return None

        with profile("Load ~/.doeff.py", indent=1):
            spec = importlib.util.spec_from_file_location("_doeff_config", doeff_config_file)
            if not spec or not spec.loader:
                print(
                    "[DOEFF][DISCOVERY] Warning: Unable to load ~/.doeff.py",
                    file=sys.stderr,
                )
                return None

            config_module = importlib.util.module_from_spec(spec)
            sys.modules["_doeff_config"] = config_module

            try:
                spec.loader.exec_module(config_module)
            except Exception as exc:  # pragma: no cover - best effort diagnostic
                print(
                    f"[DOEFF][DISCOVERY] Error executing ~/.doeff.py: {exc}",
                    file=sys.stderr,
                )
                raise

            if hasattr(config_module, "__default_env__"):
                print(
                    "[DOEFF][DISCOVERY] Successfully resolved __default_env__ from ~/.doeff.py",
                    file=sys.stderr,
                )
                return "_doeff_config.__default_env__"

            print(
                "[DOEFF][DISCOVERY] Warning: ~/.doeff.py exists but __default_env__ not found",
                file=sys.stderr,
            )
            return None

    @property
    def services(self) -> RunServices:
        if self._services is None:
            with profile("Initialize discovery services", indent=1):
                self._services = RunServices()
        return self._services

    @property
    def builder(self) -> ProgramBuilder:
        if self._builder is None:
            self._builder = ProgramBuilder(self._resolver, self.services.merger)
        return self._builder


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
            payload["report"] = run_result.display(verbose=context.report_verbose)
            if execution.call_tree_ascii is not None:
                payload["call_tree"] = execution.call_tree_ascii
        print(json.dumps(payload))
        return

    print(final_value)
    if context.report:
        if run_result is not None:
            print()
            print(run_result.display(verbose=context.report_verbose))
        else:
            print(
                "\n(No run report available: interpreter did not return a RunResult)",
                file=sys.stderr,
            )


def _import_symbol(path: str) -> Any:
    if ":" in path:
        module_name, attr_path = path.split(":", 1)
        module = importlib.import_module(module_name)
        return _resolve_attr(module, attr_path)
    parts = path.split(".")
    if len(parts) < 2:
        raise ValueError(f"'{path}' is not a fully-qualified symbol. Use module.symbol format.")
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
    from doeff.types import EffectBase, Program

    if isinstance(obj, (Program, EffectBase)):
        return obj  # type: ignore[return-value]
    if callable(obj):
        produced = obj()
        if isinstance(produced, (Program, EffectBase)):
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
    from doeff.cesk.runtime_result import RuntimeResult
    from doeff.program import Program as ProgramType

    if isinstance(value, ProgramType):
        result = sync_run(value, sync_handlers_preset)
        return result.value, None
    if isinstance(value, RuntimeResult):
        return value.value, None
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
        "sync_run": sync_run,
        "sync_handlers_preset": sync_handlers_preset,
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


def _discover_topmost_interpreter() -> str | None:
    from doeff.cli.profiling import is_profiling_enabled, profile

    with profile("Auto-discover interpreter for -c", indent=1):
        try:
            from doeff_indexer import Indexer
        except ImportError:
            return None

        cwd_package = _detect_cwd_package()
        if cwd_package is None:
            return None

        try:
            indexer = Indexer.for_module(cwd_package)
        except RuntimeError:
            return None

        symbols = indexer.find_symbols(tags=["interpreter", "default"], symbol_type="function")
        if not symbols:
            return None

        topmost = min(symbols, key=lambda s: s.module_path.count("."))
        if is_profiling_enabled():
            print(f"[DOEFF][DISCOVERY] Interpreter: {topmost.full_path}", file=sys.stderr)
        return topmost.full_path


def _detect_cwd_package() -> str | None:
    from pathlib import Path

    cwd = Path.cwd()
    if (cwd / "__init__.py").exists():
        return cwd.name
    for child in cwd.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            return child.name
    return None


def handle_run_code(args: argparse.Namespace) -> int:
    from doeff.cli.code_runner import execute_doeff_code

    code = args.code
    if code == "-":
        code = sys.stdin.read()

    if not code or not code.strip():
        print("Error: No code provided", file=sys.stderr)
        return 1

    # Create runbox record before execution (if runbox CLI is available)
    skip_runbox = getattr(args, "no_runbox", False)
    maybe_create_runbox_record(skip_runbox=skip_runbox)
    program: Program[Any] = execute_doeff_code(code, filename="<doeff-code>")

    context = RunContext(
        program_path=None,
        program_instance=program,
        interpreter_path=args.interpreter,
        env_paths=args.envs or [],
        apply_path=getattr(args, "apply", None),
        transformer_paths=getattr(args, "transform", None) or [],
        output_format=args.format,
        report=getattr(args, "report", False),
        report_verbose=getattr(args, "report_verbose", False),
    )

    command = RunCommand(context)
    resolved_context, execution = command.execute()
    _render_run_output(resolved_context, execution)
    return 0


def handle_run(args: argparse.Namespace) -> int:
    code_arg = getattr(args, "code", None)
    if code_arg is not None:
        return handle_run_code(args)

    if not getattr(args, "program", None):
        print("Error: --program is required when not using -c", file=sys.stderr)
        return 1

    # Create runbox record before execution (if runbox CLI is available)
    skip_runbox = getattr(args, "no_runbox", False)
    maybe_create_runbox_record(skip_runbox=skip_runbox)
    context = RunContext(
        program_path=args.program,
        program_instance=None,
        interpreter_path=args.interpreter,
        env_paths=args.envs or [],
        apply_path=args.apply,
        transformer_paths=args.transform or [],
        output_format=args.format,
        report=getattr(args, "report", False),
        report_verbose=getattr(args, "report_verbose", False),
    )

    script = getattr(args, "script", None)
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
            "  - sync_run, sync_handlers_preset: Run functions\n"
            "  - sys, json: Standard library modules\n\n"
            "Example:\n"
            "  doeff run --program myapp.program - <<'PY'\n"
            "  print(f'Result: {value}')\n"
            "  result = sync_run(program, sync_handlers_preset)\n"
            "  print(f'Re-run: {result.value}')\n"
            "  PY"
        ),
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
