"""The carry of doeff-hy-check (`--carry <file>`): a .hy whose inputs did not change since the previous run is not given to pyright; the previous run's pyright diagnostics are carried over instead.

Why: the daily type measurement of agora-controllers (hy-type-trend) gave every .hy in scope (about 3,000) to pyright on each run; the seconds grew with the size of the repo and
the load on the machine, and the run was cut off twice in a row by the 7,200-second limit of the processing stage. The diagnostics pyright emits for one file are
determined only by the expansion of that file, the declarations that fix the types of the names it uses (inside the modules reached by import), and the environment (pyright version, configuration, python search path).
A file for which all of these are the same as in the previous run reuses the previous run's diagnostics as they are.

Inputs (digest per file: InputGraph.inputs_of):
- The file itself: the .hy source, the expanded Python, and the position map (a diagnostic position is mapped back to the .hy position through the expansion and the map).
- The declaration of each name the file uses (one module name: _Node), and the declarations of the names its surface uses (all of them, transitively). Surface = the part pyright reads on the caller side
  to decide the type of that name:
  - A function whose types are all declared (arguments and result) = decorators and signature. If the body is empty (`...`, `pass`, or a raise of NotImplementedError) it is kept as is;
    if it is only a docstring it becomes a mark, and any other body is replaced by a single mark (an empty method of a Protocol counts as a method with no implementation, so whether the body is empty is part of the surface).
  - A class = the whole class (methods are reduced by the rule for functions above; but a method that assigns to an attribute of self / cls keeps its whole body, since pyright
    infers the attribute type from there).
  - A function without type declarations, a function with global / nonlocal, a variable, a type alias, etc. = the whole statement (pyright infers the type from the value and the body). At module level,
    import statements are split per name.
  - Docstrings are dropped and line numbers are not looked at (ast.dump without positions): rewriting a description or moving lines is not passed on to the users.
  - __all__: for the declaration of a single name, only whether that name is in __all__ is included (adding another name to __all__ is not passed on to users). A module that writes __all__ in a
    form pyright cannot read (anything other than assignment of a sequence of strings, +=, extend, append), and the whole set of names of a module (star import), use
    the whole of the __all__ statements.
- Where the modules the file imports resolve to (in each directory of the search path, the existing ones among the candidate files pyright may hit: Resolution).
  The digest changes when candidates appear or disappear (a new file hides a previous resolution, or an import that failed to resolve now resolves). The digest of a name declaration also includes the
  resolution of its module.
- The environment fingerprint (Environment.fingerprint): the version of the carry format, the pyright version, the pyright configuration, the python version and search path, the names of the contents of the search-path
  directories under the python prefix (site-packages, the standard library; dist-info names = distribution versions), the contents of .pth files, and the source of the functions that produce diagnostics (semantics:
  it is a syntax tree, so comments and indentation are not included). Search paths whose contents are looked at per file (the root, extraPaths, typings, directories outside the prefix: the checkout of
  doeff installed as editable) are not put into the fingerprint (for an editable dist-info, only the name without the version): even when the pin or version of doeff goes up, only the .hy files that use
  a changed declaration are checked again.
- Place marks (Places): a daily run puts the root under a per-task working directory and puts the checkout of the doeff pin next to the root (the path differs on every run).
  The spelling of the root and the root's parent inside the digest material is replaced by marks (@root, @parent), so measuring the same tree in a different place gives the same digest.

Falling to the safe side: a Python module that cannot be read because of a syntax error gets a different digest on every run (the files that use it are checked every time). A reference that uses a module as a value, and
a star import, depend on all names of that module (_Node name = None). When a name is not in the module, it depends on the targets of star imports, the module's __getattr__, and
the modules under the same name. Names inside function bodies are not distinguished from module names (the same spelling depends on it: this only causes extra checking).

Refresh (refresh_runs): each run re-checks the ceil(count / refresh_runs) files with unchanged inputs whose last check is oldest, counts the files whose result differs
from the carried diagnostics, and reports them (refresh_mismatches in the summary). If this definition of inputs misses a dependency, it shows up here (a file whose pyright answer
varies with the order of checking also shows up here).

Carry document (JSON, version CARRY_VERSION): {"version", "fingerprint", "run", "summary", "files": {relative path: {"inputs", "checked",
"rows": [[line, column, severity, rule, message], …]}}}. run = the run number (1 for a run without a carry); checked = the run number in which the file was given to pyright;
rows = the pyright diagnostics of that run (.hy positions, in pyright's order, before tidying; the tidying (static_check.settle) is applied every time to the carried and the freshly checked ones together).
summary is the field read by the user (scripts/hy_type_trend.hy of agora-controllers): run, checked, carried, reasons, refresh_mismatches, mismatched.

Per-group rewriting (--carry-group): the files to check are split into groups and given to pyright, and each time a group is finished,
an interim document is written (next_record's done = the files already checked). The interim document holds only the current diagnostics of the checked files, the carried files, and the previous run's records of the
refresh files not yet checked; it does not hold new or changed files not yet checked (the next run treats them as new). Even if the user cuts the
tool off by time, the diagnostics of the finished groups remain for the next run. Previously a cut-off run wrote no document, and when the run that checks everything (the run where the environment fingerprint changed) exceeded the limit,
the next run checked everything again and could never finish measuring (13 daily runs, 2026-10-27 to 29).
"""

import ast
import hashlib
import inspect
import json
import keyword
import math
import os
import re
import subprocess
import sys
import textwrap
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from pathlib import Path
from typing import TypeVar

#: Version of the carry document and of how inputs are decided (raise it when the decision changes: it enters the fingerprint, so everything carried from the previous run is checked again).
CARRY_VERSION = 1
#: Default refresh period (runs): even a file with unchanged inputs is checked again once within this number of runs (agora-controllers: 6 daily runs / day over 2 days).
DEFAULT_REFRESH_RUNS = 12
#: Words for the reason a file is checked (keys of the summary's reasons): absent = no carry file (first run); unreadable = the carry file cannot be read;
#: environment = the environment fingerprint differs; new = a file not in the previous run; changed = the inputs changed; refresh = refresh.
REASONS = ("absent", "unreadable", "environment", "new", "changed", "refresh")
#: Upper limit on the number of files, listed by name in the summary, whose answer differed on refresh.
MISMATCH_SHOWN = 20
#: Upper limit on the length of a string constant in the surface that is read as an annotation expression (a long message is not an expression).
_ANNOTATION_TEXT_LIMIT = 400
#: Characters of a string that is read as an annotation (names, attributes, subscripts, unions, strings and numbers inside Literal); a message containing other characters is not read as an expression.
_ANNOTATION_TEXT = re.compile(r"[\w.\[\](),|'\"\s*=-]+")
#: The mark put in place of a reduced body (non-empty body), and the mark for a docstring-only body.
_BODY_MARK = "<body>"
_DOCSTRING_MARK = "<docstring>"
#: The spelling of the pyright version (even if the wrapper tool adds a notice about a newer version, the fingerprint is decided by the version alone).
_PYRIGHT_VERSION = re.compile(r"pyright \d+\.\d+\.\d+")
#: The command that reads the python search path (pyright also asks the same python for the search path).
_PATH_PROBE = (
    "import json, sys; print(json.dumps({'path': sys.path, 'prefix': sys.prefix, "
    "'base': sys.base_prefix, 'version': sys.version}))"
)


@dataclass(frozen=True, order=True)
class CarriedRow:
    """One pyright diagnostic (a .hy position): a row of the carry document (the path is held by the owning CarriedFile)."""

    line: int
    column: int
    severity: str
    rule: str
    message: str


@dataclass(frozen=True)
class Candidate:
    """One candidate for where a module resolves: path and kind (pyi, py, hy, hy-failed, hy-unstaged, package, init, native, typed:<digest of contents>)."""

    path: str
    kind: str


#: Where a module resolves: in search-path order, the sequence of existing candidates. Empty = not on the root or the editable search paths (site-packages, the standard library: covered by the fingerprint,
#: or unresolvable).
Resolution = tuple[Candidate, ...]

_V = TypeVar("_V")


