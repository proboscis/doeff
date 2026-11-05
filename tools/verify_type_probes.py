"""In a fresh venv with only the 4 core packages installed, run the type probes (tests/type_probes/) and the README Python examples through pyright and mypy,
and check that the lines with errors match the probe marks (section 12 of the doeff-publish initiative).

The pre-release typing report (how far doeff's types hold up in a user's type check) ran 25 probes in a core-only venv, counted where they hold and where
there are gaps, and cut the gaps into cards. The development environment's type checks (tests/test_static_typing.py and others) read through the workspace's editable install and
the repo's pyright configuration, so they cannot notice a fixed gap coming back under the user's way of installing (wheels, no extras). This tool checks with the same
way of installing as the user and the tools' default discovery:

    uv run --no-project python tools/verify_type_probes.py                  # default 3.11 and 3.14+gil
    uv run --no-project python tools/verify_type_probes.py --python 3.12    # --python can be given any number of times

For each Python version:

1. Install only the 4 core packages into a fresh venv (install in tools/verify_core_install.py -- it also checks that what got installed is exactly the 4 core packages).
2. Run the probes through pyright (strict) and mypy --strict, and compare, line by line, whether an error appears against the probe's mark.
3. Run the Python examples of the doeff and doeff-vm READMEs (the wheel metadata body) through pyright (standard) and mypy (default) and check that there is not a single
   error -- the editor of someone who copied the README must not show red at the default strictness.

The pyright and mypy versions are pinned to the versions in uv.lock (a version bump changes how errors appear -- run this tool too for a change that bumps the uv.lock versions).

Probe marks (annotations at the end of a line -- a line without a mark means that neither tool reports an error):

    # expect-error              both tools report an error on this line
    # expect-error[pyright]     only pyright reports one (mypy has no equivalent rule). [mypy] has the same form
    # gap-N want-error          the wish is an error. Neither reports one now -- gap N of the typing report. With [tool], only that tool does not report one now
    # gap-N want-clean          the wish is no error. Both report one now. [tool] has the same form

Types are written with typing.assert_type (if the type differs, that line gets an error). `# pyright: <strictness>` and `# mypy: <settings>` at the head of a file are the
strictness for that file alone (e.g. probes of how an un-annotated README is written). On a gap-mark line, when the named tool behaves as wished, the mark fails -- the change that
fills the gap switches the mark to expect-error or no mark, bringing it under guard.

It is run by the CI workflow Type probes (.github/workflows/type-probes.yml).

Exit code: 0 if everything passes, 1 if a check fails, 2 if the tool cannot be used (venv, install or type-tool failure). Uses only the standard library.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import tomllib

# A module in the same dir -- when run as a script, Python puts the script's dir at the head of sys.path.
import verify_core_install

REPO = verify_core_install.REPO
PROBES = REPO / "tests" / "type_probes"
LOCK = REPO / "uv.lock"
PYRIGHT = "pyright"
MYPY = "mypy"
CHECKERS = (PYRIGHT, MYPY)
DEFAULT_PYTHONS = verify_core_install.DEFAULT_PYTHONS
# Time limit per child process (seconds). For pyright's first run it includes fetching node and pyright itself.
CHILD_TIMEOUT_SECONDS = 300
# The shape of a mark (after the #). An unreadable mark is failed rather than silently treated as "no mark" -- so that a misspelled mark does not create an unguarded line.
_MARK = re.compile(
    r"(?P<kind>expect-error|gap-(?P<gap>\d+) want-(?P<want>error|clean))(?:\[(?P<checkers>[^\]]*)\])?"
)
# Words that look like a mark (when _MARK does not match, it is failed as an unreadable mark).
_MARK_WORDS = re.compile(r"\b(?:expect-\w+|gap-\w+)")
_MYPY_ERROR = re.compile(r"^(?P<file>.+?\.pyi?):(?P<line>\d+):(?:\d+:)? error: (?P<message>.*)$")


@dataclass(frozen=True)
class Expectation:
    """The mark on one line of a probe -- the tools that report an error on this line now and, for a gap line, its number and the wish."""

    # the names of the tools that report an error on this line now
    erring: frozenset[str]
    # the number of the typing-report gap (None for a line that is not a gap)
    gap: int | None = None
    # the tools for which the gap is still open -- when such a tool behaves as wished, the gap is dropped as filled
    open_in: frozenset[str] = frozenset()


# The mark of a line without a mark -- neither tool reports an error.
CLEAN = Expectation(frozenset())


@dataclass(frozen=True)
class MarkedLine:
    """One marked line of a probe."""

    # the path from the checking dir
    file: str
    # the line, counted from 1
    line: int
    expectation: Expectation


@dataclass(frozen=True)
class Marks:
    """The marks of a probe -- the marked lines, and the sentences saying which marks were unreadable."""

    lines: tuple[MarkedLine, ...]
    problems: tuple[str, ...]


def _read_mark(comment: str, file: str, line: int) -> Marks:
    """Read the mark of one annotation (empty if there is no mark)."""
    where = f"{file}:{line}"
    found = _MARK.search(comment)
    if found is None:
        words = _MARK_WORDS.findall(comment)
        return Marks((), (f"{where}: unreadable mark: {' '.join(words)}",) if words else ())
    named = (
        frozenset(CHECKERS)
        if found["checkers"] is None
        else frozenset(name.strip() for name in found["checkers"].split(","))
    )
    if not named <= frozenset(CHECKERS):
        return Marks(
            (),
            (f"{where}: unknown name in the mark's [tool]: [{found['checkers']}] (allowed: {', '.join(CHECKERS)})",),
        )
    if found["gap"] is None:
        return Marks((MarkedLine(file, line, Expectation(named)),), ())
    # A named tool is now opposite to the wish. An unnamed tool still behaves as wished.
    erring = frozenset(CHECKERS) - named if found["want"] == "error" else named
    return Marks((MarkedLine(file, line, Expectation(erring, int(found["gap"]), named)),), ())


def read_marks(text: str, file: str) -> Marks:
    """Read the marks of each line from the probe's body (a # inside a string is not read)."""
    reads = tuple(
        _read_mark(token.string, file, token.start[0])
        for token in tokenize.generate_tokens(io.StringIO(text).readline)
        if token.type == tokenize.COMMENT
    )
    return Marks(
        tuple(marked for read in reads for marked in read.lines),
        tuple(problem for read in reads for problem in read.problems),
    )


