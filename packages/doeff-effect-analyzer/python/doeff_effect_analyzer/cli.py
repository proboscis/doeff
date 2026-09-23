"""``doeff-effects`` — effect sets of Programs and env coverage, read without running.

    doeff-effects program  pkg.mod:service_program [--json]
    doeff-effects handler  pkg.handlers:clock_handler [--json]
    doeff-effects coverage pkg.mod:service_program --env pkg.envs:board_env \\
        [--outer doeff_core_effects.scheduler:scheduled] [--fold-carried Spawn] [--json]

``coverage`` exits 1 when an effect has no handler or a handler in the env could
not be read (it could hide a gap); 0 when every effect is covered.
Targets are imported (``--path`` entries and the working directory are
importable); no Program is run.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence

from doeff_effect_analyzer.handler_effects import (
    analyze_env,
    analyze_handler,
    check_coverage,
)
from doeff_effect_analyzer.program_effects import analyze_program, qualified_name

_DESCRIPTION = "Effect sets of doeff Programs and env coverage, read without running."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doeff-effects", description=_DESCRIPTION)
    parser.add_argument(
        "--path", action="append", default=[], help="Extra import path (repeatable)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    program = sub.add_parser("program", help="Effects a Program function performs.")
    program.add_argument("target", help="module:attr of a Program function")
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
    coverage.add_argument("--json", action="store_true")
    return parser


def _short(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _program(arguments: argparse.Namespace) -> int:
    report = analyze_program(arguments.target)
    if arguments.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"{report.target}: {', '.join(_short(n) for n in report.effect_names) or '(none)'}")
    for carried in report.carried:
        names = ", ".join(_short(n) for n in carried.program.effect_names) or "(none)"
        print(
            f"  carried by {_short(qualified_name(carried.carrier))}: "
            f"{carried.program.target}: {names}"
        )
    for item in report.unresolved:
        print(f"  unresolved at {item.location}: {item.reason}: {item.text}")
    return 0


def _handler(arguments: argparse.Namespace) -> int:
    handler = analyze_handler(arguments.target)
    if arguments.json:
        print(json.dumps(handler.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(handler.name)
    for clause in handler.clauses:
        emits = ", ".join(_short(n) for n in clause.emits.effect_names) or "-"
        print(f"  {clause.handles.__name__} → performs {emits}")
    for item in handler.unresolved:
        print(f"  unresolved at {item.location}: {item.reason}: {item.text}")
    return 0


def _coverage(arguments: argparse.Namespace) -> int:
    report = analyze_program(arguments.target)
    folded = set(arguments.fold_carried)
    effects = report.effect_types_with(
        lambda carrier: carrier.__name__ in folded or qualified_name(carrier) in folded
    )
    env = [*(analyze_handler(spec) for spec in arguments.outer), *analyze_env(arguments.env)]
    result = check_coverage(effects, env, origin=report.target)
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