def _grouped(pairs: Iterable[tuple[str, _V]]) -> dict[str, tuple[_V, ...]]:
    """Turn a sequence of (key, value) into a tuple of values per key (values in arrival order), to build an index from a name to its declarations."""
    ordered = sorted(
        ((key, order, value) for order, (key, value) in enumerate(pairs)), key=itemgetter(0, 1)
    )
    return {
        key: tuple(value for _, _, value in group)
        for key, group in groupby(ordered, key=itemgetter(0))
    }


def _resolution_json(resolution: Resolution) -> str:
    """To turn a resolution into a string for the digest material."""
    return json.dumps(
        [[candidate.path, candidate.kind] for candidate in resolution], ensure_ascii=False
    )


# --- carry document ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FileInputs:
    """The input of one file in this run: path = path relative to the root; inputs = the input digest (InputGraph.inputs_of)."""

    path: str
    inputs: str


@dataclass(frozen=True)
class FileRows:
    """The pyright diagnostics of one file in this run (in pyright's order)."""

    path: str
    rows: tuple[CarriedRow, ...]


@dataclass(frozen=True)
class CarriedFile:
    """One file of the carry document: inputs = the input digest when it was checked; checked = the run number when it was checked; rows = the pyright diagnostics of that run (
    in pyright's order, so the output order of a carried run is the same as that of a run that checked everything)."""

    path: str
    inputs: str
    checked: int
    rows: tuple[CarriedRow, ...]


@dataclass(frozen=True)
class CarryRecord:
    """One carry document: fingerprint = the environment fingerprint; run = the run number; files = the record per file (in path order)."""

    fingerprint: str
    run: int
    files: tuple[CarriedFile, ...]


class CarryUnreadableError(ValueError):
    """The carry file exists but cannot be read, or its shape or version differs (the caller checks everything: the carry is a shortcut for the measurement and must not be a reason to stop the measurement)."""


def read_carry(path: Path) -> CarryRecord | None:
    """Read the previous run's carry file (None = no file: first run). If it cannot be read or its shape or version differs, CarryUnreadableError."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise CarryUnreadableError(f"cannot read carry file {path}: {error}") from error
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise CarryUnreadableError(f"carry file {path} is not JSON: {error}") from error
    return _record_of(loaded, path)


def _record_of(loaded: object, path: Path) -> CarryRecord:
    """Read the JSON of a carry document (a JSON boundary: missing fields are not filled with defaults)."""
    match loaded:
        case {"version": 1, "fingerprint": str(fingerprint), "run": int(run), "files": dict(files)}:
            return CarryRecord(
                fingerprint,
                run,
                tuple(_file_of(str(key), value, path) for key, value in sorted(files.items())),
            )
        case {"version": version} if version != CARRY_VERSION:
            raise CarryUnreadableError(
                f"cannot read version {version!r} of carry file {path} (readable version = {CARRY_VERSION})"
            )
        case _:
            raise CarryUnreadableError(
                f"the shape of carry file {path} differs (version, fingerprint, run, and files are required)"
            )


def _file_of(key: str, value: object, path: Path) -> CarriedFile:
    """Read one file of the carry document."""
    match value:
        case {"inputs": str(inputs), "checked": int(checked), "rows": list(rows)}:
            return CarriedFile(key, inputs, checked, tuple(_row_of(row, path) for row in rows))
        case _:
            raise CarryUnreadableError(
                f"{key} in carry file {path} lacks inputs, checked, or rows: {value!r}"[
                    :400
                ]
            )


def _row_of(row: object, path: Path) -> CarriedRow:
    """Read one diagnostic of the carry document."""
    match row:
        case [int(line), int(column), str(severity), str(rule), str(message)]:
            return CarriedRow(line, column, severity, rule, message)
        case _:
            raise CarryUnreadableError(f"the shape of a diagnostic in carry file {path} differs: {row!r}"[:400])


@dataclass(frozen=True)
class ReasonCount:
    """One reason for checking (a REASONS word) and the number of files checked for that reason."""

    reason: str
    count: int


@dataclass(frozen=True)
class Summary:
    """The numbers of one carry run: checked = files given to pyright; carried = files whose previous diagnostics were carried; reasons = counts per reason for checking (in the order of REASONS);
    mismatched = files re-checked on refresh whose answer differed from the carried diagnostics."""

    run: int
    checked: int
    carried: int
    reasons: tuple[ReasonCount, ...]
    mismatched: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        """Shape the summary field of the carry document read by users (a JSON boundary: names up to MISMATCH_SHOWN, counts for all)."""
        return {
            "run": self.run,
            "checked": self.checked,
            "carried": self.carried,
            "reasons": {count.reason: count.count for count in self.reasons},
            "refresh_mismatches": len(self.mismatched),
            "mismatched": list(self.mismatched[:MISMATCH_SHOWN]),
        }

    def line(self) -> str:
        """Make a one-line description for people of how many were checked and how many carried in this run (printed to standard error)."""
        reasons = (
            ", ".join(f"{count.reason} {count.count}" for count in self.reasons if count.count)
            or "none"
        )
        shown = ", ".join(self.mismatched[:MISMATCH_SHOWN])
        mismatch = f": {shown}" if self.mismatched else ""
        return (
            f"carry (run {self.run}): checked with pyright: {self.checked} .hy files, carried from previous run: {self.carried} .hy files"
            f" (reasons for checking: {reasons}); answer differed on refresh: {len(self.mismatched)} files{mismatch}"
        )


def carry_text(record: CarryRecord, summary: Summary) -> str:
    """Turn the carry document into a JSON string (a JSON boundary: files in path order; the same content gives the same string)."""
    files = {
        carried.path: {
            "inputs": carried.inputs,
            "checked": carried.checked,
            "rows": [
                [row.line, row.column, row.severity, row.rule, row.message] for row in carried.rows
            ],
        }
        for carried in sorted(record.files, key=lambda f: f.path)
    }
    document = {
        "version": CARRY_VERSION,
        "fingerprint": record.fingerprint,
        "run": record.run,
        "summary": summary.as_json(),
        "files": files,
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"


def write_carry(path: Path, text: str) -> None:
    """Keep this run's carry document for the next run (write under another name in the same directory and then replace, so if cut off midway the previous run's one remains)."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


# --- deciding which files to check ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Checking:
    """One file to give to pyright in this run: reason = the reason (a REASONS word); before = the previous run's record when checking for a refresh (compared with the re-checked answer;
    None if not a refresh)."""

    path: str
    reason: str
    before: CarriedFile | None = None


@dataclass(frozen=True)
class Plan:
    """The carry plan of this run: checking = files to give to pyright; carried = files whose previous diagnostics are carried; run = the number of this run."""

    checking: tuple[Checking, ...]
    carried: tuple[CarriedFile, ...]
    run: int

    @property
    def checked_paths(self) -> frozenset[str]:
        """The set of relative paths of the files to give to pyright (to select the files to check)."""
        return frozenset(checking.path for checking in self.checking)


def plan_carry(
    previous: CarryRecord | CarryUnreadableError | None,
    fingerprint: str,
    inputs: Sequence[FileInputs],
    refresh_runs: int,
) -> Plan:
    """Pure: decide the files to check and the files to carry in this run, from the previous run's carry (None = none; CarryUnreadableError = unreadable) and the current inputs."""
    if (
        previous is None
        or isinstance(previous, CarryUnreadableError)
        or previous.fingerprint != fingerprint
    ):
        reason = (
            "absent"
            if previous is None
            else "unreadable"
            if isinstance(previous, CarryUnreadableError)
            else "environment"
        )
        run = previous.run + 1 if isinstance(previous, CarryRecord) else 1
        return Plan(tuple(Checking(file.path, reason) for file in inputs), (), run)
    known = {
        carried.path: carried for carried in previous.files
    }  # index from path to the previous run's record (local to this function)
    moved = tuple(
        Checking(file.path, "new" if before is None else "changed")
        for file in inputs
        if (before := known.get(file.path)) is None or before.inputs != file.inputs
    )
    moved_paths = frozenset(checking.path for checking in moved)
    same = tuple(known[file.path] for file in inputs if file.path not in moved_paths)
    quota = math.ceil(len(inputs) / refresh_runs) if refresh_runs > 0 else 0
    refreshed = tuple(sorted(same, key=lambda carried: (carried.checked, carried.path))[:quota])
    refreshed_paths = frozenset(carried.path for carried in refreshed)
    return Plan(
        (*moved, *(Checking(carried.path, "refresh", carried) for carried in refreshed)),
        tuple(carried for carried in same if carried.path not in refreshed_paths),
        previous.run + 1,
    )