def expectation_at(marks: Sequence[MarkedLine], file: str, line: int) -> Expectation:
    """The mark of one line of a file (CLEAN for a line without a mark)."""
    return next(
        (marked.expectation for marked in marks if (marked.file, marked.line) == (file, line)),
        CLEAN,
    )


@dataclass(frozen=True)
class Finding:
    """One error from a type tool."""

    checker: str
    # the path from the checking dir
    file: str
    # the line, counted from 1
    line: int
    message: str


@dataclass(frozen=True)
class Checked:
    """The outcome of one run of a type tool -- the errors, and the number of files the tool read (None if it could not be counted)."""

    findings: tuple[Finding, ...]
    files: int | None


def pyright_findings(report: object, directory: Path) -> Checked:
    """From pyright --outputjson's answer, take the errors (those with severity error) and the number of files read."""
    match report:
        case {"generalDiagnostics": list(diagnostics), "summary": {"filesAnalyzed": int(analyzed)}}:
            pass
        case _:
            raise TypeError(f"unexpected shape of the pyright answer: {report!r}")
    errors = (
        verify_core_install.pyright_finding(diagnostic)
        for diagnostic in diagnostics
        if isinstance(diagnostic, dict) and diagnostic.get("severity") == "error"
    )
    return Checked(
        tuple(
            Finding(
                PYRIGHT,
                Path(error.file).resolve().relative_to(directory.resolve()).as_posix(),
                error.line + 1,
                f"{error.message.splitlines()[0]}  [{error.rule}]",
            )
            for error in errors
        ),
        analyzed,
    )


