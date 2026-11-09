"""In a fresh venv holding only the four core distributions, check that the README Python examples, the trial command `python -m doeff demo` and the LLM agent example run, and that uses needing the Hy or
HTTP extras name the extra to add (milestone 8 of the doeff-publish effort).

`pip install doeff` installs only the four core distributions (doeff, doeff-vm, doeff-core-effects, frozendict); the Hy language is added by extra hy and the HTTP client
handlers by extra http. tests/test_core_install_extras.py reads the declarations in pyproject.toml, and tests/test_missing_extra_hints.py
blocks the Hy import in the development environment and reads the messages. The development environment has Hy, httpx and the test tools installed, so if the core-only install broke,
neither would notice. This tool checks with a real install:

    uv run --no-project python tools/verify_core_install.py                  # defaults: 3.11 and 3.14+gil
    uv run --no-project python tools/verify_core_install.py --python 3.13    # --python can be given repeatedly
    uv run --no-project python tools/verify_core_install.py --pypi latest    # latest on PyPI (the user's pip install doeff)
    uv run --no-project python tools/verify_core_install.py --pypi 0.6.0     # 0.6.0 on PyPI

For each Python version:

1. Create a fresh venv in a temporary dir outside the repo and install this checkout's doeff, doeff-vm and doeff-core-effects without extras (dependencies are resolved from
   each package's declaration, not uv.lock - the same as a user who installed from PyPI).
2. Check that the distributions in the venv are exactly the core four, and that the three doeff ones came from this checkout.
3. Run the Python examples of the READMEs of doeff and doeff-vm (the body of the wheel metadata - what the PyPI page shows) top to bottom in a fresh dir per README,
   and compare with the annotations (the contract is in tools/readme_examples.py). Hy fences and Python fences that load a Hy module (those that write `import hy`,
   or import a module written by a README Hy file= fence) are extra hy examples, so they are not run.
4. Run the uses that need the Hy or HTTP extra (EXTRA_USES) and check that they exit with 1 and that the last line of stderr ends with
   `pip install "doeff[<extra>]"`.
5. Run the README Python examples of step 3 through pyright (standard) and mypy --strict (launched by `uv tool run` outside the venv, pointing at the core venv),
   and check that no import is reported as a "non-public name" (reportPrivateImportUsage, attr-defined). Complaints about type
   annotations in the README examples themselves are not counted. A py.typed package treats only names re-exported with `X as X` or `__all__` as public,
   so even if the README writes `from doeff_core_effects import Ask`, a missing re-export turns the user's editor red.
   Also check that, in a core-only venv (no Hy, no httpx), the names at the package head exist as attributes and are bound by a star import.
6. Run the trial command `python -m doeff demo` that the README points to right after the install steps, and check that it exits with 0 and prints, in this order, the answers
   of running the same digest with four handlers - the canned answer, the effect line seen by the trace, the answer where budget(1) dropped k, and the answer where live_world
   read a page in the working dir.
7. Run the LLM agent example (examples/llm_agent.py) that the README and docs/index.md say runs with `pip install doeff` alone, in a dir holding copies of only the two
   example files, and check that it prints the answer of the fake model. Then add pytest to the venv and check that the test example
   (examples/test_llm_agent.py) passes in that dir. Both are run with OPENAI_ environment variables removed (no model key is read).
   Since pytest gets added, this goes last, after steps 2-6 that look at only the core four.
8. In a dir copied from the example dir (examples/testing_without_mocks) that the introductory article "Testing Python code without mocks" says runs with `pip install doeff`,
   check that each script prints the lines the article shows, in that order, with nothing on stderr, and that the dir's pytest passes.
   For fanout.py, also check that the three fetches ran concurrently (faster than the 0.6 seconds they would take one at a time). As in 7, pytest is added, so this goes
   last. Run only with --pypi: the example dir is written to stay on the PyPI version and is not rewritten
   until the value-shaped version appears on PyPI (ADR-DOE-API-002 R3), so running it on the checkout version would fail whenever the checkout API changes.

9. Of the examples of the article "Algebraic effects for Python", run monitor.py, which includes the article's production stack (`real` - real time and await_handler), and
   spend.py in the copied dir, and check that they exit with 0 and that monitor.py prints the production stack output (the `-- real: ...` heading and the answer) last.
   Run trading.py (virtual clock and wall clock layers), the example of the third article "Time", the same way and check the output of the last layer, and run
   its tests (test_trading.py) with the pytest that 8 added, in the same copied dir.
   The article says "every example runs with doeff 0.8.0 alone", so this also runs on the PyPI version (a version that does not match the
   await_handler call style fails with TypeError).

The per-Python-version checks in CI (job core-only-install of .github/workflows/python-compatibility.yml and
.github/workflows/core-install-macos-windows.yml) run 1-7 without --pypi.

--pypi installs doeff from PyPI instead of the checkout in step 1 (doeff-vm accepts only wheels - the user's machine has no Rust tools), checks in 2 that
the three doeff distributions came from PyPI, and checks the installed version's README in 3 and 5. After 3, it runs the Python examples of the READMEs of
doeff and doeff-vm in this checkout (on GitHub main in CI; what a first-time visitor reads on GitHub) in the venv of the installed version under the same contract - since the wheel
README was written at the same time as the installed version, step 3 stays green and misses a mismatch even when the GitHub README has moved ahead of the released version.
The wheel README and the GitHub README are counted separately as "of N examples in <set>, M pass on the installed version", and
the section title and a one-line reason for each failed example are printed. 4, 6 and 7 are not run - their expectations (extra messages, the trial command answer, example files) are the checkout's,
which can be newer than the released version. 8 is run - the article promises the examples run with today's `pip install doeff`, so if the example files
move ahead of the released version (use an API not yet released) it goes red. The daily CI (.github/workflows/core-install-from-pypi.yml) runs it on Linux,
macOS and Windows.

Exit code: 0 if everything passes, 1 if a check fails, 2 if the tool cannot be used (venv or install failure). Uses only the standard library.
"""

from __future__ import annotations

import argparse
import ast
import functools
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Modules in the same dir - when run as a script, Python puts the script's dir at the head of sys.path.
import readme_examples
import verify_release_install

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FromCheckout:
    """One doeff distribution installed from this checkout."""

    # Distribution name (PEP 503 form)
    name: str
    # Dir inside the repo
    directory: Path


FROM_CHECKOUT = (
    FromCheckout("doeff", Path(".")),
    FromCheckout("doeff-vm", Path("packages/doeff-vm")),
    FromCheckout("doeff-core-effects", Path("packages/doeff-core-effects")),
)
# Everything that `pip install doeff` installs.
CORE_FOUR = frozenset({*(package.name for package in FROM_CHECKOUT), "frozendict"})


@dataclass(frozen=True)
class Checkout:
    """Build and install the three doeff distributions of this checkout (a check before release)."""


@dataclass(frozen=True)
class PyPI:
    """Install doeff from PyPI (a check after release - the same as the `pip install doeff` a user types)."""

    # Named version (None is the latest on PyPI - what someone arriving today installs)
    version: str | None

    def requirement(self) -> str:
        """The requirement passed to uv pip install."""
        return "doeff" if self.version is None else f"doeff=={self.version}"


