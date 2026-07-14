"""doeff CLI entry point.

Usage:
    doeff run --program myapp.module.program
    doeff run --program myapp.program --interpreter myapp.interpreter
    doeff run --program myapp.program --env myapp.env
    doeff run --program myapp.program --set name=Alice --set count=10
    doeff run -c 'from doeff import Ask; yield Ask("key")'
    doeff run --hy '(import doeff [Pure]) (Pure 42)'
    doeff run --program myapp.program --apply myapp.transforms.wrap
    doeff run --program myapp.program -- script.py
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterable
from typing import Any, cast

from doeff.cli.profiling import (
    print_profiling_status,
    profiling_config_from_env,
    use_profiling_config,
)
from doeff.cli.run_services import (
    RunContext,
    execute,
    import_symbol,
    resolve_context,
)
from doeff.cli.runbox import maybe_create_runbox_record

_DEFAULT_RUNNER = "doeff.runners.local.run_local"


class RunArgs(argparse.Namespace):
    command: str
    func: Callable[["RunArgs"], int]
    program: str | None
    code: str | None
    hy_code: str | None
    interpreter: str | None
    envs: list[str] | None
    set_vars: list[str] | None
    apply: list[str] | None
    transform: list[str] | None
    runner: str | None
    format: str
    no_runbox: bool
    script: str | None
    raw_argv: list[str]


_HY_FLAG_REWRITE = {
    "interpreter": (
        "--interpreter",
        """--hy builds the handler stack inline instead of delegating to a
  Python interpreter function. Import the composing function in Hy and
  apply it to your Program:

    doeff run --hy '
    (import myapp [my_program])
    (import myapp.sim [sim_interpreter])
    (sim_interpreter my_program)
    '

  Migration reference: VAULT or design_doeff_run_redesign.md.""",
    ),
    "envs": (
        "--env",
        """--hy reaches env values through handler composition, not a CLI
  flag. Two options:

    # 1. Wrap with lazy_ask for structured / Program env entries:
    doeff run --hy '
    (import myapp [my_program])
    (import doeff-core-effects [lazy-ask])
    ((lazy-ask :env {"service_client" (myapp.build-client)})
     my_program)
    '

    # 2. Point to string secrets via DOEFF_* environment variables:
    export DOEFF_OPENAI_API_KEY=sk-...
    doeff run --hy '(import myapp [my_program]) my_program'

  Both paths play well together; see env_var_ask for lazy {path} syntax.""",
    ),
    "set_vars": (
        "--set",
        """--hy overrides values via Local (scoped) or DOEFF_* env vars
  (ambient). Example replacement for ``--set model=gpt-4``:

    # a. Scoped override in your Hy source:
    doeff run --hy '
    (import myapp [my_program])
    (import doeff [Local])
    (Local {"model" "gpt-4"} my_program)
    '

    # b. Ambient env var (read by env_var_ask):
    export DOEFF_model=gpt-4
    doeff run --hy '(import myapp [my_program]) my_program'""",
    ),
    "apply": (
        "--apply",
        """--hy composes Kleisli arrows inline. Replacement for
  ``--apply myapp.transforms.double``:

    doeff run --hy '
    (import myapp [my_program])
    (import myapp.transforms [double])
    (-> my_program double)
    '""",
    ),
    "transform": (
        "--transform",
        """--hy composes Program -> Program transforms inline. Replacement
  for ``--transform myapp.memoize --transform myapp.sim``:

    doeff run --hy '
    (import myapp [my_program])
    (import myapp.memoize myapp.sim)
    (-> my_program myapp.memoize myapp.sim)
    '""",
    ),
}


_LEGACY_FLAG_DEPRECATION = {
    "interpreter": """--interpreter is deprecated. Migration:
  # Before
  doeff run --program myapp.p --interpreter myapp.sim_interpreter
  # After (compose inline with Hy)
  doeff run --hy '(import myapp [p]) (import myapp.sim [sim_interpreter]) (sim_interpreter p)'""",
    "envs": """--env is deprecated. Migration:
  # Before
  doeff run --program myapp.p --env myapp.env_dict
  # After — choose one:
  #  (a) inline lazy-ask in Hy:
  doeff run --hy '(import myapp [p]) (import doeff-core-effects [lazy-ask]) ((lazy-ask :env myapp.env_dict) p)'
  #  (b) string secrets via DOEFF_* env vars:
  export DOEFF_OPENAI_API_KEY=sk-...; doeff run --program myapp.p""",
    "set_vars": """--set is deprecated. Migration:
  # Before
  doeff run --program myapp.p --set model=gpt-4
  # After — choose one:
  #  (a) scoped Local inside Hy:
  doeff run --hy '(import myapp [p]) (import doeff [Local]) (Local {"model" "gpt-4"} p)'
  #  (b) ambient env var:
  export DOEFF_model=gpt-4; doeff run --program myapp.p""",
    "apply": """--apply is deprecated. Migration:
  # Before
  doeff run --program myapp.p --apply myapp.transforms.double
  # After (inline threading with Hy)
  doeff run --hy '(import myapp [p]) (import myapp.transforms [double]) (-> p double)'""",
    "transform": """--transform is deprecated. Migration:
  # Before
  doeff run --program myapp.p --transform myapp.sim
  # After (inline composition with Hy)
  doeff run --hy '(import myapp [p]) (import myapp.sim) (-> p myapp.sim)'""",
}


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