def mypy_findings(output: str) -> Checked:
    """From mypy's output, take the error lines (excluding notes) and the number of files read."""
    checked = verify_core_install.MYPY_CHECKED.search(output)
    return Checked(
        tuple(
            Finding(MYPY, Path(found["file"]).as_posix(), int(found["line"]), found["message"])
            for found in map(_MYPY_ERROR.match, output.splitlines())
            if found is not None
        ),
        None if checked is None else int(checked[1]),
    )


@dataclass(frozen=True)
class Judgement:
    """The outcome of comparing errors against marks -- sentences saying which lines differ from the marks, and the number for each still-open gap line."""

    failures: tuple[str, ...]
    open_gaps: tuple[int, ...]


def judge(marks: Sequence[MarkedLine], findings: Sequence[Finding]) -> Judgement:
    """Compare errors against marks -- look at every marked line and every line with an error, per tool (a line without a mark is CLEAN)."""
    places = sorted(
        {*((finding.file, finding.line) for finding in findings), *((m.file, m.line) for m in marks)}
    )
    lines = tuple(
        _judge_line(
            f"{file}:{line}",
            expectation_at(marks, file, line),
            [finding for finding in findings if (finding.file, finding.line) == (file, line)],
        )
        for file, line in places
    )
    return Judgement(
        tuple(failure for judged in lines for failure in judged.failures),
        tuple(gap for judged in lines for gap in judged.open_gaps),
    )


def _judge_line(where: str, expectation: Expectation, findings: Sequence[Finding]) -> Judgement:
    """Compare one line against the marks, per tool. If a gap line is as marked, count it as an open gap."""
    failures = tuple(
        f"{where}: {failure}"
        for checker in CHECKERS
        if (
            failure := _line_failure(
                expectation,
                checker,
                [finding.message for finding in findings if finding.checker == checker],
            )
        )
        is not None
    )
    still_open = expectation.gap is not None and not failures
    return Judgement(failures, (expectation.gap,) if still_open and expectation.gap else ())


def _line_failure(expectation: Expectation, checker: str, messages: Sequence[str]) -> str | None:
    """A sentence saying that one tool's outcome on a line differs from the mark (None if it matches)."""
    errs = bool(messages)
    if errs == (checker in expectation.erring):
        return None
    said = f" — {checker} errors: {' / '.join(messages)}" if messages else ""
    if expectation.gap is not None and checker in expectation.open_in:
        return (
            f"type-report gap {expectation.gap} is now closed by {checker} — change the mark and put it under guard"
            f" (as hoped, {'it now errors' if errs else 'the error is gone'}){said}"
        )
    wanted = "reports an error" if not errs else "reports no error"
    return f"{checker} differs from the mark — the mark says \"{wanted}\"{said}"


@dataclass(frozen=True)
class Suite:
    """One set of files to run through the type tools."""

    # the name for printing
    name: str
    directory: Path
    # the path from the dir
    files: tuple[str, ...]
    # pyright's typeCheckingMode
    pyright_mode: str
    # mypy's strictness arguments (empty by default)
    mypy_arguments: tuple[str, ...]
    # the marked lines (for an unmarked line, neither tool reports an error)
    marks: tuple[MarkedLine, ...]


@dataclass(frozen=True)
class Versions:
    """The versions of the type tools (the uv.lock versions)."""

    pyright: str
    mypy: str


def locked_version(lock: str, name: str) -> str:
    """From the body of uv.lock, read the version of one distribution (fail if the version is not a single one)."""
    versions = {
        package["version"] for package in tomllib.loads(lock)["package"] if package["name"] == name
    }
    if len(versions) != 1:
        raise ValueError(f"the version of {name} in uv.lock is not a single one: {sorted(versions)}")
    return versions.pop()


def checker_command(name: str, version: str) -> tuple[str, ...]:
    """The command that starts a type tool with a pinned version -- uv tool run starts it outside the venv, and the core venv is pointed to by an argument."""
    return ("uv", "tool", "run", "--from", f"{name}=={version}", name)


def locked_versions(lock: str) -> Versions:
    """From the body of uv.lock, read the versions of the type tools."""
    return Versions(pyright=locked_version(lock, PYRIGHT), mypy=locked_version(lock, MYPY))