Source = Checkout | PyPI
# The value of --pypi that means the latest on PyPI rather than a version.
LATEST = "latest"
# The distribution that accepts only wheels when installing from PyPI (a PyO3 extension). GitHub runners have Rust tools, so even without a wheel for the machine it would be built from source and pass,
# hiding the failure on a user's machine without Rust.
WHEEL_ONLY = "doeff-vm"
# READMEs whose examples are run. The README of doeff-core-effects is excluded - one example (the section Swapping a production handler for a test handler)
# uses handlers written in Hy (os_file, memory_file), and the README also says that example needs extra hy.
CHECKED_READMES = ("doeff", "doeff-vm")
# Fence languages that do not run without an extra (the Hy language is installed by extra hy).
NEEDS_AN_EXTRA = frozenset({"hy"})
# The name of the Hy package itself that Python imports to load Hy modules (`import hy` installs the Hy import hook).
HY_PACKAGE = "hy"
DEFAULT_PYTHONS = ("3.11", "3.14+gil")
# Type-check tools. The venv being checked is kept to the core four only, so uv tool run launches them outside the venv and --pythonpath / --python-executable
# point at the core venv.
PYRIGHT = ("uv", "tool", "run", "pyright")
MYPY = ("uv", "tool", "run", "mypy")
# The pyright rules this check names - non-public names (names a py.typed package does not re-export) and failure to
# find the venv's packages (if they cannot be found, the check would go green without checking public names). Complaints about the README examples themselves (missing type annotations etc.) are
# outside this check.
PYRIGHT_RULES = frozenset({"reportPrivateImportUsage", "reportMissingImports"})
# The same markers for mypy (mypy reports by message and [code], not by rule).
MYPY_MARKERS = ("does not explicitly export attribute", "[import-not-found]")
# Sample names available from the package head in a core-only venv (the names the README and tutorial examples take) - checks they are bound without loading Hy modules.
HEAD_NAMES = ("Ask", "Get", "Put", "Tell", "reader", "state", "writer")
MYPY_CHECKED = re.compile(r"(?:\(checked|no issues found in) (\d+) source files?")
# Time limit (seconds) for one child process. There is no Hy compile, so each finishes in a few seconds.
CHILD_TIMEOUT_SECONDS = 120
# The suffix naming a similar module that a 3.15 traceback appends to the end of a ModuleNotFoundError line that carries a name.
_SUGGESTION_SUFFIX = re.compile(r"\. Did you mean: '[^']*'\?$")
# A program run inside the installed venv that prints the state of what was installed as JSON (direct_url.json is PEP 610 - where it was installed from).
# Up to 3.14 the README body is in the metadata payload; from 3.15 it is in the Description field (the payload is empty).
_DESCRIBE = """
import importlib.metadata as metadata, json, sys, sysconfig

def readme(name):
    found = metadata.metadata(name)
    return found.get_payload() or found.get("Description", "")

def source(distribution):
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    editable = direct.get("dir_info", {}).get("editable", False)
    return [distribution.metadata["Name"], distribution.version, direct.get("url", ""), editable]

print(json.dumps({
    "version": sys.version.split()[0],
    "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    "installed": [source(d) for d in metadata.distributions()],
    "readmes": [readme(name) for name in sys.argv[1:]],
}))
"""


@dataclass(frozen=True)
class WrittenFile:
    """One file placed in the dir where an extra-requiring use is run."""

    # Path from the dir
    path: str
    text: str


# The Hy program read by the CLI's --program hyprog.mod.answer_program.
HY_PROGRAM_FILES = (
    WrittenFile("hyprog/__init__.py", ""),
    WrittenFile("hyprog/mod.hy", "(import doeff [Pure])\n(setv answer-program (Pure 42))\n"),
)


@dataclass(frozen=True)
class ExtraUse:
    """One use that needs the Hy or HTTP extra - with only the core four, it fails with a message naming the extra to add."""

    # Printed name
    name: str
    # Command under the venv's bin (python or doeff)
    program: str
    # Arguments after the command
    arguments: tuple[str, ...]
    # The extra to add (the final `pip install "doeff[<extra>]"` of the message)
    extra: str


def _python_use(name: str, statement: str, extra: str) -> ExtraUse:
    """A use that runs one Python statement in an interpreter that puts neither the checkout nor the cwd on sys.path."""
    return ExtraUse(name, "python", ("-I", "-c", statement), extra)


# The table of messages and extras is doeff_core_effects/_extra_hints.py. The checks that read the same situations in the development environment are tests/test_missing_extra_hints.py and
# tests/cli/test_cli_without_hy.py (the case where Hy exists and only httpx is missing does not exist in a core-four-only venv).
EXTRA_USES = (
    _python_use("module written in Hy", "import doeff_core_effects._os_process", "hy"),
    _python_use("cache", "from doeff_core_effects._cache import cache", "hy"),
    _python_use(
        "HTTP handler", "from doeff_core_effects import http_production_handler", "http"
    ),
    _python_use(
        "HTTP handler module",
        "from doeff_core_effects._http_handlers import http_fixture_handler",
        "http",
    ),
    _python_use("HTTP effect", "from doeff_core_effects import HttpRequest", "http"),
    ExtraUse("doeff run --hy", "doeff", ("run", "--hy", "(import doeff [Pure]) (Pure 42)"), "hy"),
    ExtraUse(
        "doeff run .hy program",
        "doeff",
        ("run", "--program", "hyprog.mod.answer_program"),
        "hy",
    ),
    ExtraUse(
        "doeff run -c loading a Hy module",
        "doeff",
        ("run", "-c", "import doeff_core_effects._os_process\nPure(42)"),
        "hy",
    ),
)


# The trial command `python -m doeff demo` (doeff/cli/demo.py) that the README points to right after the install steps. The page live_world fetches is made a file in the working dir with --url
# - urlopen reads a file URL by the same path as HTTP, so the check does not touch the network.
DEMO_ARGUMENTS = ("-I", "-m", "doeff", "demo")
DEMO_PAGE = "<h1>Hello, live</h1>"
# The answer line of the handler that answers with fixed values (canned) - it does not read the page and states the character count of the fixed page <h1>Hello</h1>.
DEMO_CANNED_ANSWER = "'14 chars'"
# The line the trace prints on seeing the second effect, and the answer line budget(1) returns by dropping k at the second effect.
DEMO_TRACE_LINE = "trace: Summarize(text='<h1>Hello</h1>')"
DEMO_BUDGET_ANSWER = "'over budget at Summarize'"

# The LLM agent example that the README and docs/index.md say runs with `pip install doeff` alone, and the pytest test example that checks it.
# Paths from the repo root.
LLM_EXAMPLE_FILES = ("examples/llm_agent.py", "examples/test_llm_agent.py")
# The dir under the working dir where the two files are copied.
LLM_EXAMPLE_DIRECTORY = "llm-agent"
LLM_EXAMPLE_SCRIPT = "llm_agent.py"
LLM_EXAMPLE_TESTS = "test_llm_agent.py"
# The line the example prints with the fake model (canned_reply) answer.
LLM_EXAMPLE_ANSWER = "answer: No: an existing queue already does all of it, for little cost."

# The example dir of the introductory article "Testing Python code without mocks". The article shows the script output in fences and points to the remaining
# files (infra.py, test examples) by links to this dir. Path from the repo root.
ARTICLE_EXAMPLE_DIRECTORY = Path("examples/testing_without_mocks")


# A card-game fight (examples/effects_tour/spire.py - the central example of the article). Of the output of all layers with the seed fixed, the lines after the screen (layer 1)
# - from the outcome of layer 1 to the end. For the screen lines only the heading on line 1 is checked. It is deterministic, so if the answer moves when the version changes it goes red.
SPIRE_FILE = Path("examples/effects_tour/spire.py")
# The article's "Tests are one of the stacks" test, and the settings that make that dir the pytest root (run with pytest alongside spire.py).
SPIRE_TEST_FILES = (
    Path("examples/effects_tour/test_spire.py"),
    Path("examples/effects_tour/pytest.ini"),
)
SPIRE_FIRST_LINE = "1. a scripted player on seed 7, on screen"
SPIRE_LAST_LINES = (
    "  -> ('win', 6)",
    "2. a random bot on seed 3",
    "  -> ('win', 4)",
    "3. record that fight, then replay it with no dice and no player",
    "  -> ('win', 4) ('win', 4) same: True tape: 32 answers",
    "4. a Monte Carlo bot on seed 3",
    "  -> ('win', 4)",
    "5. 100 fights, one task each: random bot vs Monte Carlo bot",
    "  -> random bot wins 0.73",
    "  -> monte carlo wins 1.0",
    "6. damage by card over 100 random fights",
    "  -> {'Strike': 3663, 'Defend': 0, 'Bash': 632}",
)


