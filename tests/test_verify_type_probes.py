"""Failure cases: the type-probe check tool tools/verify_type_probes.py names lines that differ from their marks and gaps that have been closed.

For each Python version the tool creates a fresh venv with only the 4 core packages and launches pyright and mypy (it builds doeff-vm, so it takes about 1 minute per version, and
it is not part of the development-environment checks -- the CI workflow Type probes runs it). If the tool's judgement silently returned green, the tool would not fail even if a fixed type gap came back.
Here the reading of marks and the judgement are verified with copies of the type tools' answers.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "type-probes.yml"
# The number of "gap" lines in the type report section (gap mark numbers run from 1 up to this number).
REPORTED_GAPS = 11


def _load(name: str) -> ModuleType:
    """Load a module from tools/ (tools/ is not a package). The tool imports adjacent modules by name, so place it in sys.modules by name."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("readme_examples")
_load("verify_release_install")
_load("verify_core_install")
tool = _load("verify_type_probes")

BOTH = frozenset({"pyright", "mypy"})
PYRIGHT = frozenset({"pyright"})
MYPY = frozenset({"mypy"})


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("# expect-error (argument type mismatch)", tool.Expectation(BOTH)),
        ("# expect-error[pyright] (forgot to attach)", tool.Expectation(PYRIGHT)),
        ("# expect-error[mypy, pyright]", tool.Expectation(BOTH)),
        ("# gap-7 want-error (annotation that does not apply to the effect)", tool.Expectation(frozenset(), 7, BOTH)),
        ("# gap-11 want-error[mypy]", tool.Expectation(PYRIGHT, 11, MYPY)),
        ("# gap-4 want-clean", tool.Expectation(BOTH, 4, BOTH)),
        ("# gap-5 want-clean[mypy] (call of an untyped function)", tool.Expectation(MYPY, 5, MYPY)),
    ],
    ids=["both", "pyright-only", "both-named", "gap-error", "gap-error-mypy", "gap-clean", "gap-clean-mypy"],
)
def test_a_marker_says_which_checker_errs_on_the_line_now(
    comment: str, expected: object
) -> None:
    marks = tool.read_marks(f"x = 1\nf()  {comment}\n", "p.py")
    assert marks.problems == ()
    assert marks.lines == (tool.MarkedLine("p.py", 2, expected),)


@pytest.mark.parametrize(
    "comment",
    ["# expect-eror", "# gap-4 want-ok", "# expect-error[pyrite]", "# gap-x want-error"],
    ids=["misspelt", "unknown-want", "unknown-checker", "no-number"],
)
def test_an_unreadable_marker_is_a_problem_and_not_a_clean_line(comment: str) -> None:
    # Reading it silently as an unmarked line (no error) would create an unguarded line.
    marks = tool.read_marks(f"f()  {comment}\n", "p.py")
    assert marks.lines == ()
    assert len(marks.problems) == 1
    assert marks.problems[0].startswith("p.py:1: ")


def test_a_marker_inside_a_string_is_not_read() -> None:
    marks = tool.read_marks('text = "# expect-error"\n', "p.py")
    assert marks == tool.Marks((), ())


def _finding(checker: str, line: int, file: str = "p.py") -> object:
    return tool.Finding(checker, file, line, f"誤り {checker} {line}")


def _marked(line: int, comment: str) -> object:
    (marked,) = tool.read_marks("\n" * (line - 1) + f"f()  {comment}\n", "p.py").lines
    return marked


def test_findings_that_match_every_marker_pass_and_count_the_open_gaps() -> None:
    marks = (_marked(1, "# expect-error"), _marked(2, "# gap-7 want-error"), _marked(3, "# gap-4 want-clean"))
    findings = (_finding("pyright", 1), _finding("mypy", 1), _finding("pyright", 3), _finding("mypy", 3))
    judged = tool.judge(marks, findings)
    assert judged == tool.Judgement((), (7, 4))


def test_a_marked_error_that_a_checker_no_longer_reports_is_named() -> None:
    # When a fixed gap (such as keeping the result type even when a handler is attached) comes back, the error on that line disappears.
    judged = tool.judge((_marked(1, "# expect-error"),), (_finding("pyright", 1),))
    assert judged.failures == ("p.py:1: mypy が印と違う — 印は「誤りを出す」",)


def test_an_error_on_an_unmarked_line_is_named_with_the_message() -> None:
    judged = tool.judge((), (_finding("pyright", 5, "readme_example_74.py"),))
    assert judged.failures == (
        "readme_example_74.py:5: pyright が印と違う — 印は「誤りを出さない」 — pyright の誤り: 誤り pyright 5",
    )


@pytest.mark.parametrize(
    ("comment", "findings", "checker"),
    [
        ("# gap-7 want-error", ("pyright", "mypy"), "pyright"),
        ("# gap-4 want-clean", ("pyright",), "mypy"),
        ("# gap-11 want-error[mypy]", ("pyright", "mypy"), "mypy"),
    ],
    ids=["now-errs", "now-clean", "one-checker"],
)
def test_a_gap_that_a_checker_closed_fails_until_the_marker_is_changed(
    comment: str, findings: tuple[str, ...], checker: str
) -> None:
    judged = tool.judge((_marked(1, comment),), tuple(_finding(name, 1) for name in findings))
    closed = [failure for failure in judged.failures if f"が {checker} で埋まった" in failure]
    assert len(closed) == 1, judged.failures
    assert judged.open_gaps == ()


