"""Shared run/discovery services for CLI and library APIs."""

import asyncio
import importlib
import importlib.util
import inspect
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from doeff.analysis import EffectCallTree
from doeff.cli.profiling import is_profiling_enabled, print_profiling_status, profile
from doeff.kleisli import KleisliProgram
from doeff.program import Program
from doeff.rust_vm import RunResult as VmRunResult
from doeff.rust_vm import default_handlers
from doeff.rust_vm import run as vm_run
from doeff.types import RunResult, capture_traceback


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


@runtime_checkable
class _RunResultWithEffectObservations(Protocol):
    effect_observations: list[Any] | None


@runtime_checkable
class _EffectObservationContext(Protocol):
    effect_observations: list[Any] | None


@runtime_checkable
class _RunResultWithContext(Protocol):
    context: _EffectObservationContext | None


class _RunResultReportable(Protocol):
    def display(self, *, verbose: bool = False) -> str: ...


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


class DiscoveryReporter(Protocol):
    def debug(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...


class _NullDiscoveryReporter:
    def debug(self, message: str) -> None:
        del message

    def warning(self, message: str) -> None:
        del message


class _StderrDiscoveryReporter:
    def debug(self, message: str) -> None:
        print(message, file=sys.stderr)

    def warning(self, message: str) -> None:
        print(message, file=sys.stderr)


class RunServicesProxy:
    """Small adapter for callers that already hold a merger instance."""

    def __init__(self, merger: Any) -> None:
        self.merger = merger


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
        del report_verbose
        return apply_resolved_env_paths(
            program,
            env_sources,
            services=RunServicesProxy(self._merger),
            reporter=_StderrDiscoveryReporter(),
        )

    def resolve_envs(
        self, env_sources: list[str], *, report_verbose: bool
    ) -> dict[str, Any]:
        del report_verbose
        return resolve_env_paths_to_dict(
            env_sources,
            services=RunServicesProxy(self._merger),
            reporter=_StderrDiscoveryReporter(),
        )

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
            _ = self.services
            resolved_context = self._resolve_context(self._initial_context)
            program, env = self._prepare_program(resolved_context)
            run_result, final_value = self._run_program(resolved_context, program, env)

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

    def _prepare_program(
        self, context: ResolvedRunContext
    ) -> tuple[Program[Any], dict[str, Any] | None]:
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

        merged_env: dict[str, Any] | None = None
        if env_sources:
            with profile("Merge environments", indent=1):
                merged_env = self.builder.resolve_envs(
                    env_sources, report_verbose=context.report_verbose
                )

        return program, merged_env

    def _run_program(
        self,
        context: ResolvedRunContext,
        program: Program[Any],
        env: dict[str, Any] | None,
    ) -> tuple[RunResult[Any] | None, Any]:
        with profile("Load and run interpreter", indent=1):
            interpreter_obj = self._resolver.resolve(context.interpreter_path)
            if not callable(interpreter_obj):
                raise TypeError("--interpreter must resolve to a callable")

            result = _call_interpreter(interpreter_obj, program, env=env)
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
        default_env_path = load_default_env_path(_StderrDiscoveryReporter())
        if default_env_path:
            sources.append(default_env_path)
        sources.extend(context.env_paths)
        return sources

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


def apply_runtime_envs(
    program: Program[Any],
    envs: list[str | Program[dict[str, Any]] | Mapping[str, Any]],
    *,
    load_default_env: bool,
    services: RunServices | RunServicesProxy | None = None,
    reporter: DiscoveryReporter | None = None,
) -> tuple[Program[Any], list[str]]:
    active_services = services or RunServices()
    active_reporter = reporter or _NullDiscoveryReporter()
    env_sources: list[str] = []
    merged_env: dict[str, Any] = {}

    if load_default_env:
        default_env_path = load_default_env_path(active_reporter)
        if default_env_path:
            merged_env.update(_resolve_env_path_dict(default_env_path, active_services, active_reporter))
            env_sources.append("~/.doeff.py:__default_env__")

    for env in envs:
        if isinstance(env, str):
            merged_env.update(_resolve_env_path_dict(env, active_services, active_reporter))
            env_sources.append(env)
        elif isinstance(env, Program):
            env_value = vm_run(env, handlers=default_handlers()).value
            if not isinstance(env_value, dict):
                raise TypeError(f"Environment Program must yield dict, got {type(env_value)}")
            merged_env.update(env_value)
            env_sources.append("<Program[dict]>")
        elif isinstance(env, Mapping):
            merged_env.update(env)
            env_sources.append("<dict>")
        else:
            raise TypeError(f"env must be str, Program[dict], or dict, got {type(env)}")

    if merged_env:
        from doeff.effects import Local

        program = Local(merged_env, program)  # type: ignore[assignment]

    return program, env_sources


def resolve_env_paths_to_dict(
    env_sources: list[str],
    *,
    services: RunServices | RunServicesProxy | None = None,
    reporter: DiscoveryReporter | None = None,
) -> dict[str, Any]:
    if not env_sources:
        return {}

    active_services = services or RunServices()
    active_reporter = reporter or _NullDiscoveryReporter()
    merged_env: dict[str, Any] = {}
    for env_source in env_sources:
        merged_env.update(_resolve_env_path_dict(env_source, active_services, active_reporter))
    return merged_env


def apply_resolved_env_paths(
    program: Program[Any],
    env_sources: list[str],
    *,
    services: RunServices | RunServicesProxy | None = None,
    reporter: DiscoveryReporter | None = None,
) -> Program[Any]:
    if not env_sources:
        return program

    active_services = services or RunServices()
    active_reporter = reporter or _NullDiscoveryReporter()
    merged_env = {}
    for env_source in env_sources:
        merged_env.update(_resolve_env_path_dict(env_source, active_services, active_reporter))

    from doeff.effects import Local

    return Local(merged_env, program)


def load_default_env_path(reporter: DiscoveryReporter | None = None) -> str | None:
    active_reporter = reporter or _NullDiscoveryReporter()
    if os.environ.get("DOEFF_DISABLE_DEFAULT_ENV") == "1":
        return None

    doeff_config_file = Path.home() / ".doeff.py"
    if not doeff_config_file.exists():
        active_reporter.debug("[DOEFF][DISCOVERY] Warning: ~/.doeff.py not found")
        return None

    with profile("Load ~/.doeff.py", indent=1):
        spec = importlib.util.spec_from_file_location("_doeff_config", doeff_config_file)
        if not spec or not spec.loader:
            active_reporter.debug("[DOEFF][DISCOVERY] Warning: Unable to load ~/.doeff.py")
            return None

        config_module = importlib.util.module_from_spec(spec)
        sys.modules["_doeff_config"] = config_module

        try:
            spec.loader.exec_module(config_module)
        except Exception as exc:  # pragma: no cover - best effort diagnostic
            active_reporter.warning(f"[DOEFF][DISCOVERY] Error executing ~/.doeff.py: {exc}")
            raise

        if hasattr(config_module, "__default_env__"):
            active_reporter.debug(
                "[DOEFF][DISCOVERY] Successfully resolved __default_env__ from ~/.doeff.py"
            )
            return "_doeff_config.__default_env__"

        active_reporter.debug(
            "[DOEFF][DISCOVERY] Warning: ~/.doeff.py exists but __default_env__ not found"
        )
        return None


def _resolve_env_path_dict(
    env_source: str,
    services: RunServices | RunServicesProxy,
    reporter: DiscoveryReporter,
) -> dict[str, Any]:
    env_program = services.merger.merge_envs([env_source])
    try:
        env_value = vm_run(env_program, handlers=default_handlers(), print_doeff_trace=False).value
    except Exception as exc:
        reporter.warning("[DOEFF][DISCOVERY] Environment merge failed:")
        reporter.warning(repr(exc))
        raise
    if not isinstance(env_value, dict):
        env_value = dict(env_value)
    return env_value


def _run_result_report(run_result: RunResult[Any], *, verbose: bool) -> str:
    reportable = cast(_RunResultReportable, run_result)
    return reportable.display(verbose=verbose)


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
    from doeff.types import EffectBase

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


def _call_interpreter(
    func: Callable[..., Any],
    program: Program[Any],
    *,
    env: dict[str, Any] | None = None,
) -> Any:
    signature = inspect.signature(func)

    # Check if interpreter accepts an 'env' keyword argument.
    accepts_env = "env" in signature.parameters and signature.parameters[
        "env"
    ].kind in {
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }

    if accepts_env and env is not None:
        # Pass env as a separate kwarg; interpreter controls where Local is applied.
        effective_program = program
    elif env is not None:
        # Interpreter does not accept env — fall back to Local wrapping for
        # backward compatibility.
        from doeff.effects import Local

        effective_program = Local(env, program)
    else:
        effective_program = program

    try:
        bound = signature.bind_partial(effective_program)
    except TypeError as exc:
        raise TypeError(
            "Interpreter must accept a Program as its first positional argument"
        ) from exc

    if accepts_env and env is not None:
        bound.arguments["env"] = env

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
    if isinstance(value, Program):
        result = vm_run(value, handlers=default_handlers())
        return _unwrap_run_result(result), None
    if isinstance(value, VmRunResult):
        return _unwrap_run_result(value), value
    return value, None


class _RunResultExecutionError(RuntimeError):
    """Internal wrapper used when unwrapping a failing RunResult."""


def _reported_exception(exc: BaseException) -> BaseException:
    cause = exc.__cause__
    if isinstance(exc, _RunResultExecutionError) and isinstance(cause, BaseException):
        return cause
    return exc


def _unwrap_run_result(result: RunResult[Any]) -> Any:
    try:
        return result.value
    except Exception as exc:
        try:
            from doeff.traceback import (
                attach_doeff_traceback,
                get_attached_doeff_traceback,
                set_attached_doeff_traceback,
            )

            if get_attached_doeff_traceback(exc) is None:
                doeff_tb = attach_doeff_traceback(exc, traceback_data=result.traceback_data)
                if doeff_tb is not None:
                    set_attached_doeff_traceback(exc, doeff_tb)
        except Exception:
            pass
        raise _RunResultExecutionError("Program execution failed") from exc


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def _call_tree_ascii(run_result: RunResult[Any]) -> str | None:
    observations: list[Any] | None = None
    if isinstance(run_result, _RunResultWithEffectObservations):
        observations = run_result.effect_observations
    if observations is None and isinstance(run_result, _RunResultWithContext):
        context = run_result.context
        if context is not None:
            observations = context.effect_observations
    if not observations:
        return None

    tree = EffectCallTree.from_observations(observations)
    ascii_tree = tree.visualize_ascii()
    if ascii_tree == "(no effects)":
        return None
    return ascii_tree


def _discover_topmost_interpreter() -> str | None:
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
    cwd = Path.cwd()
    if (cwd / "__init__.py").exists():
        return cwd.name
    for child in cwd.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            return child.name
    return None


__all__ = [
    "DiscoveryReporter",
    "ProgramBuilder",
    "ResolvedRunContext",
    "RunCommand",
    "RunContext",
    "RunExecutionResult",
    "RunServices",
    "SymbolResolver",
    "apply_resolved_env_paths",
    "apply_runtime_envs",
    "_call_interpreter",
    "_call_tree_ascii",
    "_discover_topmost_interpreter",
    "_ensure_kleisli",
    "_ensure_program",
    "_ensure_transformer",
    "_finalize_result",
    "_import_symbol",
    "_json_safe",
    "load_default_env_path",
    "_resolve_attr",
    "_run_result_report",
]
