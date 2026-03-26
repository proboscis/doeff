"""Run services — symbol resolution, interpreter discovery, program execution."""

import importlib
import sys
from dataclasses import dataclass, field
from typing import Any

from doeff.cli.profiling import profile


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
    apply_path: str | None = None
    transformer_paths: list[str] = field(default_factory=list)
    output_format: str = "text"


@dataclass
class ResolvedRunContext:
    program_path: str | None
    program_instance: Any
    interpreter_path: str
    env_paths: list[str]
    apply_path: str | None
    transformer_paths: list[str]
    output_format: str


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
        apply_path=ctx.apply_path,
        transformer_paths=ctx.transformer_paths,
        output_format=ctx.output_format,
    )


def default_interpreter(program: Any) -> Any:
    """Default interpreter: run with standard handlers + scheduler."""
    from doeff import run, WithHandler
    from doeff_core_effects.handlers import (
        reader, state, writer, try_handler, slog_handler,
        local_handler, listen_handler, await_handler,
    )
    from doeff_core_effects.scheduler import scheduled

    handlers = [
        reader(), state(), writer(), try_handler, slog_handler(),
        local_handler, listen_handler, await_handler(),
    ]
    wrapped = program
    for h in reversed(handlers):
        wrapped = WithHandler(h, wrapped)
    return run(scheduled(wrapped))


def execute(resolved: ResolvedRunContext) -> Any:
    """Execute a resolved run context and return the final value."""
    program = resolved.program_instance

    # Apply --apply (T -> Program[U])
    if resolved.apply_path:
        with profile("Apply transform"):
            apply_fn = import_symbol(resolved.apply_path)
            program = apply_fn(program)

    # Apply --transform (Program -> Program)
    for tp in resolved.transformer_paths:
        with profile(f"Transform: {tp}"):
            transform_fn = import_symbol(tp)
            program = transform_fn(program)

    # Merge envs and wrap with Local if any
    if resolved.env_paths:
        with profile("Merge envs"):
            from doeff.cli.discovery import StandardEnvMerger
            merger = StandardEnvMerger()
            env_program = merger.merge_envs(resolved.env_paths)

            from doeff import do
            from doeff_core_effects.effects import Local

            @do
            def with_env():
                env_dict = yield env_program
                result = yield Local(env_dict, program)
                return result

            program = with_env()

    # Run through interpreter
    with profile("Run interpreter"):
        interpreter = import_symbol(resolved.interpreter_path)
        return interpreter(program)
