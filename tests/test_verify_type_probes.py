"""型の probe の検の道具 tools/verify_type_probes.py が、印と違う行と埋まった抜けを名指す事の失敗ケース(card ki-d2c901bc9a2d)。

道具は Python の版ごとに、核の 4 個だけの新しい venv を作って pyright と mypy を起こす(doeff-vm を組むので 1 版 約 1 分かかり、
開発の環境の検には入れない — CI の workflow Type probes が走らせる)。道具の判じ方が黙って緑を返すと、直した型の抜けが戻っても
道具は落ちない。ここでは印の読みと判じ方を、型の道具の答えの写しで確かめる。
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
# 型の報告の節「抜け」の行の数(抜けの印の番号は 1 から この数まで)。
REPORTED_GAPS = 11


def _load(name: str) -> ModuleType:
    """tools/ の module を読む(tools/ は package ではない)。道具は隣の module を名で import するので、sys.modules に名で置く。"""
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
        ("# expect-error (引数の型違い)", tool.Expectation(BOTH)),
        ("# expect-error[pyright] (付け忘れ)", tool.Expectation(PYRIGHT)),
        ("# expect-error[mypy, pyright]", tool.Expectation(BOTH)),
        ("# gap-7 want-error (effect に当たらない注記)", tool.Expectation(frozenset(), 7, BOTH)),
        ("# gap-11 want-error[mypy]", tool.Expectation(PYRIGHT, 11, MYPY)),
        ("# gap-4 want-clean", tool.Expectation(BOTH, 4, BOTH)),
        ("# gap-5 want-clean[mypy] (型の無い関数の呼び)", tool.Expectation(MYPY, 5, MYPY)),
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
    # 黙って印の無い行(誤り無し)として読むと、守っていない行ができる。
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
    # 直した抜け(handler を付けても結果の型を保つ など)が戻ると、その行の誤りが消える。
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
    # gap-11 want-error[mypy] は「pyright は今も誤りを出す」も言う — pyright の誤りが消えたら、埋まったのでなく戻った事。
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
    # 印も assert_type も無い probe は import の誤りしか見ない — import を見る probe は名で言う。
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
    """install と install_problems の呼びを見た後で、check_python を止める合図。"""


def test_the_tool_installs_the_checkout_through_the_core_install_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """道具が verify_core_install の install・install_problems を今の引数で呼ぶ事。

    81aa4fd9d が両方に引数 source(この checkout か PyPI か)を足した後も、道具は古い 2 引数で呼んでいた。どの版でも venv を
    作る前に TypeError で落ち、CI の workflow Type probes は probe を 1 つも見ずに赤だった。ここでは本物の install を偽に替え、
    道具がこの checkout(Checkout)を渡す事を確かめる。"""
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
