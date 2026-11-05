"""Tests that the examples in doeff-traverse's examples/ run to the end as scripts and print the expected results.

An example is reading material run with `hy examples/<name>.hy`, and no test ran them, so nobody noticed when a doeff-hy macro changed:
after fa37eaaf1 on 2026-04-07 removed the fold macro and made defk's :pre/:post mandatory,
ex09_for_do_when.hy was failing on a require of fold that it did not even use.

Every `.hy` in examples/ is covered by this test (a new example is covered just by being written). An example that does not run with the current macros is put in the BROKEN table
with a reason, and we check that the examples in the table still fail — a fixed example left in the table turns red and names the row to remove.

The exit code alone is not enough: the Traverse handler records an exception inside an item as that item's failure and moves on, so
an example exits 0 even if a macro's expansion is broken inside an item (when the name the When expansion pulls was
changed, ex09 printed the item as failed rather than skipped and exited 0). For examples that run, stdout is compared with expected/<stem>.txt.
Parts that change on every run (wall-clock time, lib tracebacks) are replaced by marks on the expected-output side — which parts change is the
example's responsibility, and the test knows only the meaning of the marks. Each kind of mark has a fixed set of things it may swallow (a line-only ANY swallows traceback frame lines; a mark
inside a line is a string of a MARKS form), and a mark that swallows an item-result line turns red. Things that come out the same every time on the tree where they were written but
come loose on another tree, another machine, or a change that does not alter the answer (checkout or machine paths, names Hy assigns, frame lines) are not
placed in the expected output (UNPLACEABLE). The machine time zone is fixed to a value chosen on the child side (CHILD_TIME_ZONE), so printing local time does not depend on the machine.

That a failure inside an item shows up in the compared output is checked, per example, by a child that fails the first item inside the item on each Traverse run
(run_example_failing_a_case.py) — that child's output does not match the expected output. The failure is caused without rewriting the example's
source, so there is no case of fixing the test file through a rewrite inside the example that does not change the output. The same child counts a run marker — the number of
times the item function was called and the number of Programs run by doeff's run: a debug file that runs no item and prints only the internals of doeff-hy and Hy,
such as macro expansions and function addresses, is not an example and is not placed in examples/ (ex04_debug2 was
removed). An example runs a Program, and an example that runs no item is put in NO_CASES with a reason. Because it checks by the number that ran, a run that merely exists by name
but does not run (inside an uncalled function, inside a quote) is not counted (the test that counted by name was replaced by the run marker).

The macro owner is doeff-hy, and examples use only doeff-hy macros: redefining a removed macro inside an example teaches users a form
they cannot use, and changes in doeff-hy do not reach it either (blind B of the same design review — ex04, which restored fold with a defmacro inside the example,
ran). Macro definitions inside an example and requires from outside doeff-hy are rejected. The shape of an expansion is also an internal detail doeff-hy hides, and if an example
prints it, a macro change that does not alter the answer turns the example red, so pulling out an expansion (hy.macroexpand) is rejected too. An example that copies an expansion as a string and prints it
without pulling it out would not turn red when the macro changes and would teach a stale expansion — a copy carries marks of the expansion shape (names that Hy and doeff-hy hide, and
the __import__ with which an expansion pulls a module), so it is not allowed in the expected output. That every expansion of a doeff-hy macro an example uses carries that mark
is also checked by expanding each use site outside its context — a macro whose expansion has no mark (such as val) is put in UNMARKED_EXPANSIONS with a reason,
and a nested use site whose expansion is refused outside its context is not counted, since there is no form that can be pulled out and copied.

An example that, without copying the expansion, peeks at the value the expansion built at runtime and prints it (the value's class name, type, attributes) has no string mark, so the checks above
cannot tell — only that example turns red on a macro change that does not alter the answer. So the examples are also run under a mutation of the expansion that does not alter the answer
(run_example_mutated_expansion.py — wraps the expansion of a macro that builds a value in a Program run under a do-nothing handler. For defk and fnk,
it wraps the value returned by the defined or built function), and the output is required to remain the expected output. An example that changes turns red by name even without changing a macro.
Macros that are wrapped are in MUTATED_MACROS, and macros that are not wrapped are in UNMUTATED_MACROS with a reason; a macro an example uses for the first time must be placed in one of them. The mutation applies only to the expansion in the example's file, so only
examples and expected outputs are allowed under examples/.
"""

import difflib
import functools
import itertools
import re
import subprocess
import sys
import types
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import hy
import pytest
from doeff_hy._static_stub import hidden_name
from hy.errors import HyMacroExpansionError

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
# Expected stdout of an example that runs (<example stem>.txt). When you change an example's printing, rewrite it together with the example.
EXPECTED = EXAMPLES / "expected"
# The time limit of one child, before being stretched by load (seconds). One takes about 1 second (about 20 seconds even when the storage is cold).
CHILD_DEADLINE_SECONDS = 50
# The child's time zone (POSIX TZ spelling — readable even on a machine with no time zone database). Even if an example prints local time, the expected output does not depend on the machine's
# time zone (the datetime.fromtimestamp print failed on a machine with TZ=JST-9).
CHILD_TIME_ZONE = "UTC0"
# Number of children (examples, examples that fail inside an item) run at the same time. One child takes about 1 second, mostly importing hy and doeff (the example's own work is 0.2-0.3 seconds; ex05 does a
# sleep of 2.5 seconds). Running them one at a time adds up the seconds of 12 children, making this one file take 25-42 seconds under load, and the 60-second limit of the changed-tests run
# (make test-changed) cut off later tests. They are not all started at once so as not to greatly exceed the guideline for one test slot on the machine
# (_hold_check_slot in the root conftest) — 0.5-2.5 cores per test.
PARALLEL_CHILDREN = 4


@dataclass(frozen=True)
class Entrance:
    """Entry point of the child that runs an example in place of `hy <example>` — passes the example's path and arguments to the script."""

    script: Path
    arguments: tuple[str, ...] = ()


# Entry point of the child that runs an example with a failure inside an item (fails the first item on each Traverse run and writes a run marker to stderr).
FAILING_A_CASE = Entrance(Path(__file__).with_name("run_example_failing_a_case.py"))
# Entry point of the child that runs an example with its expansion changed into a form that does not alter the answer: wraps the expansion of the MUTATED_MACROS macros in a Program that runs under a do-nothing handler
# (the class of the wrapped value becomes WithHandler whether the original is a Traverse effect or a Program. For defk and fnk, the value returned by the defined or built
# function is wrapped). Which macro expansion builds a runtime value is doeff-hy's responsibility, and the test names them in a table. How to wrap is known by the entry point's _MUTATIONS.
MUTATED_MACROS: tuple[str, ...] = ("defk", "fnk", "traverse", "for/do")
MUTATED_EXPANSION = Entrance(
    Path(__file__).with_name("run_example_mutated_expansion.py"), MUTATED_MACROS
)
# The sentence that describes the mutation (used in the failure message).
MUTATION = f"wraps the value built by {', '.join(MUTATED_MACROS)} (for defk and fnk, the value they return) in a Program that runs under a do-nothing handler"
# The run-marker line (COUNTS_LINE of the child) that the child failing inside an item writes to stderr.
_COUNTS = re.compile(r"^case-calls: (\d+) program-runs: (\d+)$", re.MULTILINE)