def next_record(  # noqa: DOEFF006 — a small internal grouped return
    plan: Plan,
    fingerprint: str,
    inputs: Sequence[FileInputs],
    fresh: Sequence[FileRows],
    done: frozenset[str] | None = None,
) -> tuple[CarryRecord, Summary]:
    """Pure: build this run's carry document and counts (checked files get the current diagnostics `fresh` and the current number; carried files keep the previous run's record). done =
    the files finished in the interim document of a group run (None = everything finished); the previous run's records of refresh files not yet checked are kept as they are, and
    new or changed files not yet checked are not kept (header comment "per-group rewriting"). The counts also count only the finished files."""
    answered = {
        rows.path: rows.rows for rows in fresh
    }  # index from path to this run's diagnostics (local to this function)
    carried = {
        kept.path: kept for kept in plan.carried
    }  # index from path to the record to carry (local to this function)
    checked = plan.checked_paths if done is None else plan.checked_paths & done
    waiting = {
        checking.path: checking.before
        for checking in plan.checking
        if checking.path not in checked and checking.before is not None
    }  # previous run's records of refresh files not yet checked (local to this function)
    files = tuple(
        CarriedFile(file.path, file.inputs, plan.run, answered.get(file.path, ()))
        if file.path in checked
        else carried[file.path]
        if file.path in carried
        else waiting[file.path]
        for file in inputs
        if file.path in checked or file.path in carried or file.path in waiting
    )
    finished = tuple(checking for checking in plan.checking if checking.path in checked)
    mismatched = tuple(
        sorted(
            checking.path
            for checking in finished
            if checking.before is not None
            and sorted(checking.before.rows) != sorted(answered.get(checking.path, ()))
        )
    )
    reasons = tuple(
        ReasonCount(reason, sum(1 for checking in finished if checking.reason == reason))
        for reason in REASONS
    )
    summary = Summary(plan.run, len(finished), len(plan.carried), reasons, mismatched)
    return CarryRecord(fingerprint, plan.run, files), summary


# --- environment fingerprint ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Place:
    """One place that differs per run: path = the spelling in this run; mark = the mark that is the same across runs."""

    path: str
    mark: str


@dataclass(frozen=True)
class Places:
    """Replace the spelling of per-run places inside digest material with marks (header comment "place marks"). places = from the longest spelling; pattern = a regex that matches any of
    them and picks it up only where it is followed by a path continuation (/) or a delimiter (the matched group number is the index in places + 1)."""

    places: tuple[Place, ...]
    pattern: re.Pattern[str] | None

    def spell(self, text: str) -> str:
        """Replace the spelling of places inside text with marks."""
        if self.pattern is None:
            return text
        return self.pattern.sub(lambda hit: self.places[(hit.lastindex or 1) - 1].mark, text)


def places_of(root: Path) -> Places:
    """Build the set of places that replace the root with @root and the root's parent (the sibling where the doeff pin checkout is put) with @parent (both the absolute and
    the resolved spelling, so a spelling through a symlink gets the same mark)."""
    absolute = Path(os.path.normpath(root.absolute()))
    pairs = [(absolute, "@root"), (absolute.resolve(), "@root")]
    if absolute.parent != absolute:
        pairs.extend([(absolute.parent, "@parent"), (absolute.resolve().parent, "@parent")])
    places = tuple(
        sorted(
            {Place(str(path), mark) for path, mark in pairs if path.parent != path},
            key=lambda place: (-len(place.path), place.path),
        )
    )
    if not places:
        return Places((), None)
    alternatives = "|".join(f"({re.escape(place.path)})" for place in places)
    return Places(places, re.compile(f"(?:{alternatives})" + r"(?=[/\"'\s,;:\])]|$)"))


@dataclass(frozen=True)
class Environment:
    """The environment fingerprint, the python search paths whose contents are looked at per file (directories outside the prefix: directories of editable checkouts), and the
    place marks (places) inside the digest material."""

    fingerprint: str
    searched: tuple[Path, ...]
    places: Places


def semantics_digest(functions: Iterable[Callable[..., object]]) -> str:
    """Build the digest of the source of the functions that produce pyright diagnostics and map them back to .hy positions, and of this module's source (if either changes,
    the previous run's diagnostics are not carried). This module's source is also read by inspect like the functions (the file is not read from a place path; tested by
    test_doeff_hy_sources_do_not_read_their_place in tests/test_static_cache.py). The material is the syntax tree (_syntax_of), and a rewrite of comments, indentation, or
    blank lines alone does not change the fingerprint (on 2026-10-17 adding four `# noqa` comment lines made every .hy be checked again)."""
    return _digest(
        (
            *(_syntax_of(inspect.getsource(function)) for function in functions),
            _syntax_of(inspect.getsource(sys.modules[__name__])),
        )
    )


def _syntax_of(source: str) -> str:
    """Pure: the spelling of the syntax tree of the source (ast.dump, dropping comments, indentation, and blank lines), so a rewrite that does not change meaning does not change the fingerprint."""
    return ast.dump(ast.parse(textwrap.dedent(source)))


def probe_environment(
    settings: Mapping[str, object], python: str, pyright: str, semantics: str, places: Places
) -> Environment:
    """Build the environment fingerprint (read the pyright version and the python search path in a child process; RuntimeError if they cannot be read). The spelling of places inside the configuration, search path, and .pth
    contents is replaced by marks (places)."""
    version = subprocess.run([pyright, "--version"], capture_output=True, text=True, check=False)
    named = _PYRIGHT_VERSION.search(version.stdout + version.stderr)
    if version.returncode != 0 or named is None:
        raise RuntimeError(
            f"cannot read the pyright version (exit {version.returncode}): {(version.stdout + version.stderr)[:400]}"
        )
    probed = subprocess.run(
        [python, "-B", "-I", "-c", _PATH_PROBE], capture_output=True, text=True, check=False
    )
    try:
        answer: object = json.loads(probed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"cannot read the python search path (exit {probed.returncode}): {probed.stderr[:400]}"
        ) from error
    match answer:
        case {
            "path": list(listed),
            "prefix": str(prefix),
            "base": str(base),
            "version": str(python_version),
        }:
            entries = tuple(str(e) for e in listed if isinstance(e, str) and e and Path(e).is_dir())
        case _:
            raise RuntimeError(f"the shape of the python search path differs: {probed.stdout[:400]}")
    fixed = tuple(Path(p).resolve() for p in (prefix, base))
    under_prefix = {
        entry: any(Path(entry).resolve().is_relative_to(f) for f in fixed) for entry in entries
    }
    listings = [[entry, _listing(Path(entry))] for entry in entries if under_prefix[entry]]
    searched = tuple(
        dict.fromkeys(
            Path(os.path.normpath(Path(e).absolute())) for e in entries if not under_prefix[e]
        )
    )
    fingerprint = _digest(
        (
            str(CARRY_VERSION),
            named.group(0),
            places.spell(
                json.dumps(dict(settings), sort_keys=True, ensure_ascii=False, default=str)
            ),
            python_version,
            places.spell(json.dumps(listed, ensure_ascii=False, default=str)),
            places.spell(json.dumps(listings, ensure_ascii=False)),
            semantics,
        )
    )
    return Environment(fingerprint, searched, places)


def _listing(place: Path) -> list[str]:
    """Make the fingerprint material that covers the type of one search path under the prefix: the names of its contents (dist-info names = distribution versions) and the contents of .pth files. For the dist-info of
    a distribution installed as editable, use the name without the version (the contents are covered by the per-file digests of the search path; on 2026-10-17 merely raising the doeff version
    0.5.0 -> 0.6.0 -> 0.7.0 made every .hy be checked again twice)."""
    return [
        piece
        for name in sorted(os.listdir(place))
        if name != "__pycache__"
        for piece in _listed(place, name)
    ]


def _listed(place: Path, name: str) -> tuple[str, ...]:
    """Fingerprint material for one item of a search path (.pth: name and contents; editable dist-info: the name without the version; others: the name)."""
    if name.endswith(".pth"):
        return (name, _text_of(place / name))
    if name.endswith(".dist-info") and _editable(place / name):
        return (f"editable:{name.split('-', 1)[0]}",)
    return (name,)


