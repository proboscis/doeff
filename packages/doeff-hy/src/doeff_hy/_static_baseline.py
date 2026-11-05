"""Comparison of doeff-hy-check with a baseline (`--baseline <json>`, `--write-baseline <json>`).

The idea is the same as doeff-linter's `--baseline-report` (packages/doeff-linter/src/baseline.rs): check against a saved list of known errors,
separate "errors that were already there" from "new errors", and stop only on the new ones. There are two differences.

- Identifier = `(path, rule, message)`, with no line number. Just adding lines above does not turn the same error into a new error.
- Errors with the same identifier are compared **by count** (a multiset). Type errors appear many times in one file with the same message, so comparing as a set
  would miss the second identical error. Only the amount beyond the baseline is new errors.
- Names Hy assigns during expansion (`_hy_anon_<number>`, `_hy_let_first_<number>`, etc.) are compared after folding the number into a single marker. The numbers are assigned in expansion order, so
  adding just one definition shifts the later numbers, and the message of the same error changes so that it looks like a new error.
  Errors with the same message after folding are still compared by count, so an increase in errors on anonymous names remains new errors.
- File moves / renames: when the rule and message are the same, only the path differs, and the baseline's count for that identifier has now dropped, they are regarded as the same error one to one
  (the same as new_criticals in the linter).

Shape of the baseline file (JSON, version 1):
    {"version": 1, "errors": [{"path": ..., "rule": ..., "message": ..., "line": ...}, ...]}
`line` is for the reader's reference and is not used in comparison.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from doeff_hy_bytecode_guard._records import fold_anon_var_numbers

BASELINE_VERSION = 1

# The numbers of names doeff-hy's lazy assigns during expansion (`_lazy_sent_cached_1`, etc.). The shape and folding of names Hy assigns (`_hy_anon_44`, `_hy_let_first_28`, etc.) are
# defined in the definition source of Hy's internal names (doeff_hy_bytecode_guard._records.fold_anon_var_numbers) and are not held here.
# In the comparison key they are folded into one marker. Line numbers and digits in type names do not start with `_hy_` / `_lazy_`, so they are not folded (the shape of #2286 in vg-w39 + the lazy names of kn-w26).
_LAZY_GENERATED_NUMBER = re.compile(r"\b(_lazy_[A-Za-z0-9_]*?[A-Za-z])_\d+\b")


class Reported(Protocol):
    """The shape of a diagnostic compared with the baseline (satisfied by static_check.Diagnostic)."""

    @property
    def path(self) -> str: ...
    @property
    def line(self) -> int: ...
    @property
    def severity(self) -> str: ...
    @property
    def rule(self) -> str: ...
    @property
    def message(self) -> str: ...


_D = TypeVar("_D", bound=Reported)


@dataclass(frozen=True)
class Identity:
    """An error identifier that does not include the line number."""

    path: str
    rule: str
    message: str

    @property
    def without_path(self) -> tuple[str, str]:
        return (self.rule, self.message)


@dataclass(frozen=True)
class Baseline:
    """A multiset of the baseline's error identifiers."""

    counts: Counter[Identity]


@dataclass(frozen=True)
class Split(Generic[_D]):
    """The result of splitting this run's errors into those present in the baseline and new ones (both in original order)."""

    known: tuple[_D, ...]
    new: tuple[_D, ...]


class BaselineUnreadable(ValueError):
    """The baseline file cannot be read or has the wrong shape (the caller exits with code 2)."""


def comparable_message(message: str) -> str:
    """The message of the comparison key -- folds the numbers of Hy's anonymous names (so that even if adding a definition shifts the numbers, the same error is counted under the same key)."""
    return _LAZY_GENERATED_NUMBER.sub(r"\1_#", fold_anon_var_numbers(message, "#"))


def identity(diagnostic: Reported) -> Identity:
    """Build the key to compare with the baseline (drop the line number and fold anonymous-name numbers, so that line or number shifts do not turn the same error into a new error)."""
    return Identity(diagnostic.path, diagnostic.rule, comparable_message(diagnostic.message))


def errors_of(diagnostics: list[_D]) -> list[_D]:
    """Extract only the targets to stop on = error (warnings are not part of the baseline comparison)."""
    return [d for d in diagnostics if d.severity == "error"]


def baseline_json(diagnostics: list[Reported]) -> str:
    """Turn this run's errors into the baseline file's shape (order fixed by path, then line)."""
    rows = sorted(
        (
            {"path": d.path, "rule": d.rule, "message": d.message, "line": d.line}
            for d in errors_of(diagnostics)
        ),
        key=lambda row: (str(row["path"]), int(row["line"]), str(row["rule"]), str(row["message"])),
    )
    return json.dumps({"version": BASELINE_VERSION, "errors": rows}, ensure_ascii=False, indent=1) + "\n"


def _identity_of_row(row: object) -> Identity:
    """Read one line of the baseline file into an identifier (a JSON boundary -- so that a baseline with a missing field is not silently read as empty)."""
    match row:
        case {"path": str(path), "rule": str(rule), "message": str(message)}:
            return Identity(path, rule, comparable_message(message))
        case _:
            raise BaselineUnreadable(f"baseline error lacks the fields path, rule, and message: {row!r}")


def read_baseline(path: Path) -> Baseline:
    """Read the `--baseline` file. When it cannot be read or the version differs, BaselineUnreadable (so that new errors are not silently let through)."""
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BaselineUnreadable(f"cannot read baseline {path}: {error}") from error
    match loaded:
        case {"version": 1, "errors": list(rows)}:
            return Baseline(Counter(_identity_of_row(row) for row in rows))
        case {"version": version}:
            raise BaselineUnreadable(f"baseline {path} has unreadable version {version!r} (readable version = {BASELINE_VERSION})")
        case _:
            raise BaselineUnreadable(f"baseline {path} has the wrong shape (needs version and a list of errors)")


def split(baseline: Baseline, diagnostics: list[_D]) -> Split[_D]:
    """Compare this run's errors with the baseline and split them. For the same identifier, up to the baseline count is existing and the excess is new."""
    errors = errors_of(diagnostics)
    current = Counter(identity(d) for d in errors)
    # File moves / renames: count the identifiers that dropped in the baseline per key excluding path, and make them usable slots.
    vanished = Counter(
        ident.without_path
        for ident, count in (baseline.counts - current).items()
        for _ in range(count)
    )
    budget = baseline.counts.copy()
    ordered = sorted(errors, key=lambda d: (d.path, d.line))
    verdicts: dict[int, bool] = {}
    for diagnostic in ordered:
        key = identity(diagnostic)
        if budget[key] > 0:
            budget[key] -= 1
            verdicts[id(diagnostic)] = True
        elif vanished[key.without_path] > 0:
            vanished[key.without_path] -= 1
            verdicts[id(diagnostic)] = True
        else:
            verdicts[id(diagnostic)] = False
    return Split(
        known=tuple(d for d in errors if verdicts[id(d)]),
        new=tuple(d for d in errors if not verdicts[id(d)]),
    )