# Examples that do not run with the current macros (file name -> reason). Remove the row once fixed.
BROKEN: dict[str, str] = {}

# Examples that run no Traverse item at all (file name -> reason). A failure inside an item cannot be caused, so they are checked only by how the output is compared.
NO_CASES: dict[str, str] = {
        "ex01_basic_fail.hy": "An example that shows the Fail effect outside Traverse — the failure appears in the output as the result of 3 strategies",
}

# Among the doeff-hy macros an example uses, those whose expansion carries no expansion-shape mark (macro name -> reason). The expected-output test does not detect a print that copies such a macro's expansion,
# so put it here with a reason when an example uses it for the first time (e.g. val — its one-step expansion has the same shape as a setv the user writes, and has no hidden
# internals). Remove the rows of macros that came to carry a mark or that examples no longer use.
UNMARKED_EXPANSIONS: dict[str, str] = {}

# Among the doeff-hy macros an example uses, those not wrapped by the expansion mutation (MUTATED_MACROS) (macro name -> reason). What can be wrapped is an expression that builds a runtime value, or an expression that defines
# (builds) a kleisli function; a statement that binds a name, or a statement that defines something that is neither a Program nor a kleisli function, cannot be wrapped. For a macro an example uses
# for the first time, either add it to MUTATED_MACROS (only if the entry point knows how to wrap it) or put it here with a reason. Remove the rows of macros that are no longer used or
# that were moved to MUTATED_MACROS.
UNMUTATED_MACROS: dict[str, str] = {
        "defhandler": "A statement that defines a handler — a handler is a different form that takes a Program and returns a Program, and there is no way to wrap it. An example that prints the type, signature, or received effect contents of a defined handler is not detected by the mutation (a remaining limitation)",
        "<-": "A binding statement — the binding expressions (the value returned by calling traverse, for/do, or defk) are each wrapped by their own mutation",
}

# The characters of the marks in the expected output. The kind of a mark is decided by where it is placed, and each kind has a fixed set of things it may swallow:
#   - A line that is only ANY swallows one or more traceback frame lines (_frame_violations) — it cannot swallow an item-result line.
#   - A mark inside a line is placed in one of the MARKS forms and swallows only a string of that form (it does not cross lines).
# A mark that matches any string would hide even the lines that carry item results, and a mutation that changed those lines would go unnoticed.
ANY = "…"
# Marks inside a line (spelling -> the string it swallows). When adding a kind of thing that changes on every run, add a row here.
MARKS: dict[str, str] = {
        # Wall-clock seconds ("550 in …s" in ex05)
    f"{ANY}s": r"\d+(?:\.\d+)?s",
}
_MARK = re.compile(
    "(" + "|".join(re.escape(mark) for mark in sorted(MARKS, key=len, reverse=True)) + ")"
)

# The head line of a traceback frame, and the mark lines inside a frame (position marks ^~, the abbreviated source lines from Python 3.13, the repetition abbreviation).
_FRAME_HEAD = re.compile(r'\s*File "(?P<path>[^"]+)", line \d+, in \S+')
_FRAME_MARKER = re.compile(
    r"\s*(?:[\^~]+|\.\.\.<\d+ lines>\.\.\.|\[Previous line repeated \d+ more times?\])"
)

# Locations that vary by machine and user (venv, Python, home).
_MACHINE_PATHS = sorted(
    {path.rstrip("/") for path in (sys.prefix, sys.base_prefix, str(Path.home()))} - {""}
)

# Words in a statement that start with _ (the - of Hy spelling is also counted as part of a word — names are compared after mangling).
_UNDERSCORED_WORD = re.compile(r"(?<![\w-])_[\w-]*")
# Calls where an expansion pulls a module without binding names (_do-reference in doeff-hy's macros.hy, open-form in outcome_forms.hy — the expansion of fnk
# carries no hidden names and has only this).
_EXPANSION_IMPORT = "__import__"


def _shows_expansion(text: str) -> bool:
    """Whether a statement carries an expansion-shape mark: names that Hy and doeff-hy hide (owned by doeff-hy's hidden_name — Hy's gensym, macro helpers, and bookkeeping)
    or an __import__ with which the expansion pulls a module."""
    return any(
        word == _EXPANSION_IMPORT or hidden_name(hy.mangle(word))
        for word in _UNDERSCORED_WORD.findall(text)
    )


def _searches(pattern: str, flags: int = 0) -> Callable[[str], bool]:
    """A function that asks whether the regular expression matches somewhere in the statement."""
    compiled = re.compile(pattern, flags)
    return lambda text: compiled.search(text) is not None


# Things not placed in the expected output (name -> whether the statement carries it). Each comes out the same every time on the tree where it was written, so it could stay green, but it comes loose on another tree,
# another machine, or a macro or lib change that does not alter the answer — reject it so it is noticed on the tree where it was placed. If it appears in the print, turn that part into a mark.
UNPLACEABLE: dict[str, Callable[[str], bool]] = {
        # Always comes loose in another checkout, such as a daily work tree
        "checkout path": _searches(re.escape(str(EXAMPLES.parents[2]))),
        "machine locations (venv, Python, home)": _searches(
        "|".join(re.escape(f"{path}/") for path in _MACHINE_PATHS)
    ),
        # The number in a name Hy assigns is decided by the macro expansion and compile order, not by the example's result.
        # A print that copies an expansion as a string does not come loose on a macro change that does not alter the answer, and teaches a stale expansion
        "expansion-shape marks (names hidden by Hy and doeff-hy, __import__ in an expansion)": _shows_expansion,
        # Includes paths and the line numbers of libs and examples — make the frame a line-only ANY
        "traceback frame lines": _searches(r'^\s*File "[^"]*", line \d+', re.MULTILINE),
}


def _unplaceable_in(text: str) -> list[str]:
    """The names of the things a statement carries that are not placed in the expected output."""
    return [name for name, finds in UNPLACEABLE.items() if finds(text)]


# Forms not used inside an example (defining a macro), and the packages an example may require (the macro owner).
_MACRO_DEFINERS = frozenset({"defmacro", "defmacro!", "defreader"})
_MACRO_OWNER = "doeff_hy"
# Functions not called inside an example that pull out a macro expansion. The shape of an expansion is doeff-hy's hidden internals, and if an example prints it, a macro change
# that does not alter the answer turns the example red (adding a print of an expansion to an example
# that runs a Program passed the test).
_MACRO_EXPANDERS = frozenset({"macroexpand", "macroexpand_1", "macroexpand_all"})


@dataclass(frozen=True)
class RunMarks:
    """The run marker counted by the child that fails inside an item."""

    # Number of calls of the Traverse item function
    case_calls: int
    # Number of Programs run by doeff's run
    program_runs: int