def _editable(dist_info: Path) -> bool:
    """Whether a dist-info is an editable install (dir_info.editable in direct_url.json, PEP 610; if unreadable, treated as not editable)."""
    try:
        answer: object = json.loads((dist_info / "direct_url.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    match answer:
        case {"dir_info": {"editable": True}}:
            return True
        case _:
            return False


def _text_of(path: Path) -> str:
    """The contents of a small file (the reason if unreadable): fingerprint material."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return f"unreadable: {error}"


def _digest(parts: Iterable[str]) -> str:
    """Turn a sequence of strings into one sha256 (lengths are put first when joining, so a mix-up of delimiters never gives the same digest to different sequences)."""
    hasher = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    return hasher.hexdigest()


# --- reading the contents of a module -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Imported:
    """One destination of an import: module = the spelling of the imported module; level = relative depth; name = the from-name (None = the module itself);
    whole = the full spelling of the imported module (a.b.c) when `import a.b.c` (without as) bound the root name."""

    module: str
    level: int
    name: str | None = None
    whole: str | None = None

    @property
    def imported_module(self) -> "Imported":
        """The imported module itself (dropping the from-name; `import a.b.c` gives a.b.c), to look up whether and where it resolves."""
        return Imported(self.whole if self.whole is not None else self.module, self.level)


@dataclass(frozen=True)
class Statement:
    """The surface of one module-level statement: surface = ast.dump of the reduced statement (without positions); chains = the chains of names and attributes the surface reads; imports = name -> destination bound by imports inside the surface
    (including imports inside a function that kept its body: an index to look up the destination from a name); binds = name -> destination bound by this statement through import at module level
    (also an index)."""

    surface: str
    chains: tuple[tuple[str, ...], ...]
    imports: Mapping[str, tuple[Imported, ...]]
    binds: Mapping[str, tuple[Imported, ...]]


@dataclass(frozen=True)
class Facts:
    """The reading of one module file: statements = surfaces of the module-level statements; bound = name -> index of the statement that binds it (an index to look up a declaration from a name);
    defined = names bound by non-import statements; stars = targets of star imports; header = ast.dump of the statements that read or write __all__; exported = names of __all__
    (None when there is no __all__ or a statement not in a form pyright can read exists: the header is then put in each per-name section too); module_imports = name -> destination bound by
    module-level imports (an index); volatile = unreadable because of a syntax error (gets a different digest on every run)."""

    statements: tuple[Statement, ...]
    bound: Mapping[str, tuple[int, ...]]
    defined: frozenset[str]
    stars: tuple[Imported, ...]
    header: str
    exported: frozenset[str] | None
    module_imports: Mapping[str, tuple[Imported, ...]]
    volatile: bool = False


_VOLATILE = Facts((), {}, frozenset(), (), "", None, {}, volatile=True)


@dataclass(frozen=True)
class _AllStep:
    """One statement that writes __all__: resets = whether it assigns (discarding earlier names) or adds (+=, extend, append); names = the names in that statement."""

    resets: bool
    names: frozenset[str]


@dataclass(frozen=True)
class _Read:
    """The reading of one module-level statement (an import statement counts as one name): the surface, the bound names (names = including imports; defined = those of non-import statements),
    the targets of star imports, and for an __all__ statement the ast.dump put into the header."""

    statement: Statement
    names: frozenset[str]
    defined: frozenset[str]
    stars: tuple[Imported, ...]
    header: str | None


@dataclass(frozen=True)
class _Scan:
    """The reading from walking the tree once: chains = chains of names and attributes that are read (including inside string annotations); imports = name -> destination bound by imports inside the tree
    (an index); stars = targets of star imports; walrus = names bound by `:=`; mentions_all = whether it reads or writes __all__."""

    chains: tuple[tuple[str, ...], ...]
    imports: Mapping[str, tuple[Imported, ...]]
    stars: tuple[Imported, ...]
    walrus: frozenset[str]
    mentions_all: bool


def module_facts(text: str) -> Facts:
    """Pure: read the Python source of a module into a form from which the surface of each name's declaration can be looked up (volatile if unreadable)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _VOLATILE
    body = tree.body[1:] if tree.body and _is_docstring(tree.body[0]) else tree.body
    grouped = tuple((statement, tuple(_reads_of(statement))) for statement in body)
    reads = tuple(read for _, group in grouped for read in group)
    return Facts(
        tuple(read.statement for read in reads),
        _grouped((name, index) for index, read in enumerate(reads) for name in sorted(read.names)),
        frozenset(name for read in reads for name in read.defined),
        tuple(star for read in reads for star in read.stars),
        "\n".join(read.header for read in reads if read.header is not None),
        _exported(
            tuple(
                statement
                for statement, group in grouped
                if any(read.header is not None for read in group)
            )
        ),
        _grouped(
            (name, target)
            for read in reads
            for name, targets in read.statement.binds.items()
            for target in targets
        ),
    )


def _reads_of(statement: ast.stmt) -> Iterator[_Read]:
    """Read one module-level statement. A module-level import statement is split into per-name statements (adding one name to a statement that lists several does not change the digest of the
    other names' declarations). For other statements, collect the bound names by walking only statement levels (not entering expressions, so expressions in function bodies are not walked), then
    walk the tree reduced to the surface exactly once and collect the names read, imports, and `:=`."""
    if isinstance(statement, (ast.Import, ast.ImportFrom)) and all(
        a.name != "*" for a in statement.names
    ):
        for alias in statement.names:
            single: ast.Import | ast.ImportFrom = (
                ast.Import(names=[alias])
                if isinstance(statement, ast.Import)
                else ast.ImportFrom(module=statement.module, names=[alias], level=statement.level)
            )
            name, target = next(_import_targets(single))
            yield _Read(
                Statement(ast.dump(single), (), {}, {name: (target,)}),
                frozenset({name}),
                frozenset(),
                (),
                None,
            )
        return
    scope = tuple(_module_scope_statements(statement))
    binds = _grouped(
        (name, target)
        for inner in scope
        if isinstance(inner, (ast.Import, ast.ImportFrom))
        for name, target in _import_targets(inner)
    )
    stars = tuple(
        Imported(inner.module or "", inner.level)
        for inner in scope
        if isinstance(inner, ast.ImportFrom) and any(alias.name == "*" for alias in inner.names)
    )
    targets = frozenset(name for inner in scope for name in _statement_targets(inner, imports=True))
    defined_targets = frozenset(
        name for inner in scope for name in _statement_targets(inner, imports=False)
    )
    declared = frozenset(_global_names(scope)) | frozenset(_except_names(scope))
    reduced = _reduce(statement, method=False)  # the tree is used only by this reading, so reduce it in place
    scan = _scan(reduced)
    surface = ast.dump(reduced)
    yield _Read(
        Statement(surface, scan.chains, scan.imports, binds),
        targets | declared | scan.walrus,
        defined_targets | declared | scan.walrus,
        stars,
        surface if scan.mentions_all else None,
    )


def _exported(statements: Sequence[ast.stmt]) -> frozenset[str] | None:
    """Read the names of __all__ from the module-level statements that read or write __all__ (in order); each per-name section gets only whether that name is in __all__
    (adding another name to __all__ does not make users of other names be checked again). None if there is no __all__ or any statement is not in a form pyright can read."""
    steps = tuple(_all_step(statement) for statement in statements)
    if not steps or any(step is None for step in steps):
        return None
    names: frozenset[str] = frozenset()
    for step in steps:
        if step is not None:
            names = step.names if step.resets else names | step.names
    return names


def _all_step(statement: ast.stmt) -> _AllStep | None:
    """The reading of one statement that writes __all__ (None unless it is in a form pyright can read: assignment of a sequence of strings, annotated assignment, +=, extend, append)."""
    match statement:
        case ast.Assign(
            targets=[ast.Name(id="__all__")],
            value=ast.List(elts=elements) | ast.Tuple(elts=elements),
        ):
            resets = True
        case ast.AnnAssign(
            target=ast.Name(id="__all__"), value=ast.List(elts=elements) | ast.Tuple(elts=elements)
        ):
            resets = True
        case ast.AugAssign(
            target=ast.Name(id="__all__"),
            op=ast.Add(),
            value=ast.List(elts=elements) | ast.Tuple(elts=elements),
        ):
            resets = False
        case ast.Expr(
            value=ast.Call(
                func=ast.Attribute(value=ast.Name(id="__all__"), attr="extend"),
                args=[ast.List(elts=elements) | ast.Tuple(elts=elements)],
                keywords=[],
            )
        ):
            resets = False
        case ast.Expr(
            value=ast.Call(
                func=ast.Attribute(value=ast.Name(id="__all__"), attr="append"),
                args=[element],
                keywords=[],
            )
        ):
            elements, resets = [element], False
        case _:
            return None
    names = tuple(
        e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)
    )
    return _AllStep(resets, frozenset(names)) if len(names) == len(elements) else None


def _is_docstring(statement: ast.stmt) -> bool:
    """Whether the statement is a docstring (an expression statement of only a string constant), to drop descriptions from the surface."""
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _trivial(body: Sequence[ast.stmt]) -> bool:
    """Decide whether a body is empty (only `...`, `pass`, or a raise of NotImplementedError), on the body after dropping the docstring."""
    return all(_trivial_statement(statement) for statement in body)


def _trivial_statement(statement: ast.stmt) -> bool:
    """Whether it is one statement of an empty body (`...`, `pass`, or a raise of NotImplementedError)."""
    match statement:
        case ast.Pass():
            return True
        case ast.Expr(value=ast.Constant(value=value)):
            return value is Ellipsis
        case ast.Raise(
            exc=ast.Name(id="NotImplementedError")
            | ast.Call(func=ast.Name(id="NotImplementedError"))
        ):
            return True
        case _:
            return False


def _fully_annotated(function: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> bool:
    """Whether the arguments of a function (except a method's self / cls) and the result all have type declarations, to decide whether the body may be dropped from the surface."""
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    if method and positional and not _decorated_with(function, "staticmethod"):
        positional = positional[1:]
    starred = [a for a in (arguments.vararg, arguments.kwarg) if a is not None]
    return function.returns is not None and all(
        argument.annotation is not None
        for argument in (*positional, *arguments.kwonlyargs, *starred)
    )


def _decorated_with(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """Whether the function has a decorator named name (or whose attribute chain ends in name), so the first argument of a staticmethod is not taken as self."""
    return any(
        (isinstance(d, ast.Name) and d.id == name)
        or (isinstance(d, ast.Attribute) and d.attr == name)
        for d in function.decorator_list
    )


def _child_statements(statement: ast.stmt, into_scopes: bool) -> Iterator[ast.stmt]:
    """Statements nested directly inside a statement (bodies of if, try, for, while, with, match; with into_scopes also bodies of functions and classes). Does not enter expressions."""
    if not into_scopes and isinstance(
        statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ):
        return
    for field in ("body", "orelse", "finalbody"):
        inner = getattr(statement, field, None)
        if isinstance(inner, list):
            yield from (s for s in inner if isinstance(s, ast.stmt))
    handlers = getattr(statement, "handlers", None)  # Try, TryStar
    if isinstance(handlers, list):
        yield from (s for h in handlers if isinstance(h, ast.ExceptHandler) for s in h.body)
    if isinstance(statement, ast.Match):
        yield from (s for case in statement.cases for s in case.body)


def _statements_within(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]:
    """All statements inside a body (including bodies of nested functions and classes), to find global and assignments to attributes of self without walking expressions."""
    for statement in body:
        yield statement
        yield from _statements_within(tuple(_child_statements(statement, into_scopes=True)))


def _module_scope_statements(statement: ast.stmt) -> Iterator[ast.stmt]:
    """Module-level statements and all statements inside them that remain at module level (not entering function or class bodies), to find the statements that bind module names."""
    yield statement
    for inner in _child_statements(statement, into_scopes=False):
        yield from _module_scope_statements(inner)


def _global_names(scope: Iterable[ast.stmt]) -> Iterator[str]:
    """Names that a global inside module-level functions and classes (including nested ones) binds at module level."""
    for inner in scope:
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from (
                n
                for s in _statements_within(inner.body)
                if isinstance(s, ast.Global)
                for n in s.names
            )


def _except_names(scope: Iterable[ast.stmt]) -> Iterator[str]:
    """Names bound by `except ... as name` of a module-level try."""
    for inner in scope:
        handlers = getattr(inner, "handlers", None)
        if isinstance(handlers, list):
            yield from (h.name for h in handlers if isinstance(h, ast.ExceptHandler) and h.name)


def _assigns_attribute_of(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the method assigns to or deletes an attribute of its first argument (self / cls) (pyright infers the attribute type from there, so the body is part of the surface). Only the targets of assignment statements
    are looked at (the target of `:=` is only a name; a form with a self attribute as the target of a comprehension is not counted)."""
    positional = [*function.args.posonlyargs, *function.args.args]
    if not positional:
        return False
    owner = positional[0].arg
    return any(
        isinstance(inner, ast.Attribute)
        and isinstance(inner.value, ast.Name)
        and inner.value.id == owner
        for statement in _statements_within(function.body)
        for target in _assignment_targets(statement)
        for inner in ast.walk(target)
    )


def _assignment_targets(statement: ast.stmt) -> Iterator[ast.expr]:
    """The targets of the assignments and deletions of one statement."""
    match statement:
        case ast.Assign(targets=targets) | ast.Delete(targets=targets):
            yield from targets
        case (
            ast.AnnAssign(target=target)
            | ast.AugAssign(target=target)
            | ast.For(target=target)
            | ast.AsyncFor(target=target)
        ):
            yield target
        case ast.With(items=items) | ast.AsyncWith(items=items):
            yield from (item.optional_vars for item in items if item.optional_vars is not None)
        case _:
            pass


def _reduce(statement: ast.stmt, method: bool) -> ast.stmt:
    """Reduce a module-level statement to the surface that affects the caller's types (header comment "surface"): walk only statement levels, reduce functions, and for a class keep the whole and reduce
    its methods. The tree is used only by this reading, so it is replaced in place. method = whether it is a statement in a class body."""
    match statement:
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return _reduce_function(statement, method)
        case ast.ClassDef():
            statement.body = [_reduce(s, True) for s in _without_docstring(statement.body)]
            return statement
        case _:
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(statement, field, None)
                if isinstance(inner, list) and all(isinstance(s, ast.stmt) for s in inner):
                    setattr(statement, field, [_reduce(s, method) for s in inner])
            for handler in getattr(statement, "handlers", None) or ():
                if isinstance(handler, ast.ExceptHandler):
                    handler.body = [_reduce(s, method) for s in handler.body]
            if isinstance(statement, ast.Match):
                for case in statement.cases:
                    case.body = [_reduce(s, method) for s in case.body]
            return statement


def _reduce_function(function: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> ast.stmt:
    """A function that declares all types, has no global, and (if a method) does not assign to attributes of self / cls gets its body replaced by an empty body or a mark. Otherwise keep the body
    and drop only the leading docstring."""
    keeps_body = (
        not _fully_annotated(function, method)
        or any(isinstance(s, ast.Global) for s in _statements_within(function.body))
        or (method and _assigns_attribute_of(function))
    )
    if keeps_body:
        function.body = _without_docstring(function.body)
        return function
    stripped = [s for s in function.body if not _is_docstring(s)]
    if not stripped:
        function.body = [ast.Expr(value=ast.Constant(value=_DOCSTRING_MARK))]
    elif not _trivial(stripped):
        function.body = [ast.Expr(value=ast.Constant(value=_BODY_MARK))]
    else:
        function.body = stripped
    return function


def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """Drop the docstring at the head of a body (if it becomes empty, use pass: a body cannot be empty)."""
    if body and _is_docstring(body[0]):
        return body[1:] or [ast.Pass()]
    return body


def _scan(node: ast.AST) -> _Scan:
    """Walk the tree exactly once and collect the names and attribute chains that are read, imports, star imports, the names of `:=`, and whether __all__ appears."""
    nodes = tuple(ast.walk(node))
    inner = {
        id(n.value) for n in nodes if isinstance(n, ast.Attribute)
    }  # intermediate parts of a chain (a.b and a in a.b.c) are read by the outer chain
    return _Scan(
        tuple(dict.fromkeys(chain for n in nodes if id(n) not in inner for chain in _chains_at(n))),
        _grouped(
            (name, target)
            for n in nodes
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for name, target in _import_targets(n)
        ),
        tuple(
            Imported(n.module or "", n.level)
            for n in nodes
            if isinstance(n, ast.ImportFrom) and any(alias.name == "*" for alias in n.names)
        ),
        frozenset(
            name for n in nodes if isinstance(n, ast.NamedExpr) for name in _target_names(n.target)
        ),
        any(isinstance(n, ast.Name) and n.id == "__all__" for n in nodes),
    )


def _chains_at(node: ast.AST) -> Iterator[tuple[str, ...]]:
    """The name or attribute chain that a node reads (an attribute that is not a chain gives nothing). If a string constant can be read as an annotation expression, the names inside it too (pyright resolves a string
    annotation as an expression)."""
    match node:
        case ast.Attribute():
            chain = _chain(node)
            if chain is not None:
                yield chain
        case ast.Name(id=name):
            yield (name,)
        case ast.Constant(value=str(text)) if 0 < len(
            text
        ) <= _ANNOTATION_TEXT_LIMIT and _ANNOTATION_TEXT.fullmatch(text):
            yield from _text_chains(text)
        case _:
            pass


def _text_chains(text: str) -> Iterator[tuple[str, ...]]:
    """Names and attribute chains inside a string annotation. A string of only a name or a chain of names (most strings) is split by its spelling without being read as an expression."""
    parts = text.strip().split(".")
    if all(part.isidentifier() and not keyword.iskeyword(part) for part in parts):
        yield tuple(parts)
        return
    parsed = _expression(text)
    if parsed is not None:
        yield from chains_of(parsed)


def chains_of(node: ast.AST) -> Iterator[tuple[str, ...]]:
    """Collect the names and attribute chains read in a tree (`a.b.c` is one chain ('a', 'b', 'c'))."""
    yield from _scan(node).chains


def _expression(text: str) -> ast.AST | None:
    """Read a string as an expression (None if unreadable), to pick up names inside a string annotation."""
    try:
        return ast.parse(text.strip(), mode="eval")
    except (SyntaxError, ValueError):
        return None


def _chain(node: ast.expr) -> tuple[str, ...] | None:
    """The spelling of an attribute chain starting from a name (None for other expressions), to follow the names of imported modules."""
    match node:
        case ast.Name(id=name):
            return (name,)
        case ast.Attribute(value=value, attr=attr):
            base = _chain(value)
            return None if base is None else (*base, attr)
        case _:
            return None


def _import_targets(statement: ast.Import | ast.ImportFrom) -> Iterator[tuple[str, Imported]]:
    """The names and destinations bound by one import statement (star is not returned), to look up where a declaration is from a name."""
    match statement:
        case ast.Import(names=names):
            for alias in names:
                if alias.asname is not None:
                    yield alias.asname, Imported(alias.name, 0)
                else:
                    root = alias.name.split(".")[0]
                    yield root, Imported(root, 0, whole=alias.name)
        case ast.ImportFrom(module=module, level=level, names=names):
            for alias in names:
                if alias.name != "*":
                    yield alias.asname or alias.name, Imported(module or "", level, alias.name)


def _target_names(target: ast.AST) -> Iterator[str]:
    """The names bound by an assignment target (including inside tuple, list, and star; attributes and subscripts bind no names)."""
    match target:
        case ast.Name(id=name):
            yield name
        case ast.Tuple(elts=elements) | ast.List(elts=elements):
            for element in elements:
                yield from _target_names(element)
        case ast.Starred(value=value):
            yield from _target_names(value)
        case _:
            pass


def _statement_targets(inner: ast.stmt, imports: bool) -> Iterator[str]:
    """The names bound by the targets of one statement (names of definitions and imports, assignment targets, targets of for and with, match types, type aliases); imports = whether import names are counted too."""
    match inner:
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
            yield name
        case ast.Import() | ast.ImportFrom():
            if imports:
                yield from (name for name, _ in _import_targets(inner))
        case ast.Assign(targets=targets) | ast.Delete(targets=targets):
            yield from (n for t in targets for n in _target_names(t))
        case (
            ast.AnnAssign(target=target)
            | ast.AugAssign(target=target)
            | ast.For(target=target)
            | ast.AsyncFor(target=target)
        ):
            yield from _target_names(target)
        case ast.With(items=items) | ast.AsyncWith(items=items):
            yield from (
                n
                for item in items
                if item.optional_vars is not None
                for n in _target_names(item.optional_vars)
            )
        case ast.Match(cases=cases):
            yield from (
                n for case in cases for node in ast.walk(case.pattern) for n in _pattern_names(node)
            )
        case _:
            type_alias = getattr(ast, "TypeAlias", None)  # since 3.12
            if type_alias is not None and isinstance(inner, type_alias):
                named = vars(inner).get("name")  # the name of a type alias statement (ast.TypeAlias of 3.12)
                if isinstance(named, ast.AST):
                    yield from _target_names(named)


def _pattern_names(node: ast.AST) -> Iterator[str]:
    """The names bound by one part of a match pattern."""
    match node:
        case ast.MatchAs(name=str(name)) | ast.MatchStar(name=str(name)):
            yield name
        case ast.MatchMapping(rest=str(name)):
            yield name
        case _:
            pass


# --- digest of the inputs ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Node:
    """A dependency node: the declaration of one name of the module it resolves to (name = None means all names of the module: a reference that uses the module as a value, or a star import)."""

    resolution: Resolution
    name: str | None


@dataclass(frozen=True)
class _Expanded:
    """The digest of the contents of one node (local) and the nodes its surface uses (successors)."""

    local: str
    successors: tuple[_Node, ...]


#: One piece of the reading of a node's contents: either digest material (str) or a node the surface uses.
_Piece = str | _Node

#: Suffixes of module files and package __init__, and the kind of each candidate (in search order: pyright reads .pyi first).
_SOURCE_SUFFIXES = ((".pyi", "pyi"), (".py", "py"), (".hy", "hy"))
_INIT_FILES = (("__init__.pyi", "pyi"), ("__init__.py", "py"), ("__init__.hy", "hy"))


def _under(path: str, base: str) -> bool:
    """Whether path is the directory base or under it (decided on strings: pathlib operations are slow when called hundreds of thousands of times in one run)."""
    return path == base or path.startswith(base.rstrip("/") + "/")


def _join(base: str, *parts: str) -> str:
    """The spelling of a path under the directory base."""
    return "/".join((base.rstrip("/"), *parts)) if parts else base


class InputGraph:
    """Build the input digest per file (header comment "inputs"). root = the repo root; roots = import roots (the root and extraPaths); stub_dir = pyright's
    stubPath; environment = the environment fingerprint and search paths; staged = .hy under the root -> expanded Python (what pyright reads as `<name>.py`); failed =
    .hy whose expansion failed (invisible to pyright). Within one run, remember directory contents, readings, resolutions, and node digests (paths are strings internally)."""

    def __init__(
        self,
        root: Path,
        roots: Sequence[Path],
        stub_dir: Path,
        environment: Environment,
        staged: Mapping[Path, str],
        failed: Iterable[Path],
    ) -> None:
        self._root = str(root)
        self._environment = environment
        self._global_dirs = tuple(
            dict.fromkeys(str(d) for d in (*roots, stub_dir, *environment.searched))
        )
        self._staged = {str(path): text for path, text in staged.items()}
        self._failed = frozenset(str(path) for path in failed)
        self._mut_entries: dict[str, dict[str, bool]] = {}
        self._mut_natives: dict[str, dict[str, tuple[str, ...]]] = {}
        self._mut_facts: dict[str, Facts] = {}
        self._mut_in_dir: dict[tuple[str, str, bool], Resolution] = {}
        self._mut_resolved: dict[tuple[str, str, int], Resolution] = {}
        self._mut_local_dirs: dict[str, tuple[str, ...]] = {}
        self._mut_expanded: dict[_Node, _Expanded] = {}
        self._mut_deep: dict[_Node, str] = {}
        self._mut_markers: dict[str, str] = {}

    # resolution ------------------------------------------------------------------------------------------------------------

    def _entries(self, directory: str) -> dict[str, bool]:
        """Read the names of a directory's contents -> whether each is a directory (empty if not a directory), once per directory within one run."""
        known = self._mut_entries.get(directory)
        if known is not None:
            return known
        try:
            with os.scandir(directory) as scanned:
                listed = {entry.name: entry.is_dir() for entry in scanned}
        except OSError:
            listed = {}
        self._mut_entries[directory] = listed
        return listed

    def _natives(self, directory: str) -> dict[str, tuple[str, ...]]:
        """An index to look up native module files (`<name>.....so`, `.pyd`) in a directory by the head of the name (built once per directory within one run)."""
        known = self._mut_natives.get(directory)
        if known is None:
            entries = self._entries(directory)
            known = _grouped(
                (name.split(".", 1)[0], name)
                for name in sorted(entries)
                if entries[name] is False and name.endswith((".so", ".pyd")) and "." in name
            )
            self._mut_natives[directory] = known
        return known

    def _marker(self, path: str) -> str:
        """The digest of the contents of py.typed (look at the partial mark: it changes how a package's types are read)."""
        if path not in self._mut_markers:
            self._mut_markers[path] = _digest((_text_of(Path(path)),))
        return self._mut_markers[path]

    def _hy_kind(self, path: str) -> str:
        """The kind of a .hy under the root (expanded = hy; expansion failed = hy-failed; outside the expansion scope = hy-unstaged)."""
        if path in self._staged:
            return "hy"
        return "hy-failed" if path in self._failed else "hy-unstaged"

    def _sources_at(self, base: str, leaf: str, stub_package: bool) -> Iterator[Candidate]:
        """Module files in the directory base (leaf.pyi, .py, and .hy only under the root) and a native module of the same name."""
        entries = self._entries(base)
        in_root = _under(base, self._root)
        for suffix, kind in _SOURCE_SUFFIXES:
            name = leaf + suffix
            if (
                entries.get(name) is False
                and (kind == "pyi" or not stub_package)
                and (kind != "hy" or in_root)
            ):
                path = _join(base, name)
                yield Candidate(path, self._hy_kind(path) if kind == "hy" else kind)
        if not stub_package:
            yield from (
                Candidate(_join(base, name), "native") for name in self._natives(base).get(leaf, ())
            )

    def _package_files(self, here: str, stub_package: bool) -> Iterator[Candidate]:
        """The package directory here and its __init__ file (.hy too, only under the root)."""
        yield Candidate(here, "package")
        entries = self._entries(here)
        in_root = _under(here, self._root)
        for init, kind in _INIT_FILES:
            if (
                entries.get(init) is False
                and (kind == "pyi" or not stub_package)
                and (kind != "hy" or in_root)
            ):
                path = _join(here, init)
                yield Candidate(path, self._hy_kind(path) if kind == "hy" else kind)

    def _candidates(self, base: str, parts: Sequence[str], stubs: bool) -> Iterator[Candidate]:
        """Among the candidates pyright may hit for module (parts) under the directory base, the existing ones (including stub packages, py.typed, and parent package marks)."""
        entries = self._entries(base)
        for top in (f"{parts[0]}-stubs", parts[0]) if stubs else (parts[0],):
            if not (
                entries.get(top) is True
                or any(entries.get(top + suffix) is False for suffix, _ in _SOURCE_SUFFIXES)
                or top in self._natives(base)
            ):
                continue  # the directory base has no name with this head (most search paths end here)
            stub_package = top != parts[0]
            top_dir = _join(base, top)
            if self._entries(top_dir).get("py.typed") is False:
                marker = _join(top_dir, "py.typed")
                yield Candidate(marker, f"typed:{self._marker(marker)}")
            yield from (
                Candidate(_join(parent, init), "init")
                for parent in (_join(top_dir, *parts[1:i]) for i in range(1, len(parts)))
                for init, _ in _INIT_FILES
                if self._entries(parent).get(init) is False
            )
            here = _join(top_dir, *parts[1:])
            parent, leaf = here.rsplit("/", 1)
            yield from self._sources_at(parent, leaf, stub_package)
            if self._entries(parent).get(leaf) is True:
                yield from self._package_files(here, stub_package)

    def _in_dir(self, base: str, dotted: str, stubs: bool) -> Resolution:
        """Search the candidates of module (dotted) under the directory base, once within one run."""
        key = (base, dotted, stubs)
        known = self._mut_in_dir.get(key)
        if known is None:
            known = tuple(self._candidates(base, dotted.split("."), stubs))
            self._mut_in_dir[key] = known
        return known

    def _local_dirs(self, directory: str) -> tuple[str, ...]:
        """The parents from the directory of the importing file up to the search-path root (pyright also looks here for absolute imports that cannot be resolved on the search path: same idea as static_check's
        search_dirs. For a file outside the roots, up to the deepest search-path directory containing that file). Built once per directory."""
        known = self._mut_local_dirs.get(directory)
        if known is None:
            containing = [d for d in self._global_dirs if _under(directory, d)]
            stop = (
                self._root
                if _under(directory, self._root)
                else max(containing, key=len, default=directory)
            )
            known = tuple(_ancestors(directory, stop))
            self._mut_local_dirs[directory] = known
        return known

    def resolve(self, importer: str, target: Imported) -> Resolution:
        """Look up the resolution of the module of one import destination (a relative import starts from the importing file's directory). The resolution is decided only by the importing file's
        directory, the module spelling, and the depth, so search once per such combination within one run."""
        directory = os.path.dirname(importer)
        key = (directory, target.module, target.level)
        known = self._mut_resolved.get(key)
        if known is None:
            known = self._resolved(directory, target)
            self._mut_resolved[key] = known
        return known

    def _resolved(self, directory: str, target: Imported) -> Resolution:
        """The contents of resolve: search for the module's candidates in the search-path directories and in the parents from the importing file's directory."""
        if target.level > 0:
            base = directory
            for _ in range(target.level - 1):
                base = os.path.dirname(base)
            return (
                tuple(self._package_files(base, stub_package=False))
                if not target.module
                else self._in_dir(base, target.module, False)
            )
        if not target.module:
            return ()
        dirs = dict.fromkeys((*self._global_dirs, *self._local_dirs(directory)))
        return tuple(
            dict.fromkeys(c for d in dirs for c in self._in_dir(d, target.module, stubs=True))
        )

    def _submodule(self, resolution: Resolution, name: str) -> Resolution:
        """The resolution of a module (name) under a resolved package (under the package directory and the directory with __init__): where to look when a name is not in the module."""
        packages = dict.fromkeys(
            c.path if c.kind == "package" else os.path.dirname(c.path)
            for c in resolution
            if c.kind == "package"
            or (c.kind in ("pyi", "py", "hy") and os.path.basename(c.path).startswith("__init__."))
        )
        return tuple(dict.fromkeys(c for p in packages for c in self._in_dir(p, name, stubs=False)))

    def _spelled(self, resolution: Resolution) -> str:
        """Turn a resolution into a string for the digest material (the spelling of per-run places is replaced by marks: Places)."""
        return self._environment.places.spell(_resolution_json(resolution))

    # reading -----------------------------------------------------------------------------------------------------------------

    def _facts(self, path: str, kind: str) -> Facts:
        """Read one candidate file, once within one run (an expanded .hy reads the expanded Python; .py / .pyi read the file)."""
        known = self._mut_facts.get(path)
        if known is not None:
            return known
        if kind == "hy":
            text = self._staged.get(path)
        else:
            try:
                with open(path, encoding="utf-8") as opened:
                    text = opened.read()
            except (OSError, UnicodeDecodeError):
                text = None
        facts = _VOLATILE if text is None else module_facts(text)
        self._mut_facts[path] = facts
        return facts

    def _contents(self, resolution: Resolution) -> Iterator[tuple[str, Facts]]:
        """Among the resolution candidates, the files whose contents are read (.pyi, .py, expanded .hy) and their readings."""
        for candidate in resolution:
            if candidate.kind in ("pyi", "py", "hy"):
                yield candidate.path, self._facts(candidate.path, candidate.kind)

    # nodes -------------------------------------------------------------------------------------------------------------------

    def _imported_nodes(
        self, importer: str, targets: Sequence[Imported], chain: tuple[str, ...]
    ) -> Iterator[_Node]:
        """The nodes used by a chain headed by the name chain[0] bound by an import (targets = all the destinations of the imports that bound that name)."""
        prefixes = {
            ".".join(parts[:i])
            for target in targets
            if target.whole is not None
            for parts in (target.whole.split("."),)
            for i in range(1, len(parts) + 1)
        }
        for target in targets:
            if target.name is not None:  # from m import n [as x]
                yield _Node(
                    self.resolve(importer, Imported(target.module, target.level)), target.name
                )
            elif target.whole is not None:  # import a.b.c (binds the root name a)
                yield from self._walked_chain(importer, chain, prefixes)
            else:  # import a.b as x
                yield _Node(self.resolve(importer, target), chain[1] if len(chain) > 1 else None)

    def _walked_chain(
        self, importer: str, chain: tuple[str, ...], prefixes: set[str]
    ) -> Iterator[_Node]:
        """The nodes obtained by following a chain a.b.c.x from a bound by `import a.b.c` up to the spelling of the imported module (also same-name declarations of intermediate packages
        — a name in a package's __init__ hides the module below)."""
        depth = next(
            (i for i in range(1, len(chain)) if ".".join(chain[: i + 1]) not in prefixes),
            len(chain),
        )
        for i in range(1, depth):
            yield _Node(self.resolve(importer, Imported(".".join(chain[:i]), 0)), chain[i])
        module = self.resolve(importer, Imported(".".join(chain[:depth]), 0))
        yield _Node(module, chain[depth] if depth < len(chain) else None)

    def _statement_nodes(
        self,
        path: str,
        resolution: Resolution,
        facts: Facts,
        statement: Statement,
        name: str | None,
    ) -> Iterator[_Node]:
        """The nodes used by the surface of one module-level statement (the chains in the surface are resolved by imports inside the statement, module-level imports, module-level names, and star targets)
        and the destinations of the names that statement binds through import (name; None means all)."""
        for chain in statement.chains:
            head = chain[0]
            hits = (*statement.imports.get(head, ()), *facts.module_imports.get(head, ()))
            yield from self._imported_nodes(path, hits, chain)
            if head in facts.defined:
                yield _Node(resolution, head)
            elif not hits:
                yield from (_Node(self.resolve(path, star), head) for star in facts.stars)
        for bound, targets in statement.binds.items():
            if name is None or bound == name:
                yield from self._imported_nodes(path, targets, (bound,))

    def _pieces(self, node: _Node) -> Iterator[_Piece]:
        """The digest material of one node's contents and the nodes its surface uses."""
        yield json.dumps([node.name, self._spelled(node.resolution)], ensure_ascii=False)
        contents = tuple(self._contents(node.resolution))
        for path, facts in contents:
            if facts.volatile:
                yield f"volatile:{uuid.uuid4()}"
                continue
            if node.name is None or facts.exported is None:
                yield facts.header
            else:
                yield f"in __all__: {node.name in facts.exported}"
            indexes = (
                range(len(facts.statements))
                if node.name is None
                else facts.bound.get(node.name, ())
            )
            for index in indexes:
                statement = facts.statements[index]
                yield statement.surface
                yield from self._statement_nodes(path, node.resolution, facts, statement, node.name)
            if node.name is None:
                yield from (_Node(self.resolve(path, star), None) for star in facts.stars)
            elif not indexes:
                yield "no such name"
                yield from (_Node(self.resolve(path, star), node.name) for star in facts.stars)
                if "__getattr__" in facts.bound:
                    yield _Node(node.resolution, "__getattr__")
        if node.name is not None and not any(node.name in facts.bound for _, facts in contents):
            below = self._submodule(node.resolution, node.name)
            if below:
                yield self._spelled(below)
                yield _Node(below, None)

    def _expand(self, node: _Node) -> _Expanded:
        """Build the digest of one node's contents and the nodes its surface uses, once within one run."""
        known = self._mut_expanded.get(node)
        if known is not None:
            return known
        pieces = tuple(self._pieces(node))
        expanded = _Expanded(
            _digest(p for p in pieces if isinstance(p, str)),
            tuple(dict.fromkeys(p for p in pieces if isinstance(p, _Node))),
        )
        self._mut_expanded[node] = expanded
        return expanded

    def _deep(self, root: _Node) -> str:
        """Build the transitive digest of a node (the contents of the node and of all nodes reachable from it). Cycles are grouped per strongly connected component: Tarjan's algorithm, driven by hand so deep dependencies do not hit
        the recursion limit."""
        known = self._mut_deep.get(root)
        if known is not None:
            return known
        index: dict[_Node, int] = {}
        low: dict[_Node, int] = {}
        stack: list[_Node] = []
        on_stack: set[_Node] = set()
        work: list[tuple[_Node, Iterator[_Node]]] = []

        def visit(node: _Node) -> None:
            index[node] = low[node] = len(index)
            stack.append(node)
            on_stack.add(node)
            work.append((node, iter(self._expand(node).successors)))

        visit(root)
        while work:
            node, successors = work[-1]
            pending = self._next_unvisited(node, successors, index, low, on_stack)
            if pending is not None:
                visit(pending)
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                self._close(node, stack, on_stack)
        return self._mut_deep[root]

    def _next_unvisited(
        self,
        node: _Node,
        successors: Iterator[_Node],
        index: dict[_Node, int],
        low: dict[_Node, int],
        on_stack: set[_Node],
    ) -> _Node | None:
        """Advance a node's successors until reaching one not yet visited (skip visited or closed successors, and lower low by successors on the stack): one step of Tarjan's
        algorithm. There is one successor iterator per node, and on return it continues from where it left off (proportional to the number of edges)."""
        for successor in successors:
            if successor in self._mut_deep:
                continue
            if successor not in index:
                return successor
            if successor in on_stack:
                low[node] = min(low[node], index[successor])  # noqa: DOEFF007 - the Tarjan search updates its own work table
        return None

    def _close(self, head: _Node, stack: list[_Node], on_stack: set[_Node]) -> None:
        """Close one strongly connected component (from the stack head up) and give the same transitive digest to all nodes of the component."""
        start = len(stack) - 1 - stack[::-1].index(head)
        members = tuple(stack[start:])
        del stack[start:]  # noqa: DOEFF007 - the Tarjan search updates its own work table
        on_stack.difference_update(members)  # noqa: DOEFF007 - the Tarjan search updates its own work table
        inside = set(members)
        locals_ = sorted(self._expand(m).local for m in members)
        outside = sorted(
            {
                self._mut_deep[s]
                for m in members
                for s in self._expand(m).successors
                if s not in inside
            }
        )
        digest = _digest(("scc", *locals_, "→", *outside))
        self._mut_deep.update(dict.fromkeys(members, digest))

    def inputs_of(self, source: Path, own: str) -> str:
        """Build the input digest of one .hy under the root (own = the digest of the file itself: own_digest). If the expansion cannot be read, use a different digest on every run
        (checked every time). Walk the tree only once."""
        path = str(source)
        text = self._staged.get(path)
        if text is None:
            return f"unstaged:{uuid.uuid4()}"
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return f"volatile:{uuid.uuid4()}"
        scan = _scan(tree)
        imported = tuple(target for targets in scan.imports.values() for target in targets)
        used = (
            node
            for chain in scan.chains
            if chain[0] in scan.imports
            for node in self._imported_nodes(path, scan.imports[chain[0]], chain)
        )
        # All from-import names (including unused ones: whether a name exists in the module appears in that file's diagnostics) and star targets.
        named = (
            _Node(self.resolve(path, target.imported_module), target.name)
            for target in imported
            if target.name is not None
        )
        stars = (_Node(self.resolve(path, star), None) for star in scan.stars)
        # All resolutions of the modules to import (whether and where they resolve appears in that file's diagnostics).
        resolutions = sorted(
            {
                self._spelled(self.resolve(path, target.imported_module))
                for target in (*imported, *scan.stars)
            }
        )
        deep = sorted({self._deep(node) for node in dict.fromkeys((*used, *named, *stars))})
        return _digest(("target", self._environment.fingerprint, own, *resolutions, "→", *deep))


def _ancestors(directory: str, stop: str) -> Iterator[str]:
    """The parent directories from dir up to stop (inclusive), starting from dir itself (never going outside stop)."""
    current = directory
    while _under(current, stop):
        yield current
        parent = os.path.dirname(current)
        if parent == current:
            return
        current = parent


def own_digest(source_text: str, projection_text: str, spans: object) -> str:
    """Build the digest of one .hy itself: the source, the expanded Python, and the position map (a diagnostic position is mapped back to the .hy position through the expansion and the map)."""
    return _digest(("own", source_text, projection_text, repr(spans)))