@dataclass(frozen=True)
class ArticleScript:
    """How to run one article example script, and the output the article shows."""

    # File name in the dir
    name: str
    # All stdout lines (if timed, everything before the last seconds line)
    lines: tuple[str, ...]
    # Whether to print the seconds taken on the last line and check it ran concurrently (shorter than SEQUENTIAL_SECONDS)
    timed: bool = False
    # Value of DOEFF_SCHEDULER passed to the child (None means not passed - the installed version's default scheduler. The child environment drops DOEFF_*)
    scheduler: str | None = None

    def label(self) -> str:
        """The printed name (the form of the command a reader types)."""
        return (
            self.name
            if self.scheduler is None
            else f"{SCHEDULER_VARIABLE}={self.scheduler} {self.name}"
        )

    def environment(self, base: dict[str, str]) -> dict[str, str]:
        """The child environment - base plus the chosen scheduler."""
        return base if self.scheduler is None else {**base, SCHEDULER_VARIABLE: self.scheduler}


# The environment variable that selects the scheduler (the switch the README and article describe).
SCHEDULER_VARIABLE = "DOEFF_SCHEDULER"


_FANOUT_TITLES = (
    "['a.example', 'b.example', 'c.example']",
    "['A.EXAMPLE', 'B.EXAMPLE', 'C.EXAMPLE']",
)
# The same lines as the article fences (this table is the one place that compares the article example output). The article writes the fanout.py switch as
# DOEFF_SCHEDULER=rust, so it is run with that choice too.
ARTICLE_SCRIPTS = (
    ArticleScript(
        "production.py", ("SMTP -> grace@example.com: Welcome, Grace!", "Welcome, Grace!")
    ),
    ArticleScript(
        "trace.py",
        (
            "effect: LoadUser(user_id=1)",
            "effect: SendMail(to='grace@example.com', body='Welcome, Grace!')",
            "SMTP -> grace@example.com: Welcome, Grace!",
            "Welcome, Grace!",
        ),
    ),
    ArticleScript("retry.py", ("38.0",)),
    ArticleScript("record_replay.py", ("True 2 0",)),
    ArticleScript("fanout.py", _FANOUT_TITLES, timed=True),
    ArticleScript("fanout.py", _FANOUT_TITLES, timed=True, scheduler="rust"),
)
# The three fetches in fanout.py take 0.2 seconds each - run one at a time they take 0.6 seconds.
SEQUENTIAL_SECONDS = 0.6
ARTICLE_TIMED_LINE = re.compile(r"(\d+\.\d+) s")
# The last line of pytest -q (the form with no failures, skips or warnings).
PASSED_SUMMARY = re.compile(r"\d+ passed in \S+")


# The example dir of the article "Algebraic effects for Python", and the three run in a core-only venv (trading.py is the example of the third article, "Time").
TOUR_DIRECTORY = Path("examples/effects_tour")
# The heading line the script prints, and the last line (monitor.py's production stack = the article's Layer 5 `real`).
TOUR_SCRIPTS = {
    "spend.py": None,
    "monitor.py": ("-- real: wall clock, async client", "[[9], [15]]"),
    # Example of the third article "Time": the heading of the last three layers (virtual clock, wall clock, async exchange) and the last line (PnL with a fixed seed)
    "trading.py": ("8. live: the same strategy over an async exchange on the wall clock (60 bars)", "  -> pnl 6.96"),
}
# The tests run with pytest in the copied dir (the dir's pytest.ini makes it pytest's root). test_spire.py runs in its own dir (SPIRE_TEST_FILES).
TOUR_TEST_FILES = ("test_trading.py",)


@dataclass(frozen=True)
class Distribution:
    """One distribution installed in the venv."""

    # Distribution name (PEP 503 form)
    name: str
    # Version
    version: str
    # URL it was installed from (PEP 610 direct_url.json - "" for one installed from an index)
    url: str
    # Whether it was installed editable, reading a dir directly (reads the checkout, not the wheel contents)
    editable: bool


@dataclass(frozen=True)
class Installed:
    """The state of what was installed in the fresh venv (what the venv's interpreter answered in JSON, held after type-checking at the boundary)."""

    # The venv's interpreter
    python: Path
    # Interpreter version (e.g. 3.11.16)
    version: str
    # Whether it is a free-threading build
    free_threaded: bool
    # All installed distributions
    distributions: tuple[Distribution, ...]
    # READMEs (metadata bodies) - in the same order as CHECKED_READMES
    readmes: tuple[str, ...]

    def readme(self, package: str) -> str:
        """The README of one distribution of CHECKED_READMES."""
        return self.readmes[CHECKED_READMES.index(package)]

    def command(self, name: str) -> Path:
        """One command in the venv (doeff etc.) - in the same dir as the interpreter (Scripts on Windows, bin on other machines)."""
        return venv_command(self.python.parent.parent, name)