def _run_marks(result: subprocess.CompletedProcess[str]) -> RunMarks | None:
    """The run marker on the child's stderr (None if it finished without writing it)."""
    found = _COUNTS.search(result.stderr)
    return None if found is None else RunMarks(int(found[1]), int(found[2]))


def _examples() -> list[str]:
    return sorted(path.name for path in EXAMPLES.glob("*.hy"))


def _runnable() -> list[str]:
    return [name for name in _examples() if name not in BROKEN]


@dataclass(frozen=True)
class Child:
    """One child the test waits for — runs one example directly or through an entry point (failing inside an item, mutating the expansion)."""

    # The example's file name
    name: str
    # The entry point that runs the example (None means `hy <example>`)
    entrance: Entrance | None = None


def _in_child_time_zone(command: list[str]) -> list[str]:
    """The child's command — runs with the parent's environment as is, with only the time zone set to a fixed value (so the expected output does not depend on the machine's time zone)."""
    return ["env", f"TZ={CHILD_TIME_ZONE}", *command]


def _run_example(
    example: Path, entrance: Entrance | None, deadline_scale_factor: float
) -> subprocess.CompletedProcess[str]:
    """Runs an example with `hy <example>` (through the entry point if there is one)."""
    command = (
        [sys.executable, "-m", "hy", str(example)]
        if entrance is None
        else [sys.executable, str(entrance.script), str(example), *entrance.arguments]
    )
    return subprocess.run(
        _in_child_time_zone(command),
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )


def _run_child(child: Child, deadline_scale_factor: float) -> subprocess.CompletedProcess[str]:
    return _run_example(EXAMPLES / child.name, child.entrance, deadline_scale_factor)


def _child_of(item: pytest.Item) -> Child | None:
    """The child one test waits for. None for a test that waits for no child (the empty parametrize item pytest places when BROKEN is empty also has no example name in its name,
    so it gets None)."""
    callspec = getattr(item, "callspec", None)
    name = callspec.params.get("name") if callspec is not None else None
    if not isinstance(name, str):
        return None
    function = getattr(item, "function", None)
    if function in (test_example_runs, test_broken_example_is_still_broken):
        return Child(name)
    if function in (test_case_failure_shows_in_compared_output, test_example_runs_a_program):
        return Child(name, FAILING_A_CASE)
    if function is test_example_output_survives_expansion_mutation:
        return Child(name, MUTATED_EXPANSION)
    return None


