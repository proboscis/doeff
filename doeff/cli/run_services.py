"""Run services — symbol resolution, interpreter discovery, program execution."""

import difflib
import importlib
import importlib.util
import logging
import pkgutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from doeff_core_effects._extra_hints import MissingExtraError
from doeff_vm._not_a_program import uncalled_do_function_call

from doeff.cli.profiling import profile
from doeff.program import with_handlers

if TYPE_CHECKING:
    from doeff.cli.discovery import Discovered

_CLOSE_NAMES_SHOWN = 3
_logger: logging.Logger = logging.getLogger(__name__)


class _HyModuleWithoutHyFinder:
    """Name the extra hy when a program, interpreter or env path is a .hy module and Hy is missing.

    Last on sys.meta_path, it is asked only for modules that no other finder found.
    """

    @classmethod
    def find_spec(
        cls,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,  # noqa: ARG003 - MetaPathFinder protocol
    ) -> None:
        leaf = fullname.rpartition(".")[2]
        for entry in sys.path if path is None else path:
            if Path(entry, leaf + ".hy").is_file() or Path(entry, leaf, "__init__.hy").is_file():
                raise MissingExtraError("hy", "hy", f"{fullname} is a .hy module")


# Activate Hy's import hook so doeff run can resolve program/interpreter/env
# paths that live in .hy files (e.g. nakagawa.live.agent_pipeline.p_agent_daily).
# Without this, importlib.import_module for a .hy module silently creates an
# empty entry in sys.modules, and subsequent getattr(mod, name) fails with
# "module '<parent>' has no attribute '<child>'". Without Hy (doeff installed
# without the extra hy), a .hy module fails with the line that installs the extra.
try:
    import hy.importer  # noqa: F401 — registers sys.meta_path hook
except ImportError:
    sys.meta_path.append(_HyModuleWithoutHyFinder)


def _require_callable(symbol: object, *, description: str) -> Callable[..., object]:
    if not callable(symbol):
        raise TypeError(f"{description} is not callable")
    return symbol


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


def _close_names(owner: object, name: str) -> list[str]:
    """The public names ``owner`` has that are close to ``name``: its attributes and a package's submodules."""
    attributes = dir(owner)
    submodules = (
        [module.name for module in pkgutil.iter_modules(owner.__path__)]
        if isinstance(owner, ModuleType) and "__path__" in vars(owner)
        else []
    )
    if name in attributes or name in submodules:
        return []
    public = dict.fromkeys(n for n in (*attributes, *submodules) if not n.startswith("_"))
    return difflib.get_close_matches(name, list(public), n=_CLOSE_NAMES_SHOWN)


def _attribute_of(owner: object, name: str) -> object:
    """``getattr`` whose AttributeError for a misspelt ``name`` adds the close names ``owner`` has.

    A name ``owner`` does have (an AttributeError raised inside a property) and a name with no
    close match keep their own message. The error is raised ``from None`` because the CLI
    reports a chained error's cause (``_reported_exception``), which would drop the added line.
    """
    try:
        return getattr(owner, name)
    except AttributeError as error:
        close = _close_names(owner, name)
        if not close:
            raise
        raise AttributeError(
            f"{error}\n  Did you mean {' or '.join(map(repr, close))}?", name=name, obj=owner
        ) from None


def _names_missing_module(error: ModuleNotFoundError, module_path: str) -> bool:
    """Whether ``error`` says ``module_path``, or a package above it, does not exist.

    A ModuleNotFoundError that names any other module was raised by the code of a module that
    does exist (a dependency it imports is missing), and that is the answer to report.
    """
    missing = error.name
    return missing is not None and (module_path == missing or module_path.startswith(f"{missing}."))