def venv_command(venv: Path, name: str) -> Path:
    """The path of one command in the venv (python, doeff etc.) - on Windows Scripts\\<name>.exe, on other machines bin/<name>."""
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def print_in_utf8() -> None:
    """Write the tool's output (which contains Japanese) as UTF-8 - the default for a Windows pipe (CI log) is the locale encoding (cp1252 etc.), which cannot write Japanese and fails.

    The child process environment is not changed: a child writes in the locale encoding like a user's machine, and subprocess text reading also uses the locale encoding.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def _uv(arguments: Sequence[str], cwd: Path) -> None:
    """Call uv once (on failure CalledProcessError - exit code 2 as the tool being unusable)."""
    print(f"$ uv {' '.join(arguments)}", flush=True)
    subprocess.run(["uv", *arguments], cwd=cwd, check=True, text=True)


def _texts(value: object) -> tuple[str, ...]:
    """Check that the JSON is a list of strings and hold it (otherwise the venv interpreter's answer is broken)."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"the venv interpreter's answer has a value that is not a list of strings: {value!r}")
    return tuple(value)


def _distribution(value: object) -> Distribution:
    """Hold the JSON [name, version, URL, editable] as one installed distribution (if the shape differs, the venv interpreter's answer is broken)."""
    match value:
        case [str(name), str(version), str(url), bool(editable)]:
            return Distribution(verify_release_install.normalize(name), version, url, editable)
        case _:
            raise TypeError(
                f"the venv interpreter's answer has a value that is not the shape of an installed distribution: {value!r}"
            )


def install_arguments(source: Source) -> list[str]:
    """The arguments of uv pip install - what to install and how (follows the venv specification)."""
    match source:
        case Checkout():
            # uv reuses a wheel built from a path until pyproject.toml changes, so make it rebuild from the current checkout.
            refresh = [
                part for package in FROM_CHECKOUT for part in ("--refresh-package", package.name)
            ]
            paths = [str(REPO / package.directory) for package in FROM_CHECKOUT]
            # --no-sources: if the components' [tool.uv.sources] workspace = true were read, uv would install the three as editable (a .pth reading the checkout
            # directly), which would not check the wheel contents. If not read, the three are installed as wheels built from the paths above.
            return ["--no-sources", *refresh, *paths]
        case PyPI():
            # --refresh: re-read the local cache index (if the previous latest remains in the cache, the version someone arriving today installs is not checked).
            return ["--refresh", "--only-binary", WHEEL_ONLY, source.requirement()]


def install(python_request: str, place: Path, source: Source) -> Installed:
    """Create a fresh venv at place and install source's doeff without extras."""
    venv = place / "venv"
    _uv(["venv", "--no-project", "--python", python_request, str(venv)], place)
    python = venv_command(venv, "python")
    _uv(["pip", "install", "--python", str(python), *install_arguments(source)], place)
    result = subprocess.run(
        [str(python), "-I", "-c", _DESCRIBE, *CHECKED_READMES],
        cwd=place,
        check=True,
        text=True,
        capture_output=True,
    )
    answer = json.loads(result.stdout)
    version, free_threaded = answer["version"], answer["free_threaded"]
    if not isinstance(version, str) or not isinstance(free_threaded, bool):
        raise TypeError(
            f"the venv interpreter's version answer is broken: {version!r}, {free_threaded!r}"
        )
    if not isinstance(answer["installed"], list):
        raise TypeError(f"the venv interpreter's answer for installed items is not a list: {answer['installed']!r}")
    return Installed(
        python=python,
        version=version,
        free_threaded=free_threaded,
        distributions=tuple(map(_distribution, answer["installed"])),
        readmes=_texts(answer["readmes"]),
    )


def install_problems(installed: Installed, source: Source) -> list[str]:
    """Messages saying the installed set is not the core four and that the three doeff ones were not installed from source (empty if all match)."""
    names = {distribution.name for distribution in installed.distributions}
    extra = sorted(names - CORE_FOUR)
    missing = sorted(CORE_FOUR - names)
    problems = (
        [
            f"installed distributions are not exactly the core four - extra: {extra}, missing: {missing}"
            " (put Hy and HTTP items in extras hy, http and all - tests/test_core_install_extras.py)"
        ]
        if extra or missing
        else []
    )
    return problems + [
        problem
        for package in FROM_CHECKOUT
        for distribution in installed.distributions
        if distribution.name == package.name
        and (problem := _source_problem(package, distribution, source)) is not None
    ]


def _source_problem(
    package: FromCheckout, distribution: Distribution, source: Source
) -> str | None:
    """A message saying one doeff distribution was not installed from source (None if it matches)."""
    where = (
        f"installed from: {distribution.url or 'index'}{'(editable)' if distribution.editable else ''}"
    )
    match source:
        case Checkout():
            if distribution.editable or not _is_directory(
                distribution.url, REPO / package.directory
            ):
                return f"{package.name} was not installed as a wheel built from this checkout ({REPO / package.directory}) - {where}"
            return None
        case PyPI(version=version):
            if distribution.url or distribution.editable:
                return f"{package.name} was not installed from PyPI - {where}"
            if version is not None and distribution.version != version:
                return f"{package.name} is not the named version {version} - installed version: {distribution.version}"
            return None


def source_name(source: Source) -> str:
    """The source of the three doeff distributions, written on the check line."""
    match source:
        case Checkout():
            return "wheels built from the checkout"
        case PyPI(version=None):
            return "latest on PyPI"
        case PyPI(version=version):
            return f"{version} on PyPI"


def _is_directory(url: str, directory: Path) -> bool:
    """Whether the file URL points at the directory."""
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "file" and (
        Path(urllib.request.url2pathname(parsed.path)).resolve() == directory.resolve()
    )


def print_result(name: str, failure: str | None) -> None:
    """Print the outcome of one check on one line (if it failed, followed by the reason)."""
    print(f"    {'ok' if failure is None else 'NG'}  {name}", flush=True)
    if failure is not None:
        print("        " + failure.replace("\n", "\n        "), flush=True)


@dataclass(frozen=True)
class Readme:
    """One README whose examples are run."""

    # Distribution name (one of CHECKED_READMES)
    package: str
    # Head of the fence name - the name by which a reader finds the README in the failure message (e.g. "README of doeff", "README.md")
    source: str
    text: str


@dataclass(frozen=True)
class ReadmeSet:
    """A set of READMEs whose examples are run - the body of the installed wheel's metadata, or the files of the checkout (GitHub main in CI)."""

    # Printed name (the head of "<name>: N examples...")
    title: str
    # Name of the dir under the working dir where the examples are run
    directory: str
    # Same order as CHECKED_READMES
    readmes: tuple[Readme, ...]


# Printed names of the README sets.
WHEEL_READMES = "README of the installed wheel"
GITHUB_READMES = "README on GitHub"
# The README file name of each distribution - what pyproject.toml's readme puts in the body of the wheel metadata (tests/test_verify_core_install.py).
README_FILE = "README.md"


def wheel_readmes(installed: Installed) -> ReadmeSet:
    """The metadata README of the installed wheel (what the PyPI page shows) - the README written at the same time as the installed version."""
    return ReadmeSet(
        WHEEL_READMES,
        "examples",
        tuple(
            Readme(package, f"README of {package}", installed.readme(package))
            for package in CHECKED_READMES
        ),
    )


def checkout_readmes(repo: Path = REPO) -> ReadmeSet:
    """The README of this checkout (GitHub main in CI - what a first-time visitor reads on GitHub).

    Run on a version installed from PyPI, it names when the README has moved ahead of the released version (describing uses not yet released) - the wheel
    README was written at the same time as the installed version, so it alone does not see the mismatch.
    """
    directories = {package.name: package.directory for package in FROM_CHECKOUT}
    paths = [(package, directories[package] / README_FILE) for package in CHECKED_READMES]
    return ReadmeSet(
        GITHUB_READMES,
        "github-examples",
        tuple(
            Readme(package, path.as_posix(), (repo / path).read_text(encoding="utf-8"))
            for package, path in paths
        ),
    )


@dataclass(frozen=True)
class ExampleResult:
    """The outcome of running one README example."""

    example: readme_examples.Example
    # Message saying it failed (None if it passed)
    failure: str | None


@dataclass(frozen=True)
class CheckedReadme:
    """The outcome of running the examples of one README."""

    # Outcome per example run
    results: tuple[ExampleResult, ...]
    # All messages saying something failed (the full text of failed examples, fences that do not match the contract, READMEs with no example to run)
    failures: tuple[str, ...]


@dataclass(frozen=True)
class FailedExample:
    """One README example that failed on the installed version."""

    # README and line (e.g. "README.md:42")
    where: str
    # Title of the section containing the fence
    section: str
    # One line giving the reason for the failure (an exception line etc.)
    error: str


@dataclass(frozen=True)
class ReadmeTally:
    """The tally of running the examples of one README set on the installed version."""

    title: str
    # Number of examples run
    ran: int
    failed: tuple[FailedExample, ...]
    # All messages saying something failed (the full text of failed examples, fences that do not match the contract, READMEs with no example to run)
    failures: tuple[str, ...]

    def summary(self) -> list[str]:
        """The tally line and, per failed example, the section title and a one-line reason."""
        return [
            f"{self.title}: of {self.ran} examples, {self.ran - len(self.failed)} pass on the installed version",
            *(
                f"  NG  section \"{failed.section}\" ({failed.where}): {failed.error}"
                for failed in self.failed
            ),
        ]


def error_line(failure: str) -> str:
    """The one-line reason from a failure message - the last non-empty line (for a failed example, the exception on the last stderr line; for an annotation mismatch, a one-line message)."""
    return next((line.strip() for line in reversed(failure.splitlines()) if line.strip()), "")


def check_readmes(
    readmes: ReadmeSet, installed: Installed, place: Path, timeout: float
) -> ReadmeTally:
    """Run the Python examples of a README set and return the tally after printing it - to see whether the examples a core-only user copies work."""
    environment = verify_release_install.example_environment(place)
    checked = [
        _check_readme(
            readme, installed, place / readmes.directory / readme.package, timeout, environment
        )
        for readme in readmes.readmes
    ]
    results = [result for readme in checked for result in readme.results]
    tally = ReadmeTally(
        readmes.title,
        ran=len(results),
        failed=tuple(
            FailedExample(
                f"{result.example.source}:{result.example.line}",
                result.example.section,
                error_line(result.failure),
            )
            for result in results
            if result.failure is not None
        ),
        failures=tuple(failure for readme in checked for failure in readme.failures),
    )
    for line in tally.summary():
        print(f"    {line}", flush=True)
    return tally


def _imported_modules(code: str) -> set[str]:
    """The set of first names of modules a Python fence imports (an unreadable fence gives empty - the failure is shown by running it)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    names = set[str]()
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=aliases):
                names.update(alias.name for alias in aliases)
            case ast.ImportFrom(module=str(module), level=0):
                names.add(module)
    return {name.split(".")[0] for name in names}


def hy_extra_examples(examples: Sequence[readme_examples.Example]) -> list[readme_examples.Example]:
    """The fences of one README that do not run without adding extra hy - Hy-language fences and Python fences that load a Hy module.

    Python can load .hy only after `import hy`, so neither a fence that writes `import hy` nor one that imports a module written by a Hy file= fence in the same README
    runs in a core-only venv without the Hy language.
    """
    hy_files = [example for example in examples if example.language in NEEDS_AN_EXTRA]
    reads_hy = {HY_PACKAGE, *(Path(example.file).stem for example in hy_files if example.file)}
    return [
        example
        for example in examples
        if example in hy_files
        or (
            example.language == "python"
            and not _imported_modules(example.code).isdisjoint(reads_hy)
        )
    ]


def _check_readme(
    readme: Readme,
    installed: Installed,
    directory: Path,
    timeout: float,
    environment: dict[str, str],
) -> CheckedReadme:
    """Run one README's examples (minus fences that need extra hy) top to bottom in a fresh dir and return the outcome."""
    fences = readme_examples.fences(readme.text, readme.source)
    set_aside = hy_extra_examples(fences)
    examples = [fence for fence in fences if fence not in set_aside]
    directory.mkdir(parents=True)
    print(f"  examples in {readme.source}", flush=True)
    # So fences skipped as extra-hy examples never drop out of the check silently, show their names.
    for fence in set_aside:
        print(f"    --  {fence.name} (extra hy example - not run)", flush=True)
    results: list[ExampleResult] = []

    def record(example: readme_examples.Example, failure: str | None) -> None:
        results.append(ExampleResult(example, failure))
        print_result(example.name, failure)

    failures = readme_examples.run_in_order(
        examples, directory, installed.python, timeout, environment=environment, on_result=record
    )
    # A README with no runnable examples verifies nothing, so it fails (e.g. when every example became an excerpt or Hy).
    empty = (
        []
        if any(example.runs() and not example.problems() for example in examples)
        else [f"{readme.source} has no example that runs with only the four core packages - nothing was verified"]
    )
    for missing in empty:
        print_result(readme.source, missing)
    return CheckedReadme(tuple(results), (*failures, *empty))


def type_checked_examples(installed: Installed, package: str) -> list[readme_examples.Example]:
    """Python fences of one README to type-check in the core-only venv (excluding extra-hy examples and excerpts that are not written out).

    Include fences that write a module via `file=` too - later examples import it, and without it they would report "not found".
    """
    fences = readme_examples.fences(installed.readme(package), f"README of {package}")
    set_aside = hy_extra_examples(fences)
    return [
        fence
        for fence in fences
        if fence not in set_aside and fence.language == "python" and fence.written_as() is not None
    ]


def pyright_failures(report: object, checked: int) -> list[str]:
    """Sentences naming the names pyright --outputjson reports as not public or packages not found (PYRIGHT_RULES), plus
    a sentence when the number of files pyright read falls short of the number written, checked (empty when they match)."""
    match report:
        case {"generalDiagnostics": list(diagnostics), "summary": {"filesAnalyzed": int(analyzed)}}:
            findings = tuple(map(pyright_finding, diagnostics))
        case _:
            raise TypeError(f"unexpected shape of the pyright output: {report!r}")
    named = [
        f"{Path(finding.file).name}:{finding.line + 1}: {finding.message.splitlines()[0]}  [{finding.rule}]"
        for finding in findings
        if finding.rule in PYRIGHT_RULES
    ]
    unread = (
        [
            f"pyright read {analyzed} files, which differs from the {checked} written - the type check did not read the examples"
        ]
        if analyzed != checked
        else []
    )
    return [*named, *unread]


@dataclass(frozen=True)
class PyrightFinding:
    """One pyright finding (one element of generalDiagnostics in --outputjson)."""

    # Rule name ("" for findings without a rule, such as syntax errors)
    rule: str
    message: str
    file: str
    # Zero-based line
    line: int


def pyright_finding(diagnostic: object) -> PyrightFinding:
    """Hold one JSON finding (a different shape means the pyright output is broken)."""
    match diagnostic:
        case {"message": str(message), "file": str(file), "range": {"start": {"line": int(line)}}}:
            rule = diagnostic.get("rule")
            return PyrightFinding(rule if isinstance(rule, str) else "", message, file, line)
        case _:
            raise TypeError(f"unexpected shape of the pyright finding: {diagnostic!r}")


def mypy_failures(output: str, checked: int) -> list[str]:
    """Lines of mypy output naming names that are not public or packages not found (MYPY_MARKERS), plus
    a sentence when the number of files mypy read falls short of the number written, checked (empty when they match)."""
    named = [line for line in output.splitlines() if any(mark in line for mark in MYPY_MARKERS)]
    found = MYPY_CHECKED.search(output)
    read = "could not be counted" if found is None else f"{found[1]} files"
    unread = (
        [
            f"mypy read {read}, which differs from the {checked} written - the type check did not read the examples"
        ]
        if found is None or int(found[1]) != checked
        else []
    )
    return [*named, *unread]


def run_checker(
    command: Sequence[str], directory: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run a type checker once. Exit code 1 (errors found) is normal; any other code is raised as the tool being unusable."""
    result = subprocess.run(
        command, cwd=directory, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            result.returncode, list(command), result.stdout, result.stderr
        )
    return result


def write_example(example: readme_examples.Example, directory: Path) -> Path:
    """Write one README fence to a file named after the fence under the dir (never pass a fence that is not written out)."""
    name = example.written_as()
    if name is None:
        raise ValueError(f"{example.name} is a fence that is not written to a file")
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(example.code, encoding="utf-8")
    return path


def _check_types(package: str, installed: Installed, directory: Path, timeout: float) -> list[str]:
    """Write one README's examples to the dir, run pyright (standard) and mypy --strict on them, and return sentences naming errors about non-public names."""
    paths = [
        write_example(example, directory) for example in type_checked_examples(installed, package)
    ]
    config = directory / "pyrightconfig.json"
    config.write_text(json.dumps({"typeCheckingMode": "standard"}), encoding="utf-8")
    pyright = run_checker(
        [
            *PYRIGHT,
            "--project",
            str(config),
            "--pythonpath",
            str(installed.python),
            "--outputjson",
            *map(str, paths),
        ],
        directory,
        timeout,
    )
    mypy = run_checker(
        [
            *MYPY,
            "--strict",
            "--python-executable",
            str(installed.python),
            "--follow-imports=silent",
            "--cache-dir",
            str(directory / ".mypy_cache"),
            *map(str, paths),
        ],
        directory,
        timeout,
    )
    results = (
        ("pyright(standard)", pyright_failures(json.loads(pyright.stdout), len(paths))),
        ("mypy --strict", mypy_failures(mypy.stdout, len(paths))),
    )
    for checker, found in results:
        print_result(
            f"{checker} reads the imports in the README examples of {package} as public names",
            "\n".join(found) or None,
        )
    return [failure for _, found in results for failure in found]


def check_head_names(installed: Installed, timeout: float) -> str | None:
    """In the core-only venv (no Hy, no httpx), check that the package's head names exist as attributes and are bound by star import too,
    and return a sentence naming what is missing (None when they match) - that names published to the type check are bound without loading Hy modules."""
    statement = (
        "import doeff_core_effects as package\n"
        "namespace = {}\n"
        "exec('from doeff_core_effects import *', namespace)\n"
        f"names = {HEAD_NAMES!r}\n"
        "missing = [n for n in names if not hasattr(package, n) or n not in namespace]\n"
        "assert not missing, missing\n"
    )
    try:
        result = subprocess.run(
            [str(installed.python), "-I", "-c", statement],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        failure = f"the package head-name check did not finish within {timeout} seconds"
    else:
        failure = (
            None
            if result.returncode == 0
            else f"in the core-only venv, the package head names {HEAD_NAMES} are not all present\n{result.stderr}"
        )
    print_result("in the core-only venv, the package head names exist as attributes and via star import", failure)
    return failure


def check_public_names(installed: Installed, place: Path, timeout: float) -> list[str]:
    """Check that the type checkers (pyright, mypy --strict) read the names CHECKED_READMES examples take from the package head as public,
    and that those names are bound in the core-only venv; return sentences naming what failed - so that the editor of a user who copies the README shows no non-public names."""
    print("  type checkers read the README example imports as public names", flush=True)
    typed = place / "typed"
    failures = [
        failure
        for package in CHECKED_READMES
        for failure in _check_types(package, installed, typed / package, timeout)
    ]
    head = check_head_names(installed, timeout)
    return [*failures, *([head] if head is not None else [])]


def extra_failure(use: ExtraUse, result: subprocess.CompletedProcess[str]) -> str | None:
    """Sentence saying that an extra-requiring use did not end by returning 1 with a sentence naming the extra to add (None when it did)."""
    line = f'pip install "doeff[{use.extra}]"'
    last = (result.stderr.strip().splitlines() or [""])[-1]
    # From 3.15 the traceback appends a similar module name to the end of the named ModuleNotFoundError line (`. Did you mean: 'doeff_vm'?`) -
    # it follows the MissingExtraError sentence, so strip that suffix before comparing.
    last = _SUGGESTION_SUFFIX.sub("", last)
    if result.returncode == 1 and last.endswith(line):
        return None
    return (
        f"{use.name} did not fail with exit code 1 and last line {line}"
        f"(exit code {result.returncode})\n{result.stdout}{result.stderr}"
    )


def check_extra_uses(installed: Installed, place: Path, timeout: float) -> list[str]:
    """Run EXTRA_USES and return sentences naming the ones that did not name the extra to add."""
    directory = place / "extra-uses"
    for written in HY_PROGRAM_FILES:
        (directory / written.path).parent.mkdir(parents=True, exist_ok=True)
        (directory / written.path).write_text(written.text, encoding="utf-8")
    environment = {
        **verify_release_install.example_environment(place),
        "PYTHONPATH": str(directory),
    }
    print("  uses that need the Hy and HTTP extras", flush=True)
    return [
        failure
        for use in EXTRA_USES
        if (failure := _run_extra_use(use, installed, directory, environment, timeout)) is not None
    ]


def _run_extra_use(
    use: ExtraUse,
    installed: Installed,
    directory: Path,
    environment: dict[str, str],
    timeout: float,
) -> str | None:
    """Run one use with the venv's command, print the outcome, and return a sentence saying it did not name the extra to add (None if it did)."""
    try:
        result = subprocess.run(
            [str(installed.command(use.program)), *use.arguments],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        failure = extra_failure(use, result)
    except subprocess.TimeoutExpired:
        failure = f"{use.name} did not finish within {timeout} seconds"
    print_result(use.name, failure)
    return failure


def demo_failure(result: subprocess.CompletedProcess[str]) -> str | None:
    """Sentence saying the demo command's outcome did not end with 0 and print the four handlers' answer lines in that order (None when it did)."""
    expected = (
        DEMO_CANNED_ANSWER,
        DEMO_TRACE_LINE,
        DEMO_BUDGET_ANSWER,
        repr(f"{len(DEMO_PAGE)} chars"),
    )
    # Read the line iterator forward and look for each expected line after the previous one (other lines may sit between).
    printed = iter(result.stdout.splitlines())
    if result.returncode == 0 and all(line in printed for line in expected):
        return None
    return (
        f"python -m doeff demo did not exit with code 0 and print the four handlers' answer lines {' → '.join(expected)} in that order"
        f"(exit code {result.returncode})\n{result.stdout}{result.stderr}"
    )


def check_demo(installed: Installed, place: Path, timeout: float) -> list[str]:
    """Run the demo command in the core-only venv and return sentences naming what failed (empty when it passes) - to see whether the command typed right after `pip install doeff` works."""
    directory = place / "demo"
    directory.mkdir()
    page = directory / "page.html"
    page.write_text(DEMO_PAGE, encoding="utf-8")
    print("  demo command (the one shown right after the README install steps)", flush=True)
    try:
        result = subprocess.run(
            [str(installed.python), *DEMO_ARGUMENTS, "--url", page.as_uri()],
            cwd=directory,
            env=verify_release_install.example_environment(place),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        failure = f"python -m doeff demo did not finish within {timeout} seconds"
    else:
        failure = demo_failure(result)
    print_result(
        "python -m doeff demo prints the answers of running the same digest through the four handlers, in that order",
        failure,
    )
    return [] if failure is None else [failure]


def llm_example_failure(result: subprocess.CompletedProcess[str]) -> str | None:
    """Sentence saying the LLM agent example's outcome did not end with 0 and print the fake model's answer line (None when it did)."""
    if result.returncode == 0 and LLM_EXAMPLE_ANSWER in result.stdout.splitlines():
        return None
    return (
        f"{LLM_EXAMPLE_SCRIPT} did not exit with code 0 and print the answer line {LLM_EXAMPLE_ANSWER!r} "
        f"(exit code {result.returncode})\n{result.stdout}{result.stderr}"
    )


def llm_tests_failure(result: subprocess.CompletedProcess[str]) -> str | None:
    """Sentence saying the test example's pytest outcome did not end with 0 and a summary line starting with passed (None when it did)."""
    lines = result.stdout.splitlines()
    if result.returncode == 0 and lines and re.match(r"\d+ passed\b", lines[-1]):
        return None
    return (
        f"pytest {LLM_EXAMPLE_TESTS} did not pass with exit code 0 (exit code {result.returncode})\n"
        f"{result.stdout}{result.stderr}"
    )


def _llm_example_run_failure(
    installed: Installed,
    place: Path,
    arguments: Sequence[str],
    judge: Callable[[subprocess.CompletedProcess[str]], str | None],
    timeout: float,
) -> str | None:
    """Run the venv's python once in the LLM agent example's dir and return the reason the judge says it failed, or a timeout reason (None when it passes).

    Strip OPENAI_ environment variables to see that it does not read a model key.
    """
    environment = {
        name: value
        for name, value in verify_release_install.example_environment(place).items()
        if not name.startswith("OPENAI_")
    }
    try:
        result = subprocess.run(
            [str(installed.python), "-I", *arguments],
            cwd=place / LLM_EXAMPLE_DIRECTORY,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"python {' '.join(arguments)} did not finish within {timeout} seconds"
    return judge(result)


def check_llm_example(installed: Installed, place: Path, timeout: float) -> list[str]:
    """Run the LLM agent example in the core-only venv, add pytest and run the test example, and return sentences naming what failed (empty when it passes).

    Like a user, run in a dir holding only the example's two files. This adds pytest to the venv, so call it after the check that looks at only the four core packages.
    """
    directory = place / LLM_EXAMPLE_DIRECTORY
    directory.mkdir()
    for name in LLM_EXAMPLE_FILES:
        shutil.copy(REPO / name, directory / Path(name).name)
    print(
        "  LLM agent example (what the README and docs/index.md say runs with just pip install doeff)",
        flush=True,
    )
    script_failure = _llm_example_run_failure(
        installed, place, [LLM_EXAMPLE_SCRIPT], llm_example_failure, timeout
    )
    print_result(f"{LLM_EXAMPLE_SCRIPT} prints the fake model's answer", script_failure)
    _uv(["pip", "install", "--python", str(installed.python), "pytest"], place)
    tests_failure = _llm_example_run_failure(
        installed, place, ["-m", "pytest", "-q", LLM_EXAMPLE_TESTS], llm_tests_failure, timeout
    )
    print_result(f"pytest {LLM_EXAMPLE_TESTS} passes with only the four core packages and pytest", tests_failure)
    return [failure for failure in (script_failure, tests_failure) if failure is not None]


def article_script_failure(
    script: ArticleScript, result: subprocess.CompletedProcess[str]
) -> str | None:
    """Sentence saying one run of an article example script did not exit 0 with empty stderr and print the lines the article shows, in that order (None when it did).

    For a timed script, also check that the last line's elapsed seconds is shorter than SEQUENTIAL_SECONDS.
    """
    printed = result.stdout.splitlines()
    shown = printed[:-1] if script.timed and printed else printed
    timed = ARTICLE_TIMED_LINE.fullmatch(printed[-1]) if script.timed and printed else None
    if result.returncode != 0 or result.stderr:
        problem = f"did not exit with code 0 with nothing on stderr (exit code {result.returncode})"
    elif shown != list(script.lines):
        problem = f"did not print the lines the article shows {list(script.lines)!r} in that order"
    elif script.timed and timed is None:
        problem = "did not print the elapsed seconds on the last line (in the form `0.20 s`)"
    elif timed is not None and float(timed.group(1)) >= SEQUENTIAL_SECONDS:
        problem = f"the fetches did not run concurrently ({timed.group(1)} seconds - {SEQUENTIAL_SECONDS} if one at a time)"
    else:
        return None
    return f"{script.label()} {problem}\n{result.stdout}{result.stderr}"


def article_tests_failure(result: subprocess.CompletedProcess[str]) -> str | None:
    """Sentence saying the pytest outcome in the article example dir did not end with 0 and a last line that is a passed summary with no failures, skips, or warnings (None when it did)."""
    lines = result.stdout.splitlines()
    if result.returncode == 0 and lines and PASSED_SUMMARY.fullmatch(lines[-1]):
        return None
    return (
        f"pytest in {ARTICLE_EXAMPLE_DIRECTORY} did not pass everything with exit code 0 (exit code {result.returncode})\n"
        f"{result.stdout}{result.stderr}"
    )


def example_tests_failure(test_file: Path, result: subprocess.CompletedProcess[str]) -> str | None:
    """Sentence saying the pytest outcome of one example test file did not end with 0 and a last line that is a passed summary with no failures, skips, or warnings (None when it did)."""
    lines = result.stdout.splitlines()
    if result.returncode == 0 and lines and PASSED_SUMMARY.fullmatch(lines[-1]):
        return None
    return (
        f"pytest of {test_file} did not pass everything with exit code 0 (exit code {result.returncode})\n"
        f"{result.stdout}{result.stderr}"
    )


def _article_run_failure(
    installed: Installed,
    directory: Path,
    arguments: Sequence[str],
    environment: dict[str, str],
    judge: Callable[[subprocess.CompletedProcess[str]], str | None],
    timeout: float,
) -> str | None:
    """Run the venv's python once in the article example dir and return the reason the judge says it failed, or a timeout reason (None when it passes).

    Do not pass -I - example files import files in the same dir (trace.py reads production.py), so like the
    `python trace.py` the reader types, the script's dir must be at the head of sys.path (-I includes -P, which removes it). The dir is a copy outside the repo, so
    it does not pick up the checkout's doeff.
    """
    try:
        result = subprocess.run(
            [str(installed.python), *arguments],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"python {' '.join(arguments)} did not finish within {timeout} seconds"
    return judge(result)


def spire_failure(result: subprocess.CompletedProcess[str]) -> str | None:
    """Sentence saying the outcome of spire.py did not exit 0 with empty stderr and match the decided heading and lines after the first layer (None when it did)."""
    printed = result.stdout.splitlines()
    if result.returncode != 0 or result.stderr:
        problem = f"did not exit with code 0 with nothing on stderr (exit code {result.returncode})"
    elif printed[:1] != [SPIRE_FIRST_LINE]:
        problem = f"the first line was not {SPIRE_FIRST_LINE!r}"
    elif tuple(printed[-len(SPIRE_LAST_LINES) :]) != SPIRE_LAST_LINES:
        problem = f"the last lines differed from the decided {list(SPIRE_LAST_LINES)!r}"
    else:
        return None
    return f"{SPIRE_FILE.name} {problem}\n{result.stdout}{result.stderr}"


def check_spire(installed: Installed, place: Path, timeout: float) -> str | None:
    """Run spire.py as `python spire.py` in a copied dir, like a reader, and return the reason it failed (None when it passes)."""
    # check_tour_examples copies the whole example dir to place / effects_tour, so put the spire.py set in a separate dir
    directory = place / SPIRE_FILE.stem
    directory.mkdir()
    for path in (SPIRE_FILE, *SPIRE_TEST_FILES):
        shutil.copy(REPO / path, directory)
    failure = _article_run_failure(
        installed,
        directory,
        ["-W", "error", SPIRE_FILE.name],
        verify_release_install.example_environment(place),
        spire_failure,
        timeout,
    )
    print_result(f"{SPIRE_FILE.name} prints the answers of the six layers as decided", failure)
    return failure


def check_article_examples(installed: Installed, place: Path, timeout: float) -> list[str]:
    """Run the intro article's example dir in the core-only venv, add pytest and run the dir's tests, and return sentences naming what failed (empty when it passes).

    Like a reader, run in a copy of the example dir. Warnings count as errors too (the article shows the printed output as is). This adds pytest to the venv, so
    call it after the check that looks at only the four core packages.
    """
    directory = place / ARTICLE_EXAMPLE_DIRECTORY.name
    shutil.copytree(
        REPO / ARTICLE_EXAMPLE_DIRECTORY,
        directory,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    print(
        f"  intro article examples ({ARTICLE_EXAMPLE_DIRECTORY} - what the article says runs with pip install doeff)",
        flush=True,
    )
    environment = verify_release_install.example_environment(place)
    failures: list[str] = []
    for script in ARTICLE_SCRIPTS:
        failure = _article_run_failure(
            installed,
            directory,
            ["-W", "error", script.name],
            script.environment(environment),
            functools.partial(article_script_failure, script),
            timeout,
        )
        print_result(f"{script.label()} prints the lines the article shows", failure)
        failures.extend([] if failure is None else [failure])
    spire = check_spire(installed, place, timeout)
    _uv(["pip", "install", "--python", str(installed.python), "pytest"], place)
    tests_failure = _article_run_failure(
        installed,
        directory,
        ["-m", "pytest", "-q", "-W", "error", "-p", "no:cacheprovider"],
        environment,
        article_tests_failure,
        timeout,
    )
    print_result("pytest in the example dir passes everything with only the four core packages and pytest", tests_failure)
    spire_tests = _article_run_failure(
        installed,
        place / SPIRE_FILE.stem,
        ["-m", "pytest", "-q", "-W", "error", "-p", "no:cacheprovider"],
        environment,
        functools.partial(example_tests_failure, SPIRE_TEST_FILES[0]),
        timeout,
    )
    print_result("test_spire.py next to spire.py passes everything with only the four core packages and pytest", spire_tests)
    return [
        *failures,
        *([] if spire is None else [spire]),
        *([] if tests_failure is None else [tests_failure]),
        *([] if spire_tests is None else [spire_tests]),
    ]


def tour_script_failure(name: str, result: subprocess.CompletedProcess[str]) -> str | None:
    """Sentence saying one effects_tour script's outcome did not end with 0 and print the required last line (None when it did).

    For stderr, allow only the DeprecationWarning the repo's older version emits for `await_handler()` calls (that line and the source line the warning echoes).
    """
    last = TOUR_SCRIPTS[name]
    printed = result.stdout.splitlines()
    unexpected = [
        line for line in result.stderr.splitlines() if line.strip() and "await_handler" not in line
    ]
    if result.returncode != 0 or unexpected:
        problem = f"did not exit with code 0 with nothing on stderr (exit code {result.returncode})"
    elif not printed:
        problem = "printed nothing"
    elif last is not None and not (last[0] in printed and printed[-1] == last[-1]):
        problem = f"did not print {list(last)!r} last"
    else:
        return None
    return f"{name} {problem}\n{result.stdout}{result.stderr}"


def check_tour_examples(installed: Installed, place: Path, timeout: float) -> list[str]:
    """Run the scripts of TOUR_SCRIPTS and the tests of TOUR_TEST_FILES from a copy of the article example dir in the core-only venv, and return sentences naming what failed (empty when it passes).

    The tests use the pytest that check_article_examples added to the venv, so call it after that.
    """
    directory = place / TOUR_DIRECTORY.name
    shutil.copytree(REPO / TOUR_DIRECTORY, directory, ignore=shutil.ignore_patterns("__pycache__"))
    print(
        f"  first article examples ({TOUR_DIRECTORY} - what the article says runs with pip install doeff)", flush=True
    )
    environment = verify_release_install.example_environment(place)
    failures: list[str] = []
    for name in TOUR_SCRIPTS:
        failure = _article_run_failure(
            installed,
            directory,
            [name],
            environment,
            functools.partial(tour_script_failure, name),
            timeout,
        )
        print_result(f"{name} passes and prints through the article's production stack", failure)
        failures.extend([] if failure is None else [failure])
    for name in TOUR_TEST_FILES:
        failure = _article_run_failure(
            installed,
            directory,
            ["-m", "pytest", "-q", "-W", "error", "-p", "no:cacheprovider", name],
            environment,
            functools.partial(example_tests_failure, TOUR_DIRECTORY / name),
            timeout,
        )
        print_result(f"{name} passes everything with only the four core packages and pytest", failure)
        failures.extend([] if failure is None else [failure])
    return failures


def check_python(python_request: str, work: Path, timeout: float, source: Source) -> list[str]:
    """Verify with only the core installed into a fresh venv for one Python version, and return sentences naming what failed."""
    place = work / f"py{python_request.replace('+', '-')}"
    place.mkdir(parents=True)
    print(
        f"\n== Python {python_request}: install doeff ({source_name(source)}) into a fresh venv without extras",
        flush=True,
    )
    installed = install(python_request, place, source)
    build_kind = "free-threading" if installed.free_threaded else "GIL"
    print(f"  interpreter {installed.version}({build_kind})・{installed.python}", flush=True)
    names = sorted(f"{d.name} {d.version}" for d in installed.distributions)
    print(f"  installed distributions: {', '.join(names)}", flush=True)
    problems = install_problems(installed, source)
    print_result(
        f"the installed distributions are exactly the four core packages, and the three doeff ones are {source_name(source)}",
        "\n".join(problems) or None,
    )
    if isinstance(source, PyPI):
        # The expectations for the extra sentences, demo command, and LLM example come from the checkout, which can be newer than the released version. The wheel's README was written at the same time as the installed version,
        # so it matches. The GitHub README and the article examples promise readers they run with today's pip install doeff.
        return [
            *problems,
            *check_readmes(wheel_readmes(installed), installed, place, timeout).failures,
            # Check the GitHub README, which first-time visitors read, against the installed version - it fails if the README gets ahead of the released version.
            *check_readmes(checkout_readmes(), installed, place, timeout).failures,
            *check_public_names(installed, place, timeout),
            # Add pytest to the venv, so put this last.
            *check_article_examples(installed, place, timeout),
            *check_tour_examples(installed, place, timeout),
        ]
    return [
        *problems,
        # The wheel README built from the checkout is the same as the checkout's README, so run it only once.
        *check_readmes(wheel_readmes(installed), installed, place, timeout).failures,
        *check_public_names(installed, place, timeout),
        *check_extra_uses(installed, place, timeout),
        *check_demo(installed, place, timeout),
        # Add pytest to the venv, so put this last.
        *check_llm_example(installed, place, timeout),
        # Do not run 8 and 9 - the article examples are written to stay at the PyPI version (ADR-DOE-API-002 R3), so running them at the checkout version fails whenever the checkout
        # API changes. Only --pypi holds that check.
    ]


def parse_source(pypi: str | None) -> Source:
    """Make the --pypi value the install source (checkout if absent; latest means the newest on PyPI)."""
    if pypi is None:
        return Checkout()
    return PyPI(None if pypi == LATEST else pypi)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Read the arguments - the Python versions, and whether to keep the work dir."""
    parser = argparse.ArgumentParser(
        description=(
            "In a fresh venv with only the four core packages, verify the README Python examples, that their imports are read as public names by the type checkers, "
            "the sentences naming the Hy and HTTP extras, the demo command python -m doeff demo, the LLM agent example and its pytest test example, "
            "and the intro article examples."
        ),
    )
    parser.add_argument(
        "--python",
        action="append",
        dest="pythons",
        metavar="REQUEST",
        help=f"Python spec to pass to uv (repeatable; default {' and '.join(DEFAULT_PYTHONS)})",
    )
    parser.add_argument(
        "--pypi",
        metavar="VERSION",
        help=(
            f"install this PyPI doeff version instead of the checkout ({LATEST} is the newest) - verify only the installed version's README examples and type checks, "
            "the checkout (GitHub) README examples, and the intro article examples"
        ),
    )
    parser.add_argument("--keep", action="store_true", help="do not delete the work dir after finishing")
    parser.add_argument(
        "--timeout",
        type=float,
        default=CHILD_TIMEOUT_SECONDS,
        help="time limit for one child process (seconds; default %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point - verify per Python version, print the outcome, and return the exit code."""
    print_in_utf8()
    arguments = _parse(argv)
    pythons = tuple(arguments.pythons or DEFAULT_PYTHONS)
    source = parse_source(arguments.pypi)
    if shutil.which("uv") is None:
        print("uv is not on PATH - install it by following https://docs.astral.sh/uv/", file=sys.stderr)
        return 2
    # Outside the repo (uv does not read the repo's [tool.uv] in pyproject.toml, and the example imports do not pick up the checkout's doeff).
    work = Path(tempfile.mkdtemp(prefix="doeff-core-install-check-"))
    print(f"work dir: {work}", flush=True)
    try:
        results = {
            python: check_python(python, work, arguments.timeout, source) for python in pythons
        }
    except subprocess.CalledProcessError as failed:
        print(
            f"\nvenv or install failed (did not reach verification, exit code {failed.returncode}): "
            f"{' '.join(map(str, failed.cmd))}",
            file=sys.stderr,
        )
        if failed.stderr:
            print(failed.stderr, file=sys.stderr)
        return 2
    finally:
        if arguments.keep:
            print(f"\nkept the work dir: {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print("\n== outcome", flush=True)
    for python, failures in results.items():
        print(
            f"  Python {python}: {'passed' if not failures else f'NG {len(failures)} failures'}",
            flush=True,
        )
    passed = not any(results.values())
    print("\nall passed" if passed else "\nsome checks failed - read the NG lines above", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
