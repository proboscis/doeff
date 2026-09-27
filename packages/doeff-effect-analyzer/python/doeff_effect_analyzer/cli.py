"""``doeff-effects`` — effect sets of Programs and env coverage, read without running.

    doeff-effects program  pkg.mod:service_program [--bind foundation=pkg.envs:prod] [--json]
    doeff-effects handler  pkg.handlers:clock_handler [--json]
    doeff-effects coverage pkg.mod:service_program --env pkg.envs:board_env \\
        [--outer doeff_core_effects.scheduler:scheduled] [--fold-carried Spawn] \\
        [--bind foundation=pkg.envs:prod] [--json]

``--bind name=module:attr`` binds a parameter of the Program function (a
foundation it takes as an argument) to a module-level value.
``coverage`` exits 1 when an effect has no handler, a handler on the way (in
the env, or installed inside the Program) could not be read, or a place in the
Program could not be followed (either could hide a gap); 0 when every effect is
covered.  Targets are imported (``--path`` entries and the working directory are
importable); no Program is run.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

from doeff_effect_analyzer.handler_effects import (
    analyze_env,
    analyze_handler,
    check_coverage,
)
from doeff_effect_analyzer.program_effects import (
    ProgramEffects,
    analyze_program,
    qualified_name,
    resolve_target,
)

_DESCRIPTION = "Effect sets of doeff Programs and env coverage, read without running."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doeff-effects", description=_DESCRIPTION)
    parser.add_argument(
        "--path", action="append", default=[], help="Extra import path (repeatable)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    program = sub.add_parser("program", help="Effects a Program function performs.")
    program.add_argument("target", help="module:attr of a Program function")
    _add_bind(program)
    program.add_argument("--json", action="store_true")

    handler = sub.add_parser("handler", help="Effects a handler handles and its clauses perform.")
    handler.add_argument("target", help="module:attr of a handler or handler factory")
    handler.add_argument("--json", action="store_true")

    coverage = sub.add_parser("coverage", help="Does an env handle every effect of a Program?")
    coverage.add_argument("target", help="module:attr of a Program function")
    coverage.add_argument("--env", required=True, help="module:attr of an env builder")
    coverage.add_argument(
        "--outer",
        action="append",
        default=[],
        help="Handler installed outside the env (e.g. the scheduler), outermost first.",
    )
    coverage.add_argument(
        "--fold-carried",
        action="append",
        default=[],
        help="Carrier name whose carried Programs run under the same handlers (e.g. Spawn).",
    )
    _add_bind(coverage)
    coverage.add_argument("--json", action="store_true")
    return parser


def _add_bind(parser: argparse.ArgumentParser) -> None:
    """``--bind``: read a Program that takes its foundation as a parameter."""
    parser.add_argument(
        "--bind",
        action="append",
        default=[],
        metavar="NAME=MODULE:ATTR",
        help="Bind a parameter of the Program function to a module-level value (repeatable).",
    )


def _bindings(specs: Sequence[str]) -> dict[str, Any]:
    """``NAME=MODULE:ATTR`` specs → parameter name → the imported value."""
    out: dict[str, Any] = {}
    for spec in specs:
        name, sep, target = spec.partition("=")
        if not sep or not name or not target:
            raise SystemExit(f"--bind expects NAME=MODULE:ATTR, got {spec!r}")
        out[name] = resolve_target(target)
    return out


def _short(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _names(names: Sequence[str]) -> str:
    """Short effect names for one report line."""
    return ", ".join(_short(n) for n in names) or "(none)"


def _print_program(report: ProgramEffects, indent: str) -> None:
    """The handled scopes (with what leaves each), carried Programs and unresolved places."""
    for scope in report.handled:
        handlers = ", ".join(h.name if h.known else f"{h.name} (not read)" for h in scope.handlers)
        print(
            f"{indent}under [{handlers}] at {scope.location}: {scope.program.target}: "
            f"{_names(scope.program.effect_names)} → leaves {_names(scope.residual.effect_names)}"
        )
        _print_program(scope.program, indent + "  ")
    for carried in report.carried:
        print(
            f"{indent}carried by {_short(qualified_name(carried.carrier))}: "
            f"{carried.program.target}: {_names(carried.program.effect_names)}"
        )
    for item in report.unresolved:
        print(f"{indent}unresolved at {item.location}: {item.reason}: {item.text}")


def _program(arguments: argparse.Namespace) -> int:
    report = analyze_program(arguments.target, bindings=_bindings(arguments.bind))
    if arguments.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"{report.target}: {_names(report.effect_names)}")
    _print_program(report, "  ")
    return 0


def _handler(arguments: argparse.Namespace) -> int:
    handler = analyze_handler(arguments.target)
    if arguments.json:
        print(json.dumps(handler.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"{handler.name} ({handler.basis.value})")
    for clause in handler.clauses:
        emits = ", ".join(_short(n) for n in clause.emits.effect_names) or "-"
        print(f"  {clause.handles.__name__} → performs {emits}")
    for item in handler.unresolved:
        print(f"  unresolved at {item.location}: {item.reason}: {item.text}")
    return 0


def _coverage(arguments: argparse.Namespace) -> int:
    report = analyze_program(arguments.target, bindings=_bindings(arguments.bind))
    folded = set(arguments.fold_carried)

    def include(carrier: Any) -> bool:
        """Fold carried Programs whose carrier is named in --fold-carried."""
        return getattr(carrier, "__name__", None) in folded or qualified_name(carrier) in folded

    env = [*(analyze_handler(spec) for spec in arguments.outer), *analyze_env(arguments.env)]
    result = check_coverage(report, env, include=include)
    if arguments.json:
        print(
            json.dumps(
                {
                    "target": report.target,
                    "env": arguments.env,
                    "gaps": [
                        {"effect": qualified_name(g.effect), "performedBy": g.origin}
                        for g in result.gaps
                    ],
                    "unknownHandlers": list(result.unknown_handlers),
                    "unresolved": [
                        {"reason": u.reason, "text": u.text, "at": str(u.location)}
                        for u in result.unresolved
                    ],
                    "complete": result.complete,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for gap in result.gaps:
            print(f"no handler: {gap}")
        for name in result.unknown_handlers:
            print(f"handler could not be read (may hide a gap): {name}")
        for item in result.unresolved:
            print(
                f"could not follow (may hide a gap) at {item.location}: {item.reason}: {item.text}"
            )
        print(
            f"{report.target} under {arguments.env}: "
            f"{'covered' if result.complete else 'NOT covered'}"
        )
    return 0 if result.complete else 1


_COMMANDS = {"program": _program, "handler": _handler, "coverage": _coverage}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    for entry in [os.getcwd(), *arguments.path]:
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return _COMMANDS[arguments.command](arguments)


if __name__ == "__main__":
    raise SystemExit(main())
