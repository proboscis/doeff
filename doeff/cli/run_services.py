"""Run services — symbol resolution, interpreter discovery, program execution."""

import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doeff.cli.profiling import profile


def _load_doeff_config_env() -> str | None:
    """Load __default_env__ from ~/.doeff.py if it exists.

    Returns an importable symbol path, or None.
    """
    doeff_config_file = Path.home() / ".doeff.py"
    if not doeff_config_file.exists():
        return None
    spec = importlib.util.spec_from_file_location("_doeff_config", doeff_config_file)
    if not (spec and spec.loader):
        return None
    config_module = importlib.util.module_from_spec(spec)
    sys.modules["_doeff_config"] = config_module
    spec.loader.exec_module(config_module)
    if hasattr(config_module, "__default_env__"):
        return "_doeff_config.__default_env__"
    return None


def import_symbol(full_path: str) -> Any:
    """Import a Python symbol by dotted path (e.g., 'myapp.module:symbol' or 'myapp.module.symbol')."""
    # Support colon separator: "module:attr"
    if ":" in full_path:
        module_path, attr_name = full_path.rsplit(":", 1)
        with profile(f"Import {module_path}", indent=1):
            module = importlib.import_module(module_path)
        obj = module
        for part in attr_name.split("."):
            obj = getattr(obj, part)
        return obj

    # Dotted path: try progressively longer module paths
    parts = full_path.split(".")
    for i in range(len(parts), 0, -1):
        module_path = ".".join(parts[:i])
        attr_path = parts[i:]
        try:
            with profile(f"Import {module_path}", indent=1):
                module = importlib.import_module(module_path)
            obj = module
            for attr in attr_path:
                obj = getattr(obj, attr)
            return obj
        except (ImportError, AttributeError):
            if i == 1:
                raise
    raise ImportError(f"Could not import {full_path}")


@dataclass
class RunContext:
    program_path: str | None
    program_instance: Any | None
    interpreter_path: str | None
    env_paths: list[str] = field(default_factory=list)
    set_vars: dict[str, Any] = field(default_factory=dict)
    apply_paths: list[str] = field(default_factory=list)
    transformer_paths: list[str] = field(default_factory=list)
    output_format: str = "text"


@dataclass
class ResolvedRunContext:
    program_path: str | None
    program_instance: Any
    interpreter_path: str
    env_paths: list[str]
    set_vars: dict[str, str]
    apply_paths: list[str]
    transformer_paths: list[str]
    output_format: str


@dataclass(frozen=True)
class DoeffRunContext:
    """CLI invocation context passed to interpreters for remote execution support.

    Interpreters that need to reconstruct the original ``doeff run`` command
    (e.g. for k3s Jobs, Docker, SSH) can accept this as ``ctx=``.
    """

    program_ref: str
    interpreter_ref: str
    env_refs: list[str]
    set_overrides: dict[str, str]
    apply_refs: list[str]
    transform_refs: list[str]


def resolve_context(ctx: RunContext) -> ResolvedRunContext:
    """Resolve a RunContext: load program, discover interpreter if needed."""
    # Load program
    if ctx.program_instance is not None:
        program = ctx.program_instance
    elif ctx.program_path:
        with profile("Load program"):
            program = import_symbol(ctx.program_path)
    else:
        raise ValueError("No program specified")

    # Resolve interpreter
    interpreter_path = ctx.interpreter_path
    env_paths = list(ctx.env_paths)

    if interpreter_path is None and ctx.program_path:
        # Auto-discover interpreter
        with profile("Discover interpreter"):
            try:
                from doeff.cli.discovery import IndexerBasedDiscovery
                discovery = IndexerBasedDiscovery()
                interpreter_path = discovery.find_default_interpreter(ctx.program_path)

                # Also discover default envs if none specified
                if not env_paths:
                    env_paths = discovery.discover_default_envs(ctx.program_path)
            except ImportError:
                pass

    if interpreter_path is None:
        interpreter_path = "doeff.cli.run_services:default_interpreter"

    return ResolvedRunContext(
        program_path=ctx.program_path,
        program_instance=program,
        interpreter_path=interpreter_path,
        env_paths=env_paths,
        set_vars=ctx.set_vars,
        apply_paths=ctx.apply_paths,
        transformer_paths=ctx.transformer_paths,
        output_format=ctx.output_format,
    )


def default_interpreter(program: Any) -> Any:
    """Default interpreter: run with standard handlers + scheduler."""
    from doeff import run, WithHandler
    from doeff_core_effects.handlers import (
        lazy_ask, state, writer, try_handler, slog_handler,
        listen_handler, await_handler,
    )
    from doeff_core_effects.scheduler import scheduled

    handlers = [
        lazy_ask(), state(), writer(), try_handler, slog_handler(),
        listen_handler, await_handler(),
    ]
    wrapped = program
    for h in reversed(handlers):
        wrapped = WithHandler(h, wrapped)
    return run(scheduled(wrapped))


def execute(resolved: ResolvedRunContext) -> Any:
    """Execute a resolved run context and return the final value."""
    program = resolved.program_instance

    # Apply --apply (T -> Program[U]), chained left-to-right
    for ap in resolved.apply_paths:
        with profile(f"Apply: {ap}"):
            apply_fn = import_symbol(ap)
            program = apply_fn(program)

    # Apply --transform (Program -> Program)
    for tp in resolved.transformer_paths:
        with profile(f"Transform: {tp}"):
            transform_fn = import_symbol(tp)
            program = transform_fn(program)

    # Build env Program[dict] from ~/.doeff.py + discovered/explicit envs
    env_sources = list(resolved.env_paths)
    with profile("Load ~/.doeff.py"):
        config_env_path = _load_doeff_config_env()
        if config_env_path:
            env_sources.insert(0, config_env_path)  # base, overridden by project envs

    env_program = None
    if env_sources:
        with profile("Merge envs"):
            from doeff.cli.discovery import StandardEnvMerger
            merger = StandardEnvMerger()
            env_program = merger.merge_envs(env_sources)

    # Apply --set KEY=VALUE overrides (highest priority)
    if resolved.set_vars:
        from doeff import Pure, do

        set_dict = dict(resolved.set_vars)
        if env_program is not None:
            base = env_program

            @do
            def with_overrides():
                merged = yield base
                merged.update(set_dict)
                return merged

            env_program = with_overrides()
        else:
            env_program = Pure(set_dict)

    # Build CLI context for interpreters that support remote execution
    run_ctx = None
    if resolved.program_path:
        run_ctx = DoeffRunContext(
            program_ref=resolved.program_path,
            interpreter_ref=resolved.interpreter_path,
            env_refs=list(resolved.env_paths),
            set_overrides=dict(resolved.set_vars),
            apply_refs=list(resolved.apply_paths),
            transform_refs=list(resolved.transformer_paths),
        )

    # Run through interpreter — pass env/ctx as keyword args if supported
    with profile("Run interpreter"):
        interpreter = import_symbol(resolved.interpreter_path)
        import inspect
        sig = inspect.signature(interpreter)
        kwargs: dict[str, Any] = {}
        if env_program is not None and "env" in sig.parameters:
            kwargs["env"] = env_program
        if run_ctx is not None and "ctx" in sig.parameters:
            kwargs["ctx"] = run_ctx
        return interpreter(program, **kwargs)