def run_suite(suite: Suite, python: Path, versions: Versions, timeout: float) -> tuple[Finding, ...]:
    """Run one set through pyright and mypy and return the errors (if a tool did not read all the files, fail as the tool being unusable)."""
    config = suite.directory / "pyrightconfig.json"
    config.write_text(json.dumps({"typeCheckingMode": suite.pyright_mode}), encoding="utf-8")
    pyright = verify_core_install.run_checker(
        [
            *checker_command(PYRIGHT, versions.pyright),
            "--project",
            str(config),
            "--pythonpath",
            str(python),
            "--outputjson",
            *suite.files,
        ],
        suite.directory,
        timeout,
    )
    mypy = verify_core_install.run_checker(
        [
            *checker_command(MYPY, versions.mypy),
            *suite.mypy_arguments,
            "--python-executable",
            str(python),
            "--follow-imports=silent",
            "--show-error-codes",
            "--no-color-output",
            "--cache-dir",
            str(suite.directory / ".mypy_cache"),
            *suite.files,
        ],
        suite.directory,
        timeout,
    )
    checked = (
        (PYRIGHT, pyright_findings(json.loads(pyright.stdout), suite.directory), pyright.stderr),
        (MYPY, mypy_findings(mypy.stdout), mypy.stdout),
    )
    unread = [
        subprocess.CalledProcessError(
            1, [checker], output, f"{checker} read {result.files} files, which differs from the {len(suite.files)} written"
        )
        for checker, result, output in checked
        if result.files != len(suite.files)
    ]
    if unread:
        raise unread[0]
    return tuple(finding for _, result, _ in checked for finding in result.findings)


def check_suite(suite: Suite, python: Path, versions: Versions, timeout: float) -> tuple[str, ...]:
    """Run one set through the type tools, compare against the marks, print the outcome, and return the list of sentences saying which lines differ from the marks."""
    findings = run_suite(suite, python, versions, timeout)
    judged = judge(suite.marks, findings)
    verify_core_install.print_result(
        f"{suite.name}(pyright {suite.pyright_mode}, mypy {' '.join(suite.mypy_arguments) or 'default'})"
        f" -- {len(suite.files)} files, {len(suite.marks)} marked lines, {len(findings)} errors",
        "\n".join(judged.failures) or None,
    )
    if judged.open_gaps:
        gaps = ", ".join(
            f"gap {gap}: {count} lines" for gap, count in sorted(Counter(judged.open_gaps).items())
        )
        print(f"        open gaps (as marked): {gaps}", flush=True)
    return judged.failures


def probe_marks() -> Marks:
    """All the marks of the probes in tests/type_probes."""
    reads = tuple(
        read_marks(probe.read_text(encoding="utf-8"), probe.name) for probe in probe_files()
    )
    return Marks(
        tuple(marked for read in reads for marked in read.lines),
        tuple(problem for read in reads for problem in read.problems),
    )


def probe_files() -> tuple[Path, ...]:
    """The probes in tests/type_probes (in name order)."""
    return tuple(sorted(PROBES.glob("*.py")))


def probe_suite(directory: Path, marks: Marks) -> Suite:
    """Copy the probes in tests/type_probes into the dir and return the strict set."""
    directory.mkdir(parents=True)
    probes = probe_files()
    for probe in probes:
        shutil.copyfile(probe, directory / probe.name)
    files = tuple(probe.name for probe in probes)
    return Suite("type probes", directory, files, "strict", ("--strict",), marks.lines)


def readme_suite(installed: verify_core_install.Installed, package: str, directory: Path) -> Suite:
    """Write the Python examples of one README (those that run on the core alone) into the dir and return the default-strictness set -- the examples have no marks (every line has no error)."""
    directory.mkdir(parents=True)
    paths = tuple(
        verify_core_install.write_example(example, directory)
        for example in verify_core_install.type_checked_examples(installed, package)
    )
    files = tuple(path.relative_to(directory).as_posix() for path in paths)
    return Suite(f"README examples of {package}", directory, files, "standard", (), ())