def import_symbol(full_path: str) -> object:
    """Import a symbol whose concrete type must be narrowed by the caller."""
    # Support colon separator: "module:attr"
    if ":" in full_path:
        module_path, attr_name = full_path.rsplit(":", 1)
        with profile(f"Import {module_path}", indent=1):
            module = importlib.import_module(module_path)
        obj = module
        for part in attr_name.split("."):
            obj = _attribute_of(obj, part)
        return obj

    # Dotted path: try progressively longer module paths. Only "no such module" moves on to a
    # shorter path; whatever else importing a module raises (a missing dependency of the module,
    # an error in its code) is the real answer and must not turn into "no attribute" of its package.
    parts = full_path.split(".")
    for i in range(len(parts), 0, -1):
        module_path = ".".join(parts[:i])
        try:
            with profile(f"Import {module_path}", indent=1):
                module = importlib.import_module(module_path)
        except MissingExtraError:
            # The module exists but needs an extra; a shorter path would only hide the extra's name.
            raise
        except ModuleNotFoundError as error:
            if i == 1 or not _names_missing_module(error, module_path):
                raise
            continue
        try:
            obj = module
            for attr in parts[i:]:
                obj = _attribute_of(obj, attr)
            return obj
        except AttributeError:
            if i == 1:
                raise
    raise ImportError(f"Could not import {full_path}")


def import_callable(full_path: str) -> Callable[..., object]:
    """Import a symbol and fail immediately unless it is callable."""
    return _require_callable(
        import_symbol(full_path),
        description=f"imported symbol {full_path}",
    )


def reject_uncalled_do_function(program: object, *, program_ref: str) -> None:
    """Refuse a ``--program`` that names a @do function rather than the program it builds.

    ``--program`` names a program (``my_program = main()`` — docs/14-cli-auto-discovery.md).
    Handed the function, the default interpreter failed with ``WithHandler body: `` repeated
    once per handler; this says how to make the program instead. The CLI does not call the
    function itself, so ``--program`` keeps one meaning on every runner.
    """
    cut = max(program_ref.rfind("."), program_ref.rfind(":"))
    prefix, name = program_ref[: cut + 1], program_ref[cut + 1 :]
    call = uncalled_do_function_call(program, name=name)
    if call is None:
        return
    raise TypeError(
        f"--program {program_ref} is the @do function {name!r} itself, not a program. "
        f"Call the function to build the program and name that: add `{name}_program = {call}` "
        f"next to `{name}`, then run `doeff run --program {prefix}{name}_program`"
    )


@dataclass
class RunContext:
    program_path: str | None
    program_instance: Any | None
    interpreter_path: str | None
    env_paths: list[str] = field(default_factory=list)
    set_vars: dict[str, tuple[str, Any]] = field(default_factory=dict)
    apply_paths: list[str] = field(default_factory=list)
    transformer_paths: list[str] = field(default_factory=list)
    output_format: str = "text"


@dataclass
class ResolvedRunContext:
    program_path: str | None
    program_instance: Any
    interpreter_path: str
    env_paths: list[str]
    set_vars: dict[str, tuple[str, Any]]
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


@dataclass(frozen=True)
class RunnerContext:
    """Invocation context passed to ``--runner`` callables.

    Unlike :class:`DoeffRunContext` this captures every source form — Python
    symbol path, inline ``-c`` Python, or inline ``--hy`` Hy — plus enough
    metadata for remote backends (k3s, docker) to reconstruct the original
    ``doeff run`` command verbatim via ``raw_argv``.
    """

    program_ref: str | None
    py_source: str | None
    hy_source: str | None
    runner_ref: str
    format: str
    raw_argv: list[str]


def resolve_context(ctx: RunContext) -> ResolvedRunContext:
    """Resolve a RunContext: load program, discover interpreter if needed."""
    # Load program
    if ctx.program_instance is not None:
        program = ctx.program_instance
    elif ctx.program_path:
        with profile("Load program"):
            program = import_symbol(ctx.program_path)
        reject_uncalled_do_function(program, program_ref=ctx.program_path)
    else:
        raise ValueError("No program specified")

    # Resolve interpreter
    interpreter_path = ctx.interpreter_path
    env_paths = list(ctx.env_paths)

    if interpreter_path is None and ctx.program_path:
        # Auto-discover the interpreter (and the envs, when no --env names them): the discovery
        # program reads doeff-indexer's index through its effect, answered by the Rust indexer.
        with profile("Discover interpreter"):
            discovered = _discover(ctx.program_path, envs=not env_paths)
        if discovered is not None:
            interpreter_path = discovered.interpreter
            if not env_paths:
                env_paths = list(discovered.envs)

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


