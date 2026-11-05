"""Install the 5 core packages into a fresh venv outside the repo, run the 5 README examples, and compare against their annotations (initiative doeff-publish).

Before release (wheels built locally) and after release (the PyPI version), one and the same command checks that "the README examples
pass in a fresh environment with the 5 core packages installed":

    uv run --no-project python tools/verify_release_install.py                  # wheels built from this checkout
    uv run --no-project python tools/verify_release_install.py --wheels <dir>   # existing wheels (e.g. CI build artifacts)
    uv run --no-project python tools/verify_release_install.py --pypi 0.5.0     # 0.5.0 from PyPI

For each Python version (default 3.11 and 3.14+gil -- uv also matches plain 3.14 to the free-threading 3.14t, so name the one with the GIL.
--python can be given any number of times and also accepts 3.14t):

1. Create a fresh venv in a temporary dir outside the repo (uv does not read the repo's [tool.uv] in pyproject.toml).
2. Install the 5 core packages. For wheels, install the 5 from the wheel dir with --no-index and no dependencies (so even if PyPI has the same name and version, the 5
   are always the local wheels), then install the other dependencies the 5 declare from PyPI. For PyPI, install the 5 with ==version. In both cases add the tool the README examples
   use (pytest), and confirm with `uv pip check` that the installed packages have no dependency conflicts.
3. Print the versions of the 5 installed packages.
4. Run the examples in the 5 installed READMEs -- the wheel metadata body (the README shown on the PyPI page) -- top to bottom in a fresh dir per README,
   and compare against the annotations (the contract is tools/readme_examples.py). The examples run in an environment without PYTHON*, DOEFF_* and VIRTUAL_ENV, with the Hy
   compile location also in a fresh dir (the same as a first-time user's machine; this machine's location is not used).

Exit code: 0 if everything passes, 1 if a README example or a dependency conflict fails, 2 if the tool cannot be used (build, venv or install failure).
Uses only the standard library (runs without syncing the workspace).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

# A module in the same dir -- when run as a script, Python puts the script's dir at the head of sys.path.
import readme_examples

REPO = Path(__file__).resolve().parents[1]
# The 5 core packages. This is also the order the READMEs are read in.
CORE = ("doeff", "doeff-vm", "doeff-core-effects", "doeff-hy", "doeff-hy-pytest")
# Packages whose wheel is built per Python version (a PyO3 extension -- not abi3).
NATIVE = "doeff-vm"
DEFAULT_PYTHONS = ("3.11", "3.14+gil")
# Tools the README examples use (not dependencies of the 5 core packages -- readers have them in their own project).
EXAMPLE_TOOLS = ("pytest",)
# Time limit per example (seconds). Includes the first Hy compile (a fresh location, so the .hy files of doeff_core_effects are built too).
EXAMPLE_TIMEOUT_SECONDS = 300
# A program run inside the installed venv that prints the state of what was installed as JSON.
# The README body is in the metadata payload up to 3.14, and in the Description field from 3.15 (the payload is empty).
_DESCRIBE = """
import importlib.metadata as metadata, json, sys, sysconfig
names = sys.argv[1:]

def readme(name):
    found = metadata.metadata(name)
    return found.get_payload() or found.get("Description", "")