def check_python(
    python_request: str, work: Path, versions: Versions, timeout: float
) -> tuple[str, ...]:
    """For one Python version, install only the core into a fresh venv, run the probes and README examples through the type tools, and return the list of sentences saying what failed."""
    place = work / f"py{python_request.replace('+', '-')}"
    place.mkdir(parents=True)
    print(f"\n== Python {python_request}: install doeff without extras into a fresh venv", flush=True)
    # What is run through the type tools is the core built from this checkout (a check before release -- CI looks at the trees of main and of pull requests).
    source = verify_core_install.Checkout()
    installed = verify_core_install.install(python_request, place, source)
    build_kind = "free-threading" if installed.free_threaded else "GIL"
    print(f"  interpreter {installed.version}({build_kind}), {installed.python}", flush=True)
    problems = verify_core_install.install_problems(installed, source)
    verify_core_install.print_result(
        "what got installed is exactly the 4 core packages, and the 3 doeff ones are wheels built from this checkout",
        "\n".join(problems) or None,
    )
    marks = probe_marks()
    verify_core_install.print_result("probe marks are readable", "\n".join(marks.problems) or None)
    suites = (
        probe_suite(place / "probes", marks),
        *(
            readme_suite(installed, package, place / "readme" / package)
            for package in verify_core_install.CHECKED_READMES
        ),
    )
    return (
        *problems,
        *marks.problems,
        *(
            failure
            for suite in suites
            for failure in check_suite(suite, installed.python, versions, timeout)
        ),
    )


@dataclass(frozen=True)
class PythonResult:
    """The outcome for one Python version."""

    # the Python request passed to uv
    python: str
    failures: tuple[str, ...]


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Read the arguments -- the Python versions, and whether to keep the work dir."""
    parser = argparse.ArgumentParser(
        description=(
            "In a fresh venv with only the 4 core packages installed, run the type probes and the README Python examples through pyright and mypy, "
            "and check that the lines with errors match the probe marks."
        ),
    )
    parser.add_argument(
        "--python",
        action="append",
        dest="pythons",
        metavar="REQUEST",
        help=f"The Python request passed to uv (can be given any number of times; default {' and '.join(DEFAULT_PYTHONS)})",
    )
    parser.add_argument("--keep", action="store_true", help="Do not delete the work dir when finished")
    parser.add_argument(
        "--timeout",
        type=float,
        default=CHILD_TIMEOUT_SECONDS,
        help="Time limit per child process (seconds; default %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point -- check for each Python version, print the outcome and return the exit code."""
    verify_core_install.print_in_utf8()
    arguments = _parse(argv)
    pythons = tuple(arguments.pythons or DEFAULT_PYTHONS)
    if shutil.which("uv") is None:
        print("uv is not on PATH -- install it following https://docs.astral.sh/uv/", file=sys.stderr)
        return 2
    versions = locked_versions(LOCK.read_text(encoding="utf-8"))
    print(f"type tools: pyright {versions.pyright}, mypy {versions.mypy} (the uv.lock versions)", flush=True)
    # Outside the repo (so the type tools do not read the repo's pyproject.toml or pyrightconfig.json, and the examples' imports do not pick up the checkout's doeff).
    work = Path(tempfile.mkdtemp(prefix="doeff-type-probes-"))
    print(f"work dir: {work}", flush=True)
    try:
        results = tuple(
            PythonResult(python, check_python(python, work, versions, arguments.timeout))
            for python in pythons
        )
    except subprocess.CalledProcessError as failed:
        print(
            f"\nvenv, install or a type tool failed (did not get as far as the checks; exit code {failed.returncode}): "
            f"{' '.join(map(str, failed.cmd))}",
            file=sys.stderr,
        )
        for output in (failed.stdout, failed.stderr):
            if output:
                print(output, file=sys.stderr)
        return 2
    finally:
        if arguments.keep:
            print(f"\nKept the work dir: {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print("\n== Outcome", flush=True)
    for result in results:
        print(
            f"  Python {result.python}: "
            f"{'passed' if not result.failures else f'NG {len(result.failures)}'}",
            flush=True,
        )
    passed = not any(result.failures for result in results)
    print("\nAll passed" if passed else "\nSome failed -- read the NG lines above", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