class Children:
    """At the first test, starts all the children for the selected tests and runs them PARALLEL_CHILDREN at a time, and each test waits for the
    outcome of its own child. Only the children of the tests selected in this session are started (select one test with -k and only one child)."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=PARALLEL_CHILDREN)
        self._mut_runs: dict[Child, Future[subprocess.CompletedProcess[str]]] | None = None

    def start(self, items: list[pytest.Item], deadline_scale_factor: float) -> None:
        """Does not start a child for an example without a file (a removed BROKEN example) — that test names it before waiting for a child."""
        if self._mut_runs is not None:
            return
        children = (_child_of(item) for item in items)
        self._mut_runs = {
            child: self._pool.submit(_run_child, child, deadline_scale_factor)
            for child in children
            if child is not None and (EXAMPLES / child.name).is_file()
        }

    def result(
        self, name: str, entrance: Entrance | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert self._mut_runs is not None, (
            "waited for an outcome before starting the children — a test receives it from the children fixture"
        )
        return self._mut_runs[Child(name, entrance)].result()

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


@pytest.fixture(scope="module")
def _children() -> Iterator[Children]:
    children = Children()
    yield children
    children.close()


@pytest.fixture
def children(
    _children: Children, request: pytest.FixtureRequest, deadline_scale_factor: float
) -> Children:
    """The pool of children (the load multiplier is a function-scope fixture, so the first test passes it to the module-scope pool)."""
    _children.start(request.session.items, deadline_scale_factor)
    return _children


def _expected(name: str) -> Path:
    return EXPECTED / f"{Path(name).stem}.txt"


def _line_pattern(line: str) -> str | None:
    """The regular expression for one line of the expected output (a line that is not a line-only ANY). None if there is an ANY that is not of a MARKS form."""
    # Splitting by marks: even-numbered parts are literal text, odd-numbered parts are marks.
    pieces = _MARK.split(line)
    if any(ANY in literal for literal in pieces[::2]):
        return None
    return "".join(
        MARKS[piece] if index % 2 else re.escape(piece) for index, piece in enumerate(pieces)
    )


def _mark_violations(expected: str) -> list[str]:
    """The lines of the expected output (with line numbers) that have an ANY not of a mark form."""
    return [
        f"{number}: {line}"
        for number, line in enumerate(expected.split("\n"), start=1)
        if line != ANY and _line_pattern(line) is None
    ]


def _expected_line_pattern(index: int, line: str) -> str:
    """The regular expression for line index of the expected output. A line-only ANY swallows one or more lines as a named group (frames<line>). An ANY not of a mark form is
    compared literally (_mark_violations names where it is wrong)."""
    if line == ANY:
        return rf"(?P<frames{index}>[^\n]*(?:\n[^\n]*)*?)"
    pattern = _line_pattern(line)
    return re.escape(line) if pattern is None else pattern


def _pattern(expected: str) -> re.Pattern[str]:
    return re.compile(
        "\n".join(
            _expected_line_pattern(index, line) for index, line in enumerate(expected.split("\n"))
        )
    )


@functools.cache
def _source_lines(path: str) -> frozenset[str]:
    """The non-empty lines of a file (surrounding whitespace removed). Empty for an unreadable file (such as <frozen …>)."""
    file = Path(path)
    if not file.is_file():
        return frozenset()
    lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    return frozenset(stripped for line in lines if (stripped := line.strip()))


def _frame_violations(lines: list[str]) -> list[str]:
    """Lines that are not traceback frame lines. A frame sequence starts with a File line, and the following lines are the File line, the source line of the file of the preceding File line, and
    the mark lines inside the frame."""
    heads = [_FRAME_HEAD.fullmatch(line) for line in lines]
    # For each line, the source line of the file of the preceding File line (empty if there is no File line before it).
    sources = itertools.accumulate(
        heads,
        lambda source, head: source if head is None else _source_lines(head["path"]),
        initial=frozenset[str](),
    )
    return [
        line
        for index, (line, head, source) in enumerate(zip(lines, heads, sources, strict=False))
        if head is None
        and (index == 0 or not (line.strip() in source or _FRAME_MARKER.fullmatch(line)))
    ]


def _compare(expected: str, actual: str) -> list[str] | None:
    """Compares actual with expected: None if they do not match; if they match, the lines swallowed by a line-only ANY that are not frame lines (empty if
    none)."""
    match = _pattern(expected).fullmatch(actual)
    if match is None:
        return None
    return [
        line
        for frames in match.groupdict().values()
        for line in _frame_violations(frames.split("\n"))
    ]


def _matches(expected: str, actual: str) -> bool:
    """Whether actual matches expected and the line-only ANY swallowed only frame lines."""
    return _compare(expected, actual) == []


def _output_diff(label: str, expected: str, actual: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            expected.splitlines(), actual.splitlines(), label, "stdout", lineterm=""
        )
    )


def _subforms(form: object) -> Iterator[object]:
    yield form
    if isinstance(form, hy.models.Sequence):
        for child in form:
            yield from _subforms(child)


def _module_package(arg: object) -> str | None:
    """The head package of the word of a require module (None if it is not a module word). The Hy 1.x reader reads `a.b` as `(. a b)`."""
    if isinstance(arg, hy.models.Symbol):
        return hy.mangle(str(arg))
    if isinstance(arg, hy.models.Expression) and len(arg) > 1 and arg[0] == hy.models.Symbol("."):
        return _module_package(arg[1])
    return None


def _required_packages(form: hy.models.Expression) -> list[str]:
    """The head package of the module in `(require <module> [name …] <module> :as <alias> …)` (the word after a keyword is an alias)."""
    packages: list[str] = []
    after_keyword = False
    for arg in list(form)[1:]:
        package = None if after_keyword else _module_package(arg)
        if package is not None:
            packages.append(package)
        after_keyword = isinstance(arg, hy.models.Keyword)
    return packages


def _forms(source: str, filename: str) -> Iterator[object]:
    """All forms of the source (including nested forms)."""
    for top in hy.read_many(source, filename=filename):
        yield from _subforms(top)


def _callee(head: object) -> str | None:
    """The name of the function at the head of a call (`a.b` is read by the reader as `(. a b)` — its last name)."""
    if isinstance(head, hy.models.Symbol):
        return hy.mangle(str(head))
    if (
        isinstance(head, hy.models.Expression)
        and len(head) > 2
        and head[0] == hy.models.Symbol(".")
    ):
        return _callee(head[-1])
    return None


def _macro_violations(source: str, filename: str) -> list[str]:
    return [
        violation for form in _forms(source, filename) for violation in _form_macro_violations(form)
    ]


def _form_macro_violations(form: object) -> list[str]:
    """Violations of one form: pulling out a macro expansion, defining a macro, a require from outside doeff-hy."""
    if not (isinstance(form, hy.models.Expression) and form):
        return []
    callee = _callee(form[0])
    expanded = [f"call to {callee}"] if callee in _MACRO_EXPANDERS else []
    head = str(form[0]) if isinstance(form[0], hy.models.Symbol) else ""
    if head in _MACRO_DEFINERS:
        return [*expanded, f"{head} {form[1] if len(form) > 1 else ''}".strip()]
    if head == "require":
        return [
            *expanded,
            *(
                f"require {package}"
                for package in _required_packages(form)
                if package != _MACRO_OWNER
            ),
        ]
    return expanded


def _doeff_hy_macro_names(form: hy.models.Expression) -> frozenset[str]:
    """The names (mangled) of the macros that `(require doeff-hy.macros [defk <- …] …)` pulls from the doeff-hy module."""
    return frozenset(
        hy.mangle(str(name))
        for module, names in itertools.pairwise(list(form)[1:])
        if _module_package(module) == _MACRO_OWNER and isinstance(names, hy.models.List)
        for name in names
        if isinstance(name, hy.models.Symbol)
    )


@dataclass(frozen=True)
class MacroUse:
    """One use site of a doeff-hy macro in the source, and its one-step expansion outside that context."""

    # The line of the use site
    line: int
    # The macro's name (as spelled)
    macro: str
    # Whether it sits inside a use site of another doeff-hy macro (if so, the outer macro expands it within the context)
    nested: bool
    # The hy.repr of the expansion, or the error when it could not be expanded outside the context
    expansion: str | HyMacroExpansionError

    @property
    def name(self) -> str:
        return f"{self.line}: {self.macro}"


def _uses_in(
    form: object, macros: frozenset[str], nested: bool
) -> Iterator[tuple[hy.models.Expression, bool]]:
    """The expressions in form that have a macro name at the head, and whether each sits inside another use site."""
    is_use = (
        isinstance(form, hy.models.Expression)
        and bool(form)
        and isinstance(form[0], hy.models.Symbol)
        and hy.mangle(str(form[0])) in macros
    )
    if is_use and isinstance(form, hy.models.Expression):
        yield form, nested
    if isinstance(form, hy.models.Sequence):
        for child in form:
            yield from _uses_in(child, macros, nested or is_use)


def _expand_once(
    form: hy.models.Expression, module: types.ModuleType
) -> str | HyMacroExpansionError:
    """The hy.repr of the one-step expansion of form (the error, if the macro refuses to expand outside the context)."""
    try:
        return hy.repr(hy.macroexpand_1(form, module=module))
    except HyMacroExpansionError as refused:
        return refused


def _doeff_hy_macro_uses(source: str, filename: str) -> list[MacroUse]:
    """The use sites of doeff-hy macros in the source and their one-step expansions. The expansion is done inside a module in which only the source's doeff-hy requires were evaluated,
    detaching each use site from its context one at a time — without running the example, to see the form a user could pull out with `hy.macroexpand` and copy."""
    requires = [
        form
        for form in _forms(source, filename)
        if isinstance(form, hy.models.Expression)
        and form
        and form[0] == hy.models.Symbol("require")
    ]
    module = types.ModuleType(f"expansion_of_{Path(filename).stem}")
    macros = frozenset[str]().union(*(_doeff_hy_macro_names(form) for form in requires))
    for form in requires:
        if _doeff_hy_macro_names(form):
            hy.eval(form, module=module)
    return [
        MacroUse(form.start_line, str(form[0]), nested, _expand_once(form, module))
        for top in hy.read_many(source, filename=filename)
        for form, nested in _uses_in(top, macros, nested=False)
    ]


def _unexpanded(uses: list[MacroUse]) -> list[str]:
    """Among the outermost use sites, those that cannot be expanded outside the context. A nested use site is expanded by the outer macro inside the context, so
    it is not counted even if refused outside the context — there is no form that can be pulled out and copied."""
    return [
        use.name
        for use in uses
        if not use.nested and isinstance(use.expansion, HyMacroExpansionError)
    ]


def _unmarked(uses: list[MacroUse]) -> list[MacroUse]:
    """Among the use sites that could be expanded, those whose expansion has no expansion-shape mark."""
    return [
        use
        for use in uses
        if isinstance(use.expansion, str) and not _shows_expansion(use.expansion)
    ]


@functools.cache
def _example_macro_uses(name: str) -> list[MacroUse]:
    path = EXAMPLES / name
    return _doeff_hy_macro_uses(path.read_text(encoding="utf-8"), str(path))


@pytest.mark.parametrize("name", _runnable())
def test_example_runs(name: str, children: Children) -> None:
    result = children.result(name)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = _expected(name)
    assert expected.is_file(), (
        f"{name}'s expected output {expected.relative_to(EXAMPLES)} is missing — put the stdout of running the example there, "
        f"and turn the parts that change on every run into marks"
    )
    swallowed = _compare(expected.read_text(encoding="utf-8"), result.stdout)
    assert swallowed is not None, _output_diff(
        str(expected.relative_to(EXAMPLES)), expected.read_text(encoding="utf-8"), result.stdout
    )
    assert swallowed == [], (
        f"the line-only {ANY} in {expected.relative_to(EXAMPLES)} swallowed a line that is not a traceback frame — a line-only {ANY}"
        f" hides only frames. Write the swallowed lines in the expected output: {swallowed}"
    )


@pytest.mark.parametrize("name", _runnable())
def test_case_failure_shows_in_compared_output(name: str, children: Children) -> None:
    """That a failure inside an item shows up in the compared output: the child that failed the first item inside the item on each Traverse run exits 0, and its output
    does not match the expected output. An example that runs no item at all is put in NO_CASES with a reason."""
    result = children.result(name, FAILING_A_CASE)
    marks = _run_marks(result)
    assert marks is not None, (
        f"the child that fails inside an item finished without writing a run marker\n{result.stdout}{result.stderr}"
    )
    if name in NO_CASES:
        assert marks.case_calls == 0, (
            f"{name} now runs items — remove the NO_CASES row (reason: {NO_CASES[name]})"
        )
        return
    assert marks.case_calls > 0, (
        f"{name} runs no Traverse item at all — a debug file that runs no items and prints only the internals of doeff-hy and Hy is"
        f" not an example (do not place it in examples/). If it is an example with no items, add it to NO_CASES with a reason"
    )
    # The child exiting non-zero means the caused failure was not recorded as an item failure and escaped the example — the way outputs are compared cannot be checked,
    # so it is not counted as the failure having been seen (the traverse-lib item-failure policy changed, or the child's entry point is broken)
    assert result.returncode == 0, (
        f"the child that failed inside an item exited non-zero — the caused failure was not recorded as an item failure and escaped the example. Align the child's entry point"
        f" (the owner of how an item failure is caused) with traverse-lib's current item-failure policy\n{result.stdout}{result.stderr}"
    )
    expected = _expected(name)
    assert expected.is_file(), f"{name}'s expected output is missing (test_example_runs names it)"
    assert not _matches(expected.read_text(encoding="utf-8"), result.stdout), (
        f"the output of the child that failed inside an item matches {expected.relative_to(EXAMPLES)} — the example does not print item results, or"
        f" a mark hides even the item results\n{result.stdout}"
    )


@pytest.mark.parametrize("name", _runnable())
def test_example_runs_a_program(name: str, children: Children) -> None:
    """That an example runs a Program with doeff's run, checked by the number that ran rather than by name (counted by the child that fails inside an item — a run inside an uncalled
    function or inside a quote is not counted)."""
    marks = _run_marks(children.result(name, FAILING_A_CASE))
    assert marks is not None, "the child that fails inside an item finished without writing a run marker"
    assert marks.program_runs > 0, (
        f"{name} does not run a Program with doeff's run — examples/ is the place for reading material that runs a Program and prints the result, "
        "and does not hold debug files that only print macro expansions or function reprs"
    )


def _expansion_mutation_change(
    example: Path, expected: str, result: subprocess.CompletedProcess[str]
) -> str | None:
    """Whether an example run under the answer-preserving expansion mutation keeps the expected output: if it changed, a failure message naming that example (None if unchanged
    )."""
    if result.returncode != 0:
        return (
            f"{example.name} failed under the answer-preserving expansion mutation ({MUTATION}) — the example relies on the shape of the value the expansion built (type, attributes). "
            f"An example uses only the answer after run (if the failure is inside the mutation entry point {MUTATED_EXPANSION.script.name}, the entry point cannot wrap the current macro's"
            f" call site — align the entry point)\n{result.stdout}{result.stderr}"
        )
    if _matches(expected, result.stdout):
        return None
    return (
        f"the output of {example.name} changes under the answer-preserving expansion mutation ({MUTATION}) — the example prints the shape (class name, type, attributes) of the value the expansion built."
        f" If a macro changes its expansion without changing the answer, the example turns red at the hands of the person who fixed the macro. An example prints only the answer after run\n"
        f"{_output_diff(f'expected output of {example.name}', expected, result.stdout)}"
    )


@pytest.mark.parametrize("name", _runnable())
def test_example_output_survives_expansion_mutation(name: str, children: Children) -> None:
    """Even under the answer-preserving expansion mutation, an example's output stays the expected output — an example that prints the class name, type, or attributes of the value the expansion built
    is not a copy of an expansion string, so the mark test cannot tell, and when a macro changes its expansion without changing the answer only that example turns red."""
    expected = _expected(name)
    assert expected.is_file(), f"{name}'s expected output is missing (test_example_runs names it)"
    change = _expansion_mutation_change(
        EXAMPLES / name,
        expected.read_text(encoding="utf-8"),
        children.result(name, MUTATED_EXPANSION),
    )
    assert change is None, change


def test_marks_count_only_what_ran(tmp_path: Path, deadline_scale_factor: float) -> None:
    """Every current example runs a Program, so that a run that exists only by name and does not run is not counted is checked with a source that contains one:
    a mere import, inside an uncalled function, inside a quote, and the same name outside doeff (a run defined inside the example) are not counted, and only the single run that ran is
    counted (the test that counted by name counted even a call inside an uncalled function)."""
    script = tmp_path / "names_without_runs.hy"
    script.write_text(
        """