def test_a_checker_that_breaks_the_unnamed_side_of_a_gap_is_a_regression() -> None:
    # gap-11 want-error[mypy] also says "pyright still reports an error" -- if pyright's error disappears, it is a regression, not a closed gap.
    judged = tool.judge((_marked(1, "# gap-11 want-error[mypy]"),), ())
    assert judged.failures == ("p.py:1: pyright が印と違う — 印は「誤りを出す」",)


def test_pyright_errors_are_read_relative_to_the_checked_directory(tmp_path: Path) -> None:
    report = {
        "generalDiagnostics": [
            {
                "file": str(tmp_path / "p05.py"),
                "severity": "error",
                "message": "Argument missing\n  detail",
                "rule": "reportCallIssue",
                "range": {"start": {"line": 16, "character": 0}},
            },
            {
                "file": str(tmp_path / "p05.py"),
                "severity": "warning",
                "message": "Import could not be resolved from source",
                "rule": "reportMissingModuleSource",
                "range": {"start": {"line": 2, "character": 0}},
            },
        ],
        "summary": {"filesAnalyzed": 1},
    }
    checked = tool.pyright_findings(report, tmp_path)
    assert checked == tool.Checked(
        (tool.Finding("pyright", "p05.py", 17, "Argument missing  [reportCallIssue]"),), 1
    )


def test_a_pyright_answer_of_another_shape_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="pyright の答えの形が違う"):
        tool.pyright_findings({"generalDiagnostics": []}, tmp_path)


def test_mypy_errors_are_read_without_notes_and_with_the_checked_count() -> None:
    output = (
        'p10.py:32: error: Cannot infer value of type parameter "_T" of "Gather"  [misc]\n'
        'p10.py:32: note: "Gather" defined here\n'
        "readme/a.py:3:5: error: Need type annotation  [var-annotated]\n"
        "Found 2 errors in 2 files (checked 25 source files)\n"
    )
    assert tool.mypy_findings(output) == tool.Checked(
        (
            tool.Finding("mypy", "p10.py", 32, 'Cannot infer value of type parameter "_T" of "Gather"  [misc]'),
            tool.Finding("mypy", "readme/a.py", 3, "Need type annotation  [var-annotated]"),
        ),
        25,
    )
    assert tool.mypy_findings("Success: no issues found in 7 source files\n").files == 7
    assert tool.mypy_findings("mypy: error: no such file\n").files is None


def test_every_probe_marker_is_readable_and_every_probe_checks_something() -> None:
    marks = tool.probe_marks()
    assert marks.problems == ()
    probes = tool.probe_files()
    assert len(probes) >= 25
    marked = {marked.file for marked in marks.lines}
    # A probe with neither marks nor assert_type sees only import errors -- a probe that looks at imports says so by name.
    unchecked = [
        probe.name
        for probe in probes
        if probe.name not in marked
        and "assert_type(" not in probe.read_text(encoding="utf-8")
        and "_imports_" not in probe.name
    ]
    assert unchecked == []


def test_every_gap_marker_names_a_row_of_the_type_report() -> None:
    gaps = {marked.expectation.gap for marked in tool.probe_marks().lines} - {None}
    assert gaps
    assert all(1 <= gap <= REPORTED_GAPS for gap in gaps), gaps


def test_the_checkers_are_pinned_to_the_versions_in_uv_lock() -> None:
    versions = tool.locked_versions((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d+(\.\d+)+", versions.pyright)
    assert re.fullmatch(r"\d+(\.\d+)+", versions.mypy)
    assert tool.checker_command("pyright", versions.pyright)[-3:] == (
        "--from",
        f"pyright=={versions.pyright}",
        "pyright",
    )


def test_a_lock_with_two_versions_of_a_checker_is_refused() -> None:
    lock = (
        '[[package]]\nname = "mypy"\nversion = "2.4.0"\n\n'
        '[[package]]\nname = "mypy"\nversion = "2.3.0"\n'
    )
    with pytest.raises(ValueError, match="1 つに決まらない"):
        tool.locked_version(lock, "mypy")


def test_ci_runs_the_tool_on_both_ends_of_requires_python() -> None:
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["type-probes"]
    assert job["strategy"]["matrix"]["python"] == ["3.11", "3.14"]
    runs = [step.get("run", "") for step in job["steps"]]
    assert any(
        "tools/verify_type_probes.py --python ${{ matrix.python }}" in run for run in runs
    ), runs


class _StoppedAfterInstallError(Exception):
    """A signal to stop check_python after the calls to install and install_problems have been seen."""


def test_the_tool_installs_the_checkout_through_the_core_install_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tool calls install and install_problems of verify_core_install with the current arguments.

    After 81aa4fd9d added the argument source (this checkout or PyPI) to both, the tool was still calling with the old 2 arguments. For every version it
    failed with TypeError before creating the venv, and the CI workflow Type probes was red without seeing a single probe. Here the real install is replaced with a fake, and it is verified that
    the tool passes this checkout (Checkout)."""
    core = sys.modules["verify_core_install"]
    seen: list[tuple[str, object]] = []
    installed = core.Installed(Path("python"), "3.12.0", False, (), ())

    def fake_install(python_request: str, place: Path, source: object) -> object:
        seen.append(("install", source))
        return installed

    def fake_install_problems(given: object, source: object) -> list[str]:
        assert given is installed
        seen.append(("install_problems", source))
        raise _StoppedAfterInstallError

    monkeypatch.setattr(core, "install", fake_install)
    monkeypatch.setattr(core, "install_problems", fake_install_problems)
    with pytest.raises(_StoppedAfterInstallError):
        tool.check_python("3.12", tmp_path, tool.Versions("1.1.411", "2.4.0"), 1.0)
    assert seen == [("install", core.Checkout()), ("install_problems", core.Checkout())]