def _check_hy_conflicts(args: RunArgs) -> int | None:
    """Hard-fail with a rewrite example when ``--hy`` meets a legacy flag."""
    violations = [attr for attr in _HY_FLAG_REWRITE if getattr(args, attr, None)]
    if not violations:
        return None
    sections = []
    for attr in violations:
        flag, rewrite = _HY_FLAG_REWRITE[attr]
        sections.append(
            f"Error: --hy cannot be combined with {flag}.\n\n  {rewrite}"
        )
    print("\n\n".join(sections), file=sys.stderr)
    return 2


def _warn_legacy_flags(args: RunArgs) -> None:
    """Emit a deprecation notice for every legacy flag the caller used."""
    used = [attr for attr in _LEGACY_FLAG_DEPRECATION if getattr(args, attr, None)]
    if not used:
        return
    lines = [
        "[doeff] DeprecationWarning: the following flags are deprecated "
        "and will be removed in a future release:",
    ]
    for attr in used:
        lines.append("")
        lines.append(_LEGACY_FLAG_DEPRECATION[attr])
    print("\n".join(lines), file=sys.stderr)


def _build_runner_context(args: RunArgs):
    from doeff.cli.run_services import RunnerContext
    return RunnerContext(
        program_ref=args.program,
        py_source=args.code,
        hy_source=args.hy_code,
        runner_ref=args.runner or _DEFAULT_RUNNER,
        format=args.format,
        raw_argv=list(args.raw_argv),
    )


def _dispatch_runner(args: RunArgs) -> int:
    """Resolve the runner function and invoke it with a RunnerContext."""
    from doeff.cli.run_services import import_callable

    runner_ref = args.runner or _DEFAULT_RUNNER
    try:
        runner = import_callable(runner_ref)
    except Exception as exc:
        print(
            "Error: failed to import --runner "
            f"{runner_ref!r}: {exc}\n\n"
            "  A runner is a callable of the form ``fn(ctx: RunnerContext) -> int``.\n"
            "  Builtin: doeff.runners.local\n\n"
            "  Examples:\n"
            "    doeff run --hy '(import doeff [Pure]) (Pure 1)'\n"
            "    doeff run --hy '(Pure 1)' --runner doeff.runners.local\n"
            "    doeff run --hy '(Pure 1)' --runner myapp.runners.k3s\n",
            file=sys.stderr,
        )
        return 1

    ctx = _build_runner_context(args)
    result = runner(ctx)
    if isinstance(result, int):
        return result
    return 0


def handle_run_hy(args: RunArgs) -> int:
    conflict = _check_hy_conflicts(args)
    if conflict is not None:
        return conflict
    source = args.hy_code
    if source == "-":
        args.hy_code = sys.stdin.read()
    elif not source or not source.strip():
        print(
            "Error: No Hy source provided.\n\n"
            "  Pass a Hy block directly, or use '-' to read from stdin:\n\n"
            "    doeff run --hy '(import doeff [Pure]) (Pure 42)'\n"
            "    cat my_entrypoint.hy | doeff run --hy -\n",
            file=sys.stderr,
        )
        return 1
    maybe_create_runbox_record(skip_runbox=args.no_runbox)
    return _dispatch_runner(args)


def handle_run(args: RunArgs) -> int:
    if args.hy_code is not None:
        return handle_run_hy(args)

    if args.code is not None:
        _warn_legacy_flags(args)
        if args.runner and args.runner != _DEFAULT_RUNNER:
            maybe_create_runbox_record(skip_runbox=args.no_runbox)
            code_result = _dispatch_runner(args)
        else:
            code_result = handle_run_code(args)
        return code_result

    if not args.program:
        print(
            "Error: one of PROGRAM / --hy / -c is required.\n\n"
            "  Examples:\n"
            "    doeff run --program myapp.entrypoints.p_daily\n"
            "    doeff run --hy '(import doeff [Pure]) (Pure 42)'\n"
            "    doeff run -c 'from doeff import Pure; Pure(42)'\n",
            file=sys.stderr,
        )
        return 1

    _warn_legacy_flags(args)

    if args.runner and args.runner != _DEFAULT_RUNNER:
        maybe_create_runbox_record(skip_runbox=args.no_runbox)
        return _dispatch_runner(args)

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
    source_group.add_argument(
        "--hy", dest="hy_code", metavar="HYCODE",
        help=(
            "Execute a Hy source block directly. The block is auto-wrapped "
            "in (do! ...) and runs without --interpreter/--env/--set/--apply/"
            "--transform — configure handlers inline. Use '-' to read stdin."
        ),
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
        "--runner", metavar="PATH",
        help=(
            "Runner callable (default: doeff.runners.local). Signature: "
            "fn(ctx: RunnerContext) -> int. Remote runners (k3s, docker) "
            "reconstruct the command via ctx.raw_argv / ctx.hy_source."
        ),
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


def _main(argv: Iterable[str] | None = None) -> int:
    print_profiling_status()
    parser = build_parser()
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    args = cast(RunArgs, parser.parse_args(argv_list, RunArgs()))
    args.raw_argv = argv_list
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


def main(argv: Iterable[str] | None = None) -> int:
    profiling_config = profiling_config_from_env(os.environ)
    with use_profiling_config(profiling_config):
        return _main(argv)


if __name__ == "__main__":
    sys.exit(main())
