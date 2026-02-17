"""
Python API for running programs with CLI-equivalent discovery and environment loading.

This module provides a programmatic interface to run doeff programs with the same
auto-discovery and environment loading logic as the `doeff run` CLI command.

Example:
    >>> from doeff.run import run_program
    >>> result = run_program("myapp.features.auth.login_program")
    >>> print(result.value)

    # Or with explicit configuration
    >>> result = run_program(
    ...     "myapp.program",
    ...     interpreter="myapp.interpreter",
    ...     envs=["myapp.env"],
    ... )
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from doeff.kleisli import KleisliProgram
from doeff.program import Program
from doeff.types import RunResult

T = TypeVar("T")

# Type aliases for flexible input types
ProgramLike = str | Program[Any]
InterpreterLike = str | Callable[..., Any] | None
EnvLike = str | Program[dict[str, Any]] | Mapping[str, Any]
KleisliLike = str | KleisliProgram[..., Any] | Callable[[Program[Any]], Program[Any]] | None
TransformLike = str | Callable[[Program[Any]], Program[Any]]


@dataclass
class ProgramRunResult:
    value: Any
    run_result: RunResult[Any] | None
    interpreter_path: str
    env_sources: list[str]
    applied_kleisli: str | None
    applied_transforms: list[str]


def run_program(
    program: ProgramLike,
    *,
    interpreter: InterpreterLike = None,
    envs: list[EnvLike] | None = None,
    apply: KleisliLike = None,
    transform: list[TransformLike] | None = None,
    report: bool = False,
    report_verbose: bool = False,
    quiet: bool = False,
    load_default_env: bool = True,
) -> ProgramRunResult:
    """Run a program with CLI-equivalent discovery and environment loading.

    This function provides the same functionality as `doeff run` CLI command,
    making it easy to run programs in pytest or other Python contexts with
    full auto-discovery support.

    Args:
        program: Either a fully-qualified path to a Program (e.g., "myapp.program")
                 or a Program instance directly.
        interpreter: Optional interpreter - can be a string path or callable.
                     If not specified, uses sync_run with default handlers.
        envs: Optional list of environments. Each item can be:
              - A string path (e.g., "myapp.env")
              - A Program[dict] that yields environment values
              - A dict directly
              If not specified, auto-discovery will be used.
        apply: Optional Kleisli to apply before execution. Can be:
               - A string path (e.g., "myapp.my_kleisli")
               - A KleisliProgram instance
               - A callable (Program[T] -> Program[U])
        transform: Optional list of transformers. Each can be:
                   - A string path
                   - A callable (Program[T] -> Program[U])
        report: If True, include run report in result.
        report_verbose: If True, use verbose mode for run report.
        quiet: If True, suppress discovery output messages.
        load_default_env: If True (default), load user's default environment
                          from ~/.doeff.py (same as `doeff run` behavior).

    Returns:
        ProgramRunResult containing the execution result and metadata.

    Raises:
        RuntimeError: If no default interpreter is found and none specified.
        TypeError: If program or interpreter resolution fails.

    Example:
        >>> # With auto-discovery (same as CLI)
        >>> result = run_program("myapp.features.auth.login_program")
        >>> print(result.value)

        >>> # With explicit interpreter and envs
        >>> result = run_program(
        ...     "myapp.program",
        ...     interpreter="myapp.interpreter",
        ...     envs=["myapp.env"],
        ... )

        >>> # With a Program instance and Program[dict] env
        >>> from doeff import Program
        >>> my_program = Program.pure(42)
        >>> my_env = Program.pure({"key": "value"})
        >>> result = run_program(my_program, envs=[my_env])

        >>> # With KleisliProgram
        >>> from doeff import KleisliProgram, do
        >>> @do
        ... def double(x: int) -> int:
        ...     return x * 2
        >>> result = run_program(my_program, apply=double)

        >>> # In pytest
        >>> def test_login_flow():
        ...     result = run_program("myapp.features.auth.login_program")
        ...     assert result.value == "Login successful"
    """
    # Normalize program - if string, check if we need full CLI path handling
    if isinstance(program, str):
        return _run_program_from_path(
            program,
            interpreter=interpreter,
            envs=envs,
            apply=apply,
            transform=transform,
            report=report,
            report_verbose=report_verbose,
            quiet=quiet,
            load_default_env=load_default_env,
        )

    # Program instance - handle directly
    return _run_program_instance(
        program,
        interpreter=interpreter,
        envs=envs,
        apply=apply,
        transform=transform,
        report=report,
        report_verbose=report_verbose,
        quiet=quiet,
        load_default_env=load_default_env,
    )


def _run_program_from_path(
    program_path: str,
    *,
    interpreter: InterpreterLike,
    envs: list[EnvLike] | None,
    apply: KleisliLike,
    transform: list[TransformLike] | None,
    report: bool,
    report_verbose: bool,
    quiet: bool,
    load_default_env: bool,
) -> ProgramRunResult:
    """Run a program from a string path with full CLI discovery support."""
    from doeff.__main__ import RunCommand, RunContext, SymbolResolver

    # Check if we have any non-string inputs that need special handling
    has_object_inputs = (
        (interpreter is not None and not isinstance(interpreter, str))
        or (envs is not None and any(not isinstance(e, str) for e in envs))
        or (apply is not None and not isinstance(apply, str))
        or (transform is not None and any(not isinstance(t, str) for t in transform))
    )

    if has_object_inputs:
        # Resolve the program and delegate to instance handler
        resolver = SymbolResolver()
        from doeff.__main__ import _ensure_program

        program_obj = _ensure_program(resolver.resolve(program_path), program_path)
        return _run_program_instance(
            program_obj,
            interpreter=interpreter,
            envs=envs,
            apply=apply,
            transform=transform,
            report=report,
            report_verbose=report_verbose,
            quiet=quiet,
            load_default_env=load_default_env,
        )

    # All inputs are strings - use CLI path for full discovery support
    env_paths = [e for e in (envs or []) if isinstance(e, str)]

    context = RunContext(
        program_path=program_path,
        program_instance=None,
        interpreter_path=interpreter if isinstance(interpreter, str) else None,
        env_paths=env_paths,
        apply_path=apply if isinstance(apply, str) else None,
        transformer_paths=[t for t in (transform or []) if isinstance(t, str)],
        output_format="text",
        report=report,
        report_verbose=report_verbose,
    )

    command = _QuietRunCommand(context) if quiet else RunCommand(context)
    resolved_context, execution = command.execute()

    return ProgramRunResult(
        value=execution.final_value,
        run_result=execution.run_result,
        interpreter_path=resolved_context.interpreter_path,
        env_sources=resolved_context.env_paths,
        applied_kleisli=resolved_context.apply_path,
        applied_transforms=resolved_context.transformer_paths,
    )


def _run_program_instance(
    program: Program[Any],
    *,
    interpreter: InterpreterLike,
    envs: list[EnvLike] | None,
    apply: KleisliLike,
    transform: list[TransformLike] | None,
    report: bool,
    report_verbose: bool,
    quiet: bool,
    load_default_env: bool,
) -> ProgramRunResult:
    from doeff.__main__ import SymbolResolver

    resolver = SymbolResolver()
    env_sources: list[str] = []

    interpreter_obj, interpreter_path = _resolve_interpreter(interpreter, resolver)

    applied_kleisli: str | None = None
    if apply is not None:
        program, applied_kleisli = _apply_kleisli(program, apply, resolver)

    applied_transforms: list[str] = []
    if transform:
        program, applied_transforms = _apply_transforms(program, transform, resolver)

    program, env_sources = _apply_envs(program, envs or [], resolver, load_default_env, quiet)

    final_value = _execute_program(program, interpreter_obj)

    return ProgramRunResult(
        value=final_value,
        run_result=None,
        interpreter_path=interpreter_path,
        env_sources=env_sources,
        applied_kleisli=applied_kleisli,
        applied_transforms=applied_transforms,
    )


def _resolve_interpreter(
    interpreter: InterpreterLike,
    resolver: Any,
) -> tuple[Any, str]:
    if interpreter is None:
        return None, "<default sync_run>"

    if isinstance(interpreter, str):
        return resolver.resolve(interpreter), interpreter

    if callable(interpreter):
        func_name = getattr(interpreter, "__name__", str(interpreter))
        return interpreter, f"<callable: {func_name}>"

    raise TypeError(f"interpreter must be str or callable, got {type(interpreter)}")


def _apply_kleisli(
    program: Program[Any],
    apply: KleisliLike,
    resolver: Any,
) -> tuple[Program[Any], str]:
    """Apply a Kleisli transformation to the program."""
    from doeff.__main__ import _ensure_kleisli

    if apply is None:
        return program, ""

    if isinstance(apply, str):
        kleisli = _ensure_kleisli(resolver.resolve(apply), apply)
        return kleisli(program), apply

    if isinstance(apply, KleisliProgram):
        kleisli_name = getattr(apply.func, "__name__", str(apply))
        return apply(program), f"<KleisliProgram: {kleisli_name}>"

    if callable(apply):
        func_name = getattr(apply, "__name__", str(apply))
        result = apply(program)
        if not isinstance(result, Program):
            raise TypeError(
                f"Kleisli function {func_name} must return a Program, got {type(result)}"
            )
        return result, f"<callable: {func_name}>"

    raise TypeError(f"apply must be str, KleisliProgram, or callable, got {type(apply)}")


def _apply_transforms(
    program: Program[Any],
    transforms: list[TransformLike],
    resolver: Any,
) -> tuple[Program[Any], list[str]]:
    """Apply a list of transformations to the program."""
    from doeff.__main__ import _ensure_transformer

    applied: list[str] = []

    for t in transforms:
        if isinstance(t, str):
            transformer = _ensure_transformer(resolver.resolve(t), t)
            program = transformer(program)
            applied.append(t)
        elif callable(t):
            func_name = getattr(t, "__name__", str(t))
            result = t(program)
            if not isinstance(result, Program):
                raise TypeError(
                    f"Transformer {func_name} must return a Program, got {type(result)}"
                )
            program = result
            applied.append(f"<callable: {func_name}>")
        else:
            raise TypeError(f"transform must be str or callable, got {type(t)}")

    return program, applied


def _apply_envs(
    program: Program[Any],
    envs: list[EnvLike],
    resolver: Any,
    load_default_env: bool,
    quiet: bool,
) -> tuple[Program[Any], list[str]]:
    from doeff.__main__ import RunServices
    from doeff.effects import Local
    from doeff.rust_vm import default_handlers
    from doeff.rust_vm import run as vm_run

    env_sources: list[str] = []
    merged_env: dict[str, Any] = {}

    if load_default_env:
        default_env_path = _load_default_env(quiet)
        if default_env_path:
            services = RunServices()
            env_program = services.merger.merge_envs([default_env_path])
            env_value = vm_run(env_program, handlers=default_handlers()).value
            merged_env.update(env_value)
            env_sources.append("~/.doeff.py:__default_env__")

    for env in envs:
        if isinstance(env, str):
            services = RunServices()
            env_program = services.merger.merge_envs([env])
            env_value = vm_run(env_program, handlers=default_handlers()).value
            merged_env.update(env_value)
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
        program = Local(merged_env, program)  # type: ignore[assignment]

    return program, env_sources


def _load_default_env(quiet: bool) -> str | None:
    """Load default environment from ~/.doeff.py if it exists.

    This replicates the behavior of `doeff run` CLI command.
    """
    import importlib.util
    import os
    import sys
    from pathlib import Path

    if os.environ.get("DOEFF_DISABLE_DEFAULT_ENV") == "1":
        return None

    doeff_config_file = Path.home() / ".doeff.py"
    if not doeff_config_file.exists():
        if not quiet:
            print("[DOEFF][DISCOVERY] Warning: ~/.doeff.py not found", file=sys.stderr)
        return None

    spec = importlib.util.spec_from_file_location("_doeff_config", doeff_config_file)
    if not spec or not spec.loader:
        if not quiet:
            print("[DOEFF][DISCOVERY] Warning: Unable to load ~/.doeff.py", file=sys.stderr)
        return None

    config_module = importlib.util.module_from_spec(spec)
    sys.modules["_doeff_config"] = config_module

    try:
        spec.loader.exec_module(config_module)
    except Exception as exc:
        if not quiet:
            print(f"[DOEFF][DISCOVERY] Error executing ~/.doeff.py: {exc}", file=sys.stderr)
        raise

    if hasattr(config_module, "__default_env__"):
        if not quiet:
            print(
                "[DOEFF][DISCOVERY] Successfully resolved __default_env__ from ~/.doeff.py",
                file=sys.stderr,
            )
        return "_doeff_config.__default_env__"

    if not quiet:
        print(
            "[DOEFF][DISCOVERY] Warning: ~/.doeff.py exists but __default_env__ not found",
            file=sys.stderr,
        )
    return None


def _execute_program(
    program: Program[Any],
    interpreter_obj: Any,
) -> Any:
    from doeff.__main__ import _call_interpreter, _finalize_result
    from doeff.rust_vm import default_handlers
    from doeff.rust_vm import run as vm_run

    if interpreter_obj is None:
        return vm_run(program, handlers=default_handlers()).value

    if callable(interpreter_obj):
        result = _call_interpreter(interpreter_obj, program)
        final_value, run_result = _finalize_result(result)
        return final_value

    raise TypeError(f"interpreter must be callable, got {type(interpreter_obj)}")


class _QuietRunCommand:
    """A RunCommand variant that suppresses discovery output."""

    def __init__(self, context: Any) -> None:
        from doeff.__main__ import RunCommand

        self._inner = RunCommand(context)

    def execute(self) -> Any:
        import io
        from contextlib import redirect_stderr

        stderr_capture = io.StringIO()
        with redirect_stderr(stderr_capture):
            return self._inner.execute()


__all__ = ["ProgramRunResult", "run_program"]