def _discover(program_path: str, *, envs: bool) -> "Discovered | None":
    """Run the discovery program under doeff-indexer's handler.

    None when doeff-indexer is not installed (discovery is optional), or when it is installed but its
    handler cannot be read (a broken Rust extension — logged as a warning). An index that cannot be
    built is the program's to decide (``discover`` logs it and finds nothing).
    """
    try:
        from doeff_indexer import rust_indexer
    except ModuleNotFoundError as missing:
        if missing.name != "doeff_indexer":
            _logger.warning("doeff-indexer cannot be read, so nothing is discovered: %s", missing)
        return None
    except ImportError as broken:
        _logger.warning("doeff-indexer cannot be read, so nothing is discovered: %s", broken)
        return None
    from doeff.cli.discovery import discover
    from doeff.run import run

    return run(rust_indexer(discover(program_path, envs=envs)))


def default_interpreter(program: object) -> object:
    """Fallback interpreter: run with reference handlers + the reference scheduler."""
    from doeff_core_effects._handlers import (
        await_handler,
        lazy_ask,
        listen_handler,
        slog_handler,
        state,
        try_handler,
        writer,
    )
    from doeff_core_effects._scheduler import scheduled

    from doeff import run

    handlers = [
        lazy_ask(), state(), writer, try_handler, slog_handler,
        listen_handler, await_handler,
    ]
    return run(scheduled()(with_handlers(handlers, program)))


def execute(resolved: ResolvedRunContext) -> object:
    """Execute a resolved run context and return the final value."""
    program = resolved.program_instance

    # Apply --apply (T -> Program[U]), chained left-to-right
    for ap in resolved.apply_paths:
        with profile(f"Apply: {ap}"):
            apply_fn = _require_callable(import_symbol(ap), description=f"--apply target {ap}")
            program = apply_fn(program)

    # Apply --transform (Program -> Program)
    for tp in resolved.transformer_paths:
        with profile(f"Transform: {tp}"):
            transform_fn = _require_callable(
                import_symbol(tp),
                description=f"--transform target {tp}",
            )
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

        resolved_dict = {k: v for k, (_raw, v) in resolved.set_vars.items()}
        if env_program is not None:
            base = env_program

            @do
            def with_overrides():
                merged = yield base
                merged.update(resolved_dict)
                return merged

            env_program = with_overrides()
        else:
            env_program = Pure(resolved_dict)

    # Build CLI context for interpreters that support remote execution
    run_ctx = None
    if resolved.program_path:
        raw_overrides = {k: raw for k, (raw, _v) in resolved.set_vars.items()}
        run_ctx = DoeffRunContext(
            program_ref=resolved.program_path,
            interpreter_ref=resolved.interpreter_path,
            env_refs=list(resolved.env_paths),
            set_overrides=raw_overrides,
            apply_refs=list(resolved.apply_paths),
            transform_refs=list(resolved.transformer_paths),
        )

    # Run through interpreter — pass env/ctx as keyword args if supported
    with profile("Run interpreter"):
        interpreter = _require_callable(
            import_symbol(resolved.interpreter_path),
            description=f"interpreter {resolved.interpreter_path}",
        )
        import inspect
        sig = inspect.signature(interpreter)
        kwargs: dict[str, Any] = {}
        if env_program is not None and "env" in sig.parameters:
            kwargs["env"] = env_program
        if run_ctx is not None and "ctx" in sig.parameters:
            kwargs["ctx"] = run_ctx
        return interpreter(program, **kwargs)