(import doeff [run :as doeff-run Pure])
(defn never-called [] (doeff-run (Pure 0)))
(print (hy.repr '(doeff-run (Pure 0))))
(defn run [x] x)
(run 1)
(print (doeff-run (Pure 1)))
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(FAILING_A_CASE.script), str(script)],
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _run_marks(result) == RunMarks(case_calls=0, program_runs=1), result.stderr


def test_children_run_in_a_fixed_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The child runs in CHILD_TIME_ZONE regardless of the parent's time zone (even if the parent is shifted 9 hours from UTC, local time at timestamp 0 is 00:00)."""
    monkeypatch.setenv("TZ", "JST-9")
    result = subprocess.run(
        _in_child_time_zone(
            [sys.executable, "-c", "import time; print(time.strftime('%H:%M', time.localtime(0)))"]
        ),
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.stdout == "00:00\n"


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_broken_example_is_still_broken(name: str, children: Children) -> None:
    assert (EXAMPLES / name).is_file(), f"the example for BROKEN row {name} is not in examples/ — remove the row"
    result = children.result(name)
    assert result.returncode != 0, (
        f"{name} now runs — remove the BROKEN row (reason: {BROKEN[name]})"
    )


@pytest.mark.parametrize("name", _examples())
def test_example_uses_only_doeff_hy_macros(name: str) -> None:
    path = EXAMPLES / name
    violations = _macro_violations(path.read_text(encoding="utf-8"), str(path))
    assert violations == [], (
        f"{name} defines a macro, requires from outside doeff-hy, or pulls out an expansion ({violations}) — "
        "macros belong in doeff-hy, and the shape of an expansion is doeff-hy's hidden internals, so an example does not print it"
    )


def test_macro_check_sees_nested_forms() -> None:
    """The current examples have no violations, so that the check descends into forms is verified with a source that contains violations
    (a check that does not descend would overlook a defmacro inside an example and stay green)."""
    source = """
(require doeff-hy.macros [defk] support.fold-shim [fold] bare [x] doeff_hy.handle :as h)
(when True
  (defmacro fold [coll] coll)
  (print (hy.repr (hy.macroexpand '(traverse (<- v (Iterate xs)) v))))
  (print (macroexpand-1 '(fold xs))))
"""
    assert _macro_violations(source, "<inline>") == [
        "require support",
        "require bare",
        "defmacro fold",
        "call to macroexpand",
        "call to macroexpand_1",
    ]


def test_tables_name_examples_as_they_are() -> None:
    """Expected outputs exist for exactly the examples that run, and the table of item-less examples names current examples (rows of removed or renamed examples are not left)."""
    compared = _runnable()
    present = {path.name for path in EXPECTED.glob("*.txt")}
    assert present - {_expected(name).name for name in compared} == set(), (
        "there is an expected output with no example to compare — remove it, or remove the example from BROKEN"
    )
    assert set(NO_CASES) - set(compared) == set(), "a NO_CASES row names no example that runs — remove the row"


def test_expected_output_places_only_known_marks() -> None:
    """ANY in the expected output is placed only as a line-only ANY or in a MARKS form (a mark that matches any string would hide even item results)."""
    violations = {
        path.name: found
        for path in sorted(EXPECTED.glob("*.txt"))
        if (found := _mark_violations(path.read_text(encoding="utf-8")))
    }
    assert violations == {}, (
        f"the expected output has an {ANY} that is not of a mark form — place a mark inside a line in a MARKS form (a mark with a fixed set of things it may swallow), and make a traceback"
        f" frame a line-only {ANY}. If the kind of thing that changes on every run is new, add a row to MARKS: {violations}"
    )


def test_expected_output_places_nothing_unplaceable() -> None:
    found = {
        path.name: names
        for path in sorted(EXPECTED.glob("*.txt"))
        if (names := _unplaceable_in(path.read_text(encoding="utf-8")))
    }
    assert found == {}, (
        f"the expected output contains something that must not be placed — turn that part into a mark (for an expansion-shape mark, the example does not print the expansion): {found}"
    )


@pytest.mark.parametrize("name", _examples())
def test_example_expansions_show_their_marks(name: str) -> None:
    """Every expansion of a doeff-hy macro an example uses carries an expansion-shape mark — if a copy of an expansion is printed, the expected-output test names it.
    A copy of the expansion of a macro that produces an unmarked expansion cannot be told, so give that macro's expansion a hidden name, add a kind of mark, or
    put it in UNMARKED_EXPANSIONS with a reason. The outermost use site can be expanded even outside the context (if it cannot, the check becomes empty)."""
    uses = _example_macro_uses(name)
    assert _unexpanded(uses) == [], (
        f"the outermost doeff-hy macro use site in {name} cannot be expanded outside the context — the expansion-shape mark cannot be checked:"
        f" {_unexpanded(uses)}"
    )
    unmarked = [use.name for use in _unmarked(uses) if use.macro not in UNMARKED_EXPANSIONS]
    assert unmarked == [], (
        f"the expansion of the doeff-hy macro in {name} has no expansion-shape mark (neither {_EXPANSION_IMPORT} nor a hidden name) — even if it is copied and printed,"
        f" the expected-output test cannot tell. Give the expansion a hidden name, add a kind of mark, or put it in UNMARKED_EXPANSIONS"
        f" with a reason: {unmarked}"
    )


def test_unmarked_table_names_unmarked_macros() -> None:
    """A row of UNMARKED_EXPANSIONS names a macro that current examples use and whose expansion has no mark (rows of macros that came to carry a mark or are no longer used
    are not left)."""
    unmarked = {use.macro for name in _examples() for use in _unmarked(_example_macro_uses(name))}
    assert set(UNMARKED_EXPANSIONS) - unmarked == set(), (
        "a UNMARKED_EXPANSIONS row does not name an unmarked expansion in the current examples — remove the row"
    )


def test_expansion_check_reads_uses_out_of_context() -> None:
    """All use sites in the current examples carry a mark and can be expanded outside the context, so that unmarked expansions (val) and use sites that cannot be expanded outside the context
    are told apart is verified with a source that contains them: the outermost use site that cannot be expanded is named, and a nested one that cannot be expanded is
    not counted (the outer macro expands it inside the context — the shape of a doeff-hy change refusing a (<- …) outside the context, raised by blind A). A use site
    that refuses expansion is built with the malformed (<-) that the current doeff-hy refuses (fnk wraps without opening the body, so an outer fnk can be expanded). Which
    use sites are refused outside the context is doeff-hy's decision, so apart from the 2 malformed ones, no count is fixed (design-review check c4)."""
    source = """
(require doeff-hy.macros [defk <- val traverse fnk])
(val inputs [1 2 3])
(<-)
(setv broken (fnk [] (<-)))
(defk total [xs]
  {:pre [(: xs list)] :post [(: % int)]}
  (<- n (traverse (<- x (Iterate xs)) x))
  n)
"""
    uses = _doeff_hy_macro_uses(source, "<inline>")
    assert [use.name for use in uses] == [
        "3: val",
        "4: <-",
        "5: fnk",
        "5: <-",
        "6: defk",
        "8: <-",
        "8: traverse",
        "8: <-",
    ]
    refused = {use.name for use in uses if isinstance(use.expansion, HyMacroExpansionError)}
    assert {"4: <-", "5: <-"} <= refused
    assert _unexpanded(uses) == ["4: <-"]
    assert [use.name for use in _unmarked(uses)] == ["3: val"]


def test_printed_expansion_copy_is_named(tmp_path: Path, deadline_scale_factor: float) -> None:
    """If a print of an expansion copied as a string, without pulling it out, is added to an example that runs a Program, the expected-output test names it (the other tests of the example test — runs, runs a Program, macro owner — pass in this form). The copy is
    the current expansion itself (the form of an old copy is in test_unplaceable_table_catches_its_samples)."""
    example = EXAMPLES / "ex04_simple.hy"
    source = example.read_text(encoding="utf-8")
    copied = next(
        use.expansion
        for use in _doeff_hy_macro_uses(source, str(example))
        if use.macro == "traverse" and isinstance(use.expansion, str)
    )
    script = tmp_path / "ex04_shows_expansion.hy"
    script.write_text(f"{source}(print {hy.repr(copied)})\n", encoding="utf-8")
    result = subprocess.run(
        _in_child_time_zone([sys.executable, "-m", "hy", str(script)]),
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _macro_violations(script.read_text(encoding="utf-8"), str(script)) == []
    assert result.stdout == f"{_expected(example.name).read_text(encoding='utf-8')}{copied}\n"
    assert _unplaceable_in(_expected(example.name).read_text(encoding="utf-8")) == []
    assert _unplaceable_in(result.stdout) == [
        "expansion-shape marks (names hidden by Hy and doeff-hy, __import__ in an expansion)"
    ]


def test_example_macros_are_mutated_or_named() -> None:
    """A doeff-hy macro an example uses is either wrapped by the expansion mutation (MUTATED_MACROS) or has a stated reason for not being wrapped (UNMUTATED_MACROS) — so that an example that uses a new macro
    for the first time does not stay green outside the mutation and miss a print of the value's shape. The rows of unwrapped macros name only macros that current examples use. Use sites are read by the names in `(require doeff-hy.macros [name …])`
    (the `*`, `:macros`, and alias spellings cannot be read — a remaining limitation)."""
    used = {use.macro for name in _examples() for use in _example_macro_uses(name)}
    assert used - set(MUTATED_MACROS) - set(UNMUTATED_MACROS) == set(), (
        "a doeff-hy macro an example uses is not in the expansion-mutation tables — if it is an expression that builds a runtime value (a Traverse effect, a Program), "
        "add it to MUTATED_MACROS and check that the mutation entry point can wrap it. If it is a name-binding or defining statement, put it in UNMUTATED_MACROS with a reason"
    )
    assert set(UNMUTATED_MACROS) - used == set(), (
        "a UNMUTATED_MACROS row does not name a macro of the current examples — remove the row"
    )
    assert set(UNMUTATED_MACROS) & set(MUTATED_MACROS) == set(), (
        "a UNMUTATED_MACROS row is also in MUTATED_MACROS — if it is wrapped, remove the row"
    )


def test_examples_dir_holds_only_examples_and_their_expected_outputs() -> None:
    """The files under examples/ are only the examples (.hy) directly under it and the expected outputs (.txt) in expected/ — if a lib is placed in a subdirectory, then even if an
    example prints the shape of a value that lib built, the test does not count the lib as an example (the mutation applies only to the example module's expansion) and cannot tell."""
    stray = sorted(
        str(path.relative_to(EXAMPLES))
        for path in EXAMPLES.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and not (path.parent == EXAMPLES and path.suffix == ".hy")
        and not (path.parent == EXPECTED and path.suffix == ".txt")
    )
    assert stray == [], (
        f"under examples/ there is a file that is neither an example nor an expected output — if a helper lib for an example is placed there, the shape of the value that lib built with a macro is not hit by the example test's"
        f" mutation. Write the lib inside the example, or put it in a package users use: {stray}"
    )


@dataclass(frozen=True)
class BuiltValue:
    """One use site of a macro that the mutation wraps: the expression that puts the value the expansion builds (for defk and fnk, the value they return) in `built`, and the answer it produced when run."""

    # The definition placed before the expression (such as a defk definition)
    setup: str
    # The expression put in `built`
    expression: str
    # The answer it produced when run (the print of the value after run)
    answer: str


# Use sites per macro that the mutation wraps: an expression that gives the same answer even when run under the mutation.
BUILT_VALUES: dict[str, BuiltValue] = {
    "traverse": BuiltValue("", "(traverse (<- x (Iterate [1 2 3])) (* x 2))", "[2, 4, 6]"),
    "for/do": BuiltValue("", "(for/do (<- x (From [1 2 3])) (* x 2))", "[2, 4, 6]"),
    "fnk": BuiltValue("", "((fnk [x] (* x 2)) 21)", "42"),
    "defk": BuiltValue(
        "(defk twice [x] {:pre [(: x int)] :post [(: % int)]} (* x 2))", "(twice 21)", "42"
    ),
}

# An example for checking the mutation: puts the value the expansion built in `built`, and prints its shape (class name, attributes) and the answer after run.
_REQUIRE_BUILDERS = "(require doeff-hy.macros [<- defk fnk traverse for/do])"
_BUILT_VALUE_PREAMBLE = f"""
{_REQUIRE_BUILDERS}
(import doeff [do :as _doeff-do])
(import doeff [run])
(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects._scheduler [scheduled])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse._handlers [sequential fail-handler :as fail_handler])

(defn answer [built]
  (setv outcome (run (scheduled (sequential (fail_handler (try_handler built))))))
  (if (hasattr outcome "valid_values") outcome.valid_values outcome))
"""


def _built_value_example(uses: list[BuiltValue]) -> str:
    """The source of an example that prints, per expression, the class name of the value the expression built and the answer after run."""
    return _BUILT_VALUE_PREAMBLE + "".join(
        f"""
{use.setup}
(setv built_{index} {use.expression})
(print "built:" (. (type built_{index}) __name__))
(print "answer:" (answer built_{index}))
"""
        for index, use in enumerate(uses)
    )


def _lines(result: subprocess.CompletedProcess[str], kind: str) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith(f"{kind}:")]


def test_expansion_mutation_changes_the_built_value_not_the_answer(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    """The current examples do not print the shape of the value the expansion built, so that the mutation does not miss (the value built becomes a WithHandler for each wrapped macro), that it does not
    change the answer (the answer after run is the same), and that it names an example that prints the shape of the value (the shape of ex10, and the shape of the value
    returned by calling a function defined by defk) are verified with a source that prints the shape of the value. The other tests of the example — runs, runs a Program, macro owner, things not placed in the expected output — pass in this form (it is not a copy of an expansion string,
    so the mark test cannot tell). It decides only the shape after the mutation, and not what value the macro currently builds (a Traverse or a Program)
    — this test does not change on a macro change that does not alter the answer."""
    assert set(BUILT_VALUES) == set(MUTATED_MACROS), (
        "for each MUTATED_MACROS macro, add a use site whose value shape changes under the mutation to BUILT_VALUES"
    )
    script = tmp_path / "prints_built_values.hy"
    script.write_text(_built_value_example(list(BUILT_VALUES.values())), encoding="utf-8")
    assert _macro_violations(script.read_text(encoding="utf-8"), str(script)) == []
    plain = _run_example(script, None, deadline_scale_factor)
    mutated = _run_example(script, MUTATED_EXPANSION, deadline_scale_factor)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert mutated.returncode == 0, mutated.stdout + mutated.stderr
    # The expected output the writer places = the output from running. Things not to be placed are not included
    assert _unplaceable_in(plain.stdout) == []
    assert _lines(mutated, "built") == ["built: WithHandler"] * len(BUILT_VALUES)
    answers = [f"answer: {use.answer}" for use in BUILT_VALUES.values()]
    assert _lines(plain, "answer") == _lines(mutated, "answer") == answers
    change = _expansion_mutation_change(script, plain.stdout, mutated)
    assert change is not None
    assert script.name in change
    assert "+built: WithHandler" in change


def test_expansion_mutation_names_an_example_that_breaks_on_the_built_value(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    """An example that reads an attribute of the value the expansion built fails under the mutation — the test names the failure and that example, not an output difference."""
    script = tmp_path / "reads_built_value.hy"
    script.write_text(
        _BUILT_VALUE_PREAMBLE
        + f"(setv built {BUILT_VALUES['traverse'].expression})\n(print (len (list (. built items))))\n",
        encoding="utf-8",
    )
    mutated = _run_example(script, MUTATED_EXPANSION, deadline_scale_factor)
    change = _expansion_mutation_change(script, "", mutated)
    assert change is not None
    assert script.name in change
    assert "failed" in change
    assert "no attribute 'items'" in change


def test_expansion_mutation_spares_the_modules_an_example_imports(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    """The mutation applies only to the expansion of the example's module — even if a lib `.hy` the example imports uses the same macro, its expansion is not changed (this prevents the bytecode of a lib compiled
    with the changed expansion from being written to the store shared by the machine and a run unaware of the mutation from reading the wrapped expansion)."""
    (tmp_path / "built_by_a_library.hy").write_text(
        _BUILT_VALUE_PREAMBLE
        + "".join(
            f"{use.setup}\n(setv library_{index} {use.expression})\n"
            for index, use in enumerate(BUILT_VALUES.values())
        ),
        encoding="utf-8",
    )
    script = tmp_path / "prints_library_and_own_values.hy"
    script.write_text(
        _BUILT_VALUE_PREAMBLE
        + "(import built_by_a_library)\n"
        + f"(setv own_value {BUILT_VALUES['traverse'].expression})\n"
        + "".join(
            f'(print "library:" (. (type built_by_a_library.library_{index}) __name__))\n'
            for index in range(len(BUILT_VALUES))
        )
        + '(print "own:" (. (type own_value) __name__))\n',
        encoding="utf-8",
    )
    plain = _run_example(script, None, deadline_scale_factor)
    mutated = _run_example(script, MUTATED_EXPANSION, deadline_scale_factor)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert mutated.returncode == 0, mutated.stdout + mutated.stderr
    assert _lines(mutated, "own") == ["own: WithHandler"]
    # The value the lib built is not wrapped (even under the mutation, the same value as a normal run)
    assert _lines(mutated, "library") == _lines(plain, "library")
    assert len(_lines(plain, "library")) == len(BUILT_VALUES)


def test_expansion_mutation_follows_the_macro_not_how_the_example_requires_it(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    """The mutation hits the macro table, so an example is wrapped even if it requires a macro under an alias (the check that reads the require spelling reads an alias as the name of a different macro —
    a remaining limitation of test_example_macros_are_mutated_or_named)."""
    script = tmp_path / "requires_by_alias.hy"
    script.write_text(
        _BUILT_VALUE_PREAMBLE.replace(
            _REQUIRE_BUILDERS, "(require doeff-hy.macros [<- traverse :as aliased_traverse])"
        )
        + "(setv own_value (aliased_traverse (<- x (Iterate [1 2 3])) (* x 2)))\n"
        + '(print "own:" (. (type own_value) __name__))\n',
        encoding="utf-8",
    )
    mutated = _run_example(script, MUTATED_EXPANSION, deadline_scale_factor)
    assert mutated.returncode == 0, mutated.stdout + mutated.stderr
    assert mutated.stdout == "own: WithHandler\n"


def test_mark_check_sees_unknown_marks() -> None:
    """All the marks in the current expected outputs have fixed forms, so that a mark not of that form is caught and that a mark swallows only what is fixed is verified with a statement that
    contains them."""
    expected = f"Success: {ANY}\nasync: {ANY} in {ANY}s\nsync: 550 in {ANY}s\n{ANY}\n"
    assert _mark_violations(expected) == [f"1: Success: {ANY}", f"2: async: {ANY} in {ANY}s"]
    assert _matches(f"sync: 550 in {ANY}s\n", "sync: 550 in 1.02s\n")
    # A seconds mark swallows only a number of seconds and does not cross lines
    assert not _matches(f"sync: {ANY}s\n", "sync: 550 in 1.02s\n")
    assert not _matches(f"sync: 550 in {ANY}s\n", "sync: 550 in 1.02\n  [2] OK: 3s\n")


def test_frame_mark_swallows_only_frames() -> None:
    """The line-only ANY in the current examples hides only frames, so that swallowing an item-result line is caught is verified with an output that contains it."""
    frame = (
        f'        File "{Path(__file__)}", line 1, in <module>\n    import difflib\n    ^^^^^^\n'
    )
    traceback = f"  [1] FAILED: boom\n      Traceback (most recent call last):\n{frame}"
    actual = f"{traceback}      KeyError: 'boom'\n  [2] OK: 2\n  [3] OK: 3\n"
    assert (
        _compare(
            f"  [1] FAILED: boom\n      Traceback (most recent call last):\n{ANY}\n      KeyError: 'boom'\n  [2] OK: 2\n  [3] OK: 3\n",
            actual,
        )
        == []
    )
    # Swallowing too much: an expected output that hides even the exception line and the item [2] line
    swallowing = (
        f"  [1] FAILED: boom\n      Traceback (most recent call last):\n{ANY}\n  [3] OK: 3\n"
    )
    assert _compare(swallowing, actual) == ["      KeyError: 'boom'", "  [2] OK: 2"]
    assert not _matches(swallowing, actual)
    # A frame sequence starts with a File line
    assert _frame_violations(["  [0] OK: 1", *frame.rstrip("\n").split("\n")]) == ["  [0] OK: 1"]


def test_unplaceable_table_catches_its_samples() -> None:
    """The current expected outputs have nothing not to be placed, so for each table row, a statement containing that thing is caught and a statement turned into a mark is not caught, verified here."""
    expansion_marks = "expansion-shape marks (names hidden by Hy and doeff-hy, __import__ in an expansion)"
    samples = {
        "checkout path": f"{EXAMPLES / 'ex07_proboscis_ema_rewrite.hy'}:129",
        "machine locations (venv, Python, home)": f"{Path(sys.base_prefix) / 'lib' / 'traceback.py'}",
        expansion_marks: "raised in: _hy_anon_17:129 -> fetch_price:95",
        "traceback frame lines": '        File "/x/doeff_core_effects/_handlers.py", line 154, in attempt',
    }
    assert set(samples) == set(UNPLACEABLE), "for each UNPLACEABLE row, add the statement to catch here"
    assert [name for name, sample in samples.items() if not UNPLACEABLE[name](sample)] == []
    # Copies of an expansion: gensyms, doeff-hy helper names (even in Hy spelling), bookkeeping attributes, the __import__ in fnk's expansion. Copies of old expansions
    # (the form that the removed ex04_debug2 printed as "the shape a macro builds") are caught too
    assert [
        copy
        for copy in [
            "(setx _hy_gensym_bound_2 x)",
            "(_doeff_traverse_Traverse (_doeff_do (fn [x] …)) items)",
            "(import doeff [do :as _doeff-do])",
            "handler.__doeff_body__",
            '(fn [acc x] (((. (__import__ "doeff.do" :fromlist #("do")) do) (fn [] (do (+ acc x))))))',
            "(fn [x] (_doeff_do (fn [] …)))",
        ]
        if not UNPLACEABLE[expansion_marks](copy)
    ] == []
    # Names of an example's results (names with _ inside, dunders) are not caught
    assert not UNPLACEABLE[expansion_marks](
        "fetch_price: _private_total=3 my_doeff_do __init__ __class__ _"
    )
    masked = (
        f"raised in: {ANY}:129 -> fetch_price:95\n      Traceback (most recent call last):\n{ANY}\n"
    )
    assert _unplaceable_in(masked) == []