print(json.dumps({
    "version": sys.version.split()[0],
    "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    "versions": {name: metadata.version(name) for name in names},
    "requires": [requirement for name in names for requirement in metadata.requires(name) or []],
    "readmes": {name: readme(name) for name in names},
}))
"""
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_EXTRA_MARKER = re.compile(r";.*\bextra\s*==")
_T = TypeVar("_T")


@dataclass(frozen=True)
class FromWheels:
    """Install from the wheel dir (wheels already built, or built from this checkout)."""

    directory: Path


@dataclass(frozen=True)
class FromPyPI:
    """Install the same version of the 5 packages from PyPI."""

    version: str


Source = FromWheels | FromPyPI


@dataclass(frozen=True)
class Installed:
    """The state of what was installed in the fresh venv (what the venv's interpreter answered as JSON, with types checked at the boundary)."""

    # the venv's interpreter
    python: Path
    # the interpreter version (e.g. 3.11.16)
    version: str
    # whether it is a free-threading build
    free_threaded: bool
    # the names of the 5 core packages -> installed version
    versions: dict[str, str]
    # the dependencies the 5 core packages declare (PEP 508 strings)
    requires: tuple[str, ...]
    # the names of the 5 core packages -> README (metadata body)
    readmes: dict[str, str]


@dataclass(frozen=True)
class ReadmeResult:
    """The outcome of running the examples of one README."""

    package: str
    # the number of examples run
    ran: int
    # the number of examples run that failed
    failed: int
    # a list of sentences saying what failed, fences outside the contract, and that there are no examples to run
    failures: tuple[str, ...]


@dataclass(frozen=True)
class PythonResult:
    """The outcome for one Python version."""

    request: str
    installed: Installed
    # the output when `uv pip check` failed (None if it passed)
    dependency_conflict: str | None
    readmes: tuple[ReadmeResult, ...]

    def passed(self) -> bool:
        """Whether this version has neither a dependency conflict nor a failing README example."""
        return self.dependency_conflict is None and not any(
            readme.failures for readme in self.readmes
        )


def normalize(name: str) -> str:
    """Normalize a distribution name to the same form as PyPI (PEP 503) -- to compare dependency names with the names of the 5 core packages."""
    return re.sub(r"[-_.]+", "-", name).lower()


def dependency_requirements(requirements: Iterable[str]) -> list[str]:
    """The dependencies the 5 core packages declare that are installed from PyPI (excluding the 5 core packages and extra dependencies).

    The 5 core packages are installed from local wheels, so naming them here would risk confusing them with the same name on PyPI. Extra dependencies are
    not used by the README examples (they are not installed by pip install doeff).
    """
    core = {normalize(name) for name in CORE}
    return sorted(
        {
            requirement
            for requirement in requirements
            if not _EXTRA_MARKER.search(requirement)
            and (found := _REQUIREMENT_NAME.match(requirement))
            and normalize(found.group()) not in core
        }
    )


def _uv(arguments: Sequence[str], cwd: Path) -> None:
    """Call uv once (on failure, CalledProcessError -- exit code 2 because the tool cannot be used)."""
    print(f"$ uv {' '.join(arguments)}", flush=True)
    subprocess.run(["uv", *arguments], cwd=cwd, check=True, text=True)


def build(wheels: Path, pythons: Sequence[str]) -> None:
    """Build the 5 core wheels of this checkout into wheels (doeff-vm per Python version)."""
    for package in CORE:
        if package == NATIVE:
            for python in pythons:
                _uv(
                    [
                        "build",
                        "--package",
                        package,
                        "--wheel",
                        "--python",
                        python,
                        "--out-dir",
                        str(wheels),
                    ],
                    REPO,
                )
        else:
            _uv(["build", "--package", package, "--wheel", "--out-dir", str(wheels)], REPO)


def _describe(python: Path, cwd: Path) -> Installed:
    """Ask the venv's interpreter for the installed versions, dependencies and READMEs of the 5 core packages (a JSON boundary -- field types are checked here)."""
    result = subprocess.run(
        [str(python), "-E", "-s", "-c", _DESCRIBE, *CORE],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    answer = json.loads(result.stdout)
    return Installed(
        python=python,
        version=_typed(answer["version"], str),
        free_threaded=_typed(answer["free_threaded"], bool),
        versions=_text_map(answer["versions"]),
        requires=tuple(_typed(item, str) for item in _typed(answer["requires"], list)),
        readmes=_text_map(answer["readmes"]),
    )


def _typed(value: object, kind: type[_T]) -> _T:
    """Check that the JSON value has the expected type (otherwise the venv's interpreter's answer is broken)."""
    if not isinstance(value, kind):
        raise TypeError(f"the venv interpreter's answer has a value that is not {kind.__name__}: {value!r}")
    return value


def _text_map(value: object) -> dict[str, str]:
    """Check and hold the JSON name -> string mapping."""
    return {_typed(key, str): _typed(item, str) for key, item in _typed(value, dict).items()}


def _refresh() -> list[str]:
    """Arguments to re-fetch instead of using the stale copies of the 5 core packages in uv's cache."""
    return [part for name in CORE for part in ("--refresh-package", name)]


def install(source: Source, python_request: str, place: Path) -> Installed:
    """Create a fresh venv at place and install the 5 core packages and the README example tools."""
    venv = place / "venv"
    _uv(["venv", "--no-project", "--python", python_request, str(venv)], place)
    python = venv / "bin" / "python"
    match source:
        case FromWheels(directory=directory):
            _uv(
                [
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--no-index",
                    "--find-links",
                    str(directory),
                    "--no-deps",
                    *_refresh(),
                    *CORE,
                ],
                place,
            )
            requires = _describe(python, place).requires
            _uv(
                [
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    *dependency_requirements(requires),
                    *EXAMPLE_TOOLS,
                ],
                place,
            )
        case FromPyPI(version=version):
            _uv(
                [
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    *_refresh(),
                    *(f"{name}=={version}" for name in CORE),
                    *EXAMPLE_TOOLS,
                ],
                place,
            )
    return _describe(python, place)


def dependency_conflict(installed: Installed, place: Path) -> str | None:
    """Read the installed packages' dependency conflicts with `uv pip check` (None if none) -- this also checks the mutual version constraints among the 5 core packages."""
    print(f"$ uv pip check --python {installed.python}", flush=True)
    result = subprocess.run(
        ["uv", "pip", "check", "--python", str(installed.python)],
        cwd=place,
        check=False,
        text=True,
        capture_output=True,
    )
    print(result.stdout + result.stderr, end="", flush=True)
    return None if result.returncode == 0 else result.stdout + result.stderr


def example_environment(place: Path) -> dict[str, str]:
    """The environment to run the README examples in -- closer to a first-time user's machine.

    Remove this machine's PYTHON*, DOEFF_* and VIRTUAL_ENV, and put the Hy compile location in a fresh dir (if this machine's location
    already had built output, the first compile could not be checked).
    """
    kept = {
        name: value
        for name, value in os.environ.items()  # noqa: DOEFF004 -- the one place where the tool hands the current environment to child processes
        if not name.startswith(("PYTHON", "DOEFF_")) and name != "VIRTUAL_ENV"
    }
    return {**kept, "DOEFF_HY_CODE_STORE": str(place / "hy-code-store")}


def _print_result(example: readme_examples.Example, failure: str | None) -> None:
    """Print the outcome of one run example on one line (if it failed, followed by the reason)."""
    print(f"    {'ok' if failure is None else 'NG'}  {example.name}", flush=True)
    if failure is not None:
        print("        " + failure.replace("\n", "\n        "), flush=True)


def check_readmes(installed: Installed, place: Path, timeout: float) -> tuple[ReadmeResult, ...]:
    """Run the examples of the 5 installed READMEs top to bottom, each README in a fresh dir."""
    environment = example_environment(place)

    def check(package: str) -> ReadmeResult:
        """Run one README and make it an outcome."""
        examples = readme_examples.fences(installed.readmes[package], f"README of {package}")
        directory = place / "examples" / package
        directory.mkdir(parents=True)
        print(f"  README of {package} {installed.versions[package]}", flush=True)
        problems = [problem for example in examples for problem in example.problems()]
        for problem in problems:
            print(f"    NG  {problem}", flush=True)
        ran = sum(1 for example in examples if example.runs() and not example.problems())
        # A README with no examples to run is not passed, since nothing was verified (e.g. when every example was made an excerpt).
        empty = () if ran else (f"README of {package} has no examples to run -- nothing was verified",)
        for missing in empty:
            print(f"    NG  {missing}", flush=True)
        failures = readme_examples.run_in_order(
            examples,
            directory,
            installed.python,
            timeout,
            environment=environment,
            on_result=_print_result,
        )
        return ReadmeResult(
            package=package,
            ran=ran,
            failed=len(failures) - len(problems),
            failures=(*failures, *empty),
        )

    return tuple(check(package) for package in CORE)


def check_python(source: Source, python_request: str, work: Path, timeout: float) -> PythonResult:
    """For one Python version, install into a fresh venv and run the README examples."""
    place = work / re.sub(r"[^A-Za-z0-9.]+", "-", f"py{python_request}")
    place.mkdir(parents=True)
    print(f"\n== Python {python_request}: install the 5 core packages into a fresh venv", flush=True)
    installed = install(source, python_request, place)
    build_kind = "free-threading" if installed.free_threaded else "GIL"
    print(f"  interpreter {installed.version}({build_kind}), {installed.python}", flush=True)
    print(
        "  installed versions: " + ", ".join(f"{name} {installed.versions[name]}" for name in CORE),
        flush=True,
    )
    conflict = dependency_conflict(installed, place)
    return PythonResult(
        request=python_request,
        installed=installed,
        dependency_conflict=conflict,
        readmes=check_readmes(installed, place, timeout),
    )


def _summary(results: Sequence[PythonResult]) -> str:
    """The table of outcomes by version x README (printed last, so you can see at a glance where it failed)."""

    def cell(readme: ReadmeResult) -> str:
        """The cell for one README -- passed / run (number failed, number of other failures)."""
        others = len(readme.failures) - readme.failed
        return (
            f"{readme.ran - readme.failed}/{readme.ran}"
            + (f"(failed {readme.failed})" if readme.failed else "")
            + (f"(plus {others} NG)" if others else "")
        )

    header = ["Python", *CORE, "dependency conflict"]
    rows = [
        [
            f"{result.request}({result.installed.version})",
            *(cell(readme) for readme in result.readmes),
            "none" if result.dependency_conflict is None else "present",
        ]
        for result in results
    ]
    return "\n".join(" | ".join(row) for row in (header, *rows))


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Read the arguments -- how to install (build from this checkout, existing wheels, or a PyPI version) and the Python versions."""
    parser = argparse.ArgumentParser(
        description="Install the 5 core packages into a fresh venv outside the repo, run the 5 README examples, and compare against their annotations.",
    )
    chosen = parser.add_mutually_exclusive_group()
    chosen.add_argument(
        "--wheels",
        type=Path,
        metavar="DIR",
        help="Install the wheels in DIR without building (default: build from this checkout)",
    )
    chosen.add_argument(
        "--pypi",
        metavar="VERSION",
        help="Install VERSION of the 5 core packages from PyPI (e.g. 0.5.0)",
    )
    parser.add_argument(
        "--python",
        action="append",
        dest="pythons",
        metavar="REQUEST",
        help=f"The Python request passed to uv (can be given any number of times; default {' and '.join(DEFAULT_PYTHONS)})",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        metavar="DIR",
        help="The dir to put wheels, venvs and examples in (outside the repo; empty or nonexistent -- default is a temporary dir)",
    )
    parser.add_argument("--keep", action="store_true", help="Do not delete the work dir when finished")
    parser.add_argument(
        "--timeout",
        type=float,
        default=EXAMPLE_TIMEOUT_SECONDS,
        help="Time limit per example (seconds; default %(default)s)",
    )
    return parser.parse_args(argv)


def _work_dir(requested: Path | None) -> Path:
    """Decide the work dir -- inside the repo, uv reads the repo's [tool.uv] and the environment would not be fresh, so refuse."""
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="doeff-release-check-"))
    work = requested.resolve()
    if work.is_relative_to(REPO):
        raise SystemExit(f"--work-dir {work} is inside the repo {REPO} -- pass a dir outside the repo")
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"--work-dir {work} is not empty -- pass an empty or nonexistent dir")
    work.mkdir(parents=True, exist_ok=True)
    return work


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point -- decide how to install, check each Python version, print the table and return the exit code."""
    arguments = _parse(argv)
    pythons = tuple(arguments.pythons or DEFAULT_PYTHONS)
    if shutil.which("uv") is None:
        print("uv is not on PATH -- install it following https://docs.astral.sh/uv/", file=sys.stderr)
        return 2
    work = _work_dir(arguments.work_dir)
    print(f"work dir: {work}", flush=True)
    try:
        if arguments.pypi is not None:
            source: Source = FromPyPI(arguments.pypi)
        elif arguments.wheels is not None:
            source = FromWheels(arguments.wheels.resolve())
        else:
            print(f"\n== Build the 5 core wheels from this checkout ({REPO})", flush=True)
            build(work / "wheels", pythons)
            source = FromWheels(work / "wheels")
        results = [check_python(source, python, work, arguments.timeout) for python in pythons]
    except subprocess.CalledProcessError as failed:
        print(
            f"\nbuild or install failed (did not get as far as the README examples; exit code {failed.returncode}): "
            f"{' '.join(failed.cmd)}",
            file=sys.stderr,
        )
        if failed.stderr:
            print(failed.stderr, file=sys.stderr)
        return 2
    finally:
        if arguments.keep:
            print(f"\nKept the work dir: {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print("\n== Outcome (examples passed / examples run)", flush=True)
    print(_summary(results), flush=True)
    passed = all(result.passed() for result in results)
    print("\nAll passed" if passed else "\nSome failed -- read the NG lines above", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
