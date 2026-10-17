"""核の 4 個だけを入れた新しい venv で、型の probe(tests/type_probes/)と README の Python の例を pyright と mypy にかけ、
誤りの出る行が probe の印の通りである事を確かめる(取り組み doeff-publish の区切り 12・card ki-d2c901bc9a2d)。

公開の前の型の報告(使う人の型の check で doeff の型がどこまで効くか)は、核だけの venv に 25 個の probe を当てて効く所と
抜けを数え、抜けを card に切った。開発の環境の型の検(tests/test_static_typing.py ほか)は workspace の editable な入れ方と
repo の pyright の設定で読むので、直した抜けが使う人の入れ方(wheel・extra 無し)で戻っても気づけない。この道具は使う人と
同じ入れ方と、道具の既定の探し方で確かめる:

    uv run --no-project python tools/verify_type_probes.py                  # 既定 3.11 と 3.14+gil
    uv run --no-project python tools/verify_type_probes.py --python 3.12    # --python は何度でも書ける

Python の版ごとに:

1. 新しい venv に核の 4 個だけを入れる(tools/verify_core_install.py の install — 入った物がちょうど核の 4 個かも見る)。
2. probe を pyright(strict)と mypy --strict にかけ、行ごとに誤りが出るかを probe の印と照らす。
3. doeff と doeff-vm の README(wheel の metadata の本文)の Python の例を pyright(standard)と mypy(既定)にかけ、誤りが 1 つも
   無い事を確かめる — README を写した人の editor が、既定の強さで赤を出さない事。

pyright と mypy の版は uv.lock の版に固定する(版が上がると誤りの出方が変わる — uv.lock の版を上げる変更で、この道具も走らせる)。

probe の印(行の末の注釈 — 印の無い行は、両方の道具が誤りを出さない事を言う):

    # expect-error              両方の道具が、この行に誤りを出す
    # expect-error[pyright]     pyright だけが出す(mypy に同じ規則が無い)。[mypy] も同じ形
    # gap-N want-error          望みは誤り。今は両方とも出さない — 型の報告の抜け N。[道具] を付けると、その道具だけが今は出さない
    # gap-N want-clean          望みは誤り無し。今は両方とも出す。[道具] も同じ形

型は typing.assert_type で書く(型が違えば、その行の誤りになる)。file の頭の `# pyright: <強さ>` と `# mypy: <設定>` は、その
file だけの強さ(注記の無い README の書き方の probe など)。抜けの印の行で、名指した道具が望みの通りになったら落とす — 抜けを
埋めた変更が、印を expect-error か印無しに替えて守りに入れる。

CI の workflow Type probes(.github/workflows/type-probes.yml)が走らせる。

終了コード: 全部通れば 0・確かめが落ちれば 1・道具が使えなければ(venv・install・型の道具の失敗)2。標準ライブラリだけを使う。
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

# 同じ dir の module — script として走らせると、Python が script の dir を sys.path の頭に置く。
import verify_core_install

REPO = verify_core_install.REPO
PROBES = REPO / "tests" / "type_probes"
LOCK = REPO / "uv.lock"
PYRIGHT = "pyright"
MYPY = "mypy"
CHECKERS = (PYRIGHT, MYPY)
DEFAULT_PYTHONS = verify_core_install.DEFAULT_PYTHONS
# 子の process 1 つの制限時間(秒)。pyright の初回は、node と pyright の本体の取り寄せを含む。
CHILD_TIMEOUT_SECONDS = 300
# 印の形(# の後ろ)。読めない印は、黙って「印無し」にせず落とす — 綴りを誤った印が、守っていない行を作らないため。
_MARK = re.compile(
    r"(?P<kind>expect-error|gap-(?P<gap>\d+) want-(?P<want>error|clean))(?:\[(?P<checkers>[^\]]*)\])?"
)
# 印に見える語(_MARK に合わない時、読めない印として落とす)。
_MARK_WORDS = re.compile(r"\b(?:expect-\w+|gap-\w+)")
_MYPY_ERROR = re.compile(r"^(?P<file>.+?\.pyi?):(?P<line>\d+):(?:\d+:)? error: (?P<message>.*)$")


@dataclass(frozen=True)
class Expectation:
    """probe の 1 行の印 — 今この行に誤りを出す道具と、抜けの行ならその番号と望み。"""

    # 今この行に誤りを出す道具の名
    erring: frozenset[str]
    # 型の報告の抜けの番号(抜けでない行は None)
    gap: int | None = None
    # 抜けが今も開いている道具 — この道具が望みの通りになったら、抜けが埋まった事として落とす
    open_in: frozenset[str] = frozenset()


# 印の無い行の印 — 両方の道具が誤りを出さない。
CLEAN = Expectation(frozenset())


@dataclass(frozen=True)
class MarkedLine:
    """probe の印の在る行 1 つ。"""

    # 確かめの dir からの path
    file: str
    # 1 から数える行
    line: int
    expectation: Expectation


@dataclass(frozen=True)
class Marks:
    """probe の印 — 印の在る行と、読めなかった印を言う文。"""

    lines: tuple[MarkedLine, ...]
    problems: tuple[str, ...]


def _read_mark(comment: str, file: str, line: int) -> Marks:
    """注釈 1 つの印を読む(印が無ければ空)。"""
    where = f"{file}:{line}"
    found = _MARK.search(comment)
    if found is None:
        words = _MARK_WORDS.findall(comment)
        return Marks((), (f"{where}: 読めない印: {' '.join(words)}",) if words else ())
    named = (
        frozenset(CHECKERS)
        if found["checkers"] is None
        else frozenset(name.strip() for name in found["checkers"].split(","))
    )
    if not named <= frozenset(CHECKERS):
        return Marks(
            (),
            (f"{where}: 印の [道具] に知らない名が在る: [{found['checkers']}](書けるのは {'・'.join(CHECKERS)})",),
        )
    if found["gap"] is None:
        return Marks((MarkedLine(file, line, Expectation(named)),), ())
    # 名指した道具は今は望みと逆。名指さない道具は今も望みの通り。
    erring = frozenset(CHECKERS) - named if found["want"] == "error" else named
    return Marks((MarkedLine(file, line, Expectation(erring, int(found["gap"]), named)),), ())


def read_marks(text: str, file: str) -> Marks:
    """probe の本文から、行ごとの印を読む(文字列の中の # は読まない)。"""
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
    """file の行 1 つの印(印の無い行は CLEAN)。"""
    return next(
        (marked.expectation for marked in marks if (marked.file, marked.line) == (file, line)),
        CLEAN,
    )


@dataclass(frozen=True)
class Finding:
    """型の道具の誤り 1 つ。"""

    checker: str
    # 確かめの dir からの path
    file: str
    # 1 から数える行
    line: int
    message: str


@dataclass(frozen=True)
class Checked:
    """型の道具 1 回の結末 — 誤りと、道具が読んだ file の数(数えられなければ None)。"""

    findings: tuple[Finding, ...]
    files: int | None


def pyright_findings(report: object, directory: Path) -> Checked:
    """pyright --outputjson の答えから、誤り(severity が error の物)と読んだ file の数を取る。"""
    match report:
        case {"generalDiagnostics": list(diagnostics), "summary": {"filesAnalyzed": int(analyzed)}}:
            pass
        case _:
            raise TypeError(f"pyright の答えの形が違う: {report!r}")
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
    """mypy の出力から、誤りの行(note は除く)と読んだ file の数を取る。"""
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
    """誤りを印と照らした結末 — 印と違う行を言う文と、今も開いている抜けの行ごとの番号。"""

    failures: tuple[str, ...]
    open_gaps: tuple[int, ...]


def judge(marks: Sequence[MarkedLine], findings: Sequence[Finding]) -> Judgement:
    """誤りを印と照らす — 印の在る行と誤りの出た行の全部を、道具ごとに見る(印の無い行は CLEAN)。"""
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
    """行 1 つを道具ごとに印と照らす。抜けの行が印の通りなら、開いている抜けとして数える。"""
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
    """道具 1 つの行の結末が印と違う事を言う文(合えば None)。"""
    errs = bool(messages)
    if errs == (checker in expectation.erring):
        return None
    said = f" — {checker} の誤り: {' / '.join(messages)}" if messages else ""
    if expectation.gap is not None and checker in expectation.open_in:
        return (
            f"型の報告の抜け {expectation.gap} が {checker} で埋まった — 印を替えて守りに入れる"
            f"(望みの通り {'誤りになった' if errs else '誤りが消えた'}){said}"
        )
    wanted = "誤りを出す" if not errs else "誤りを出さない"
    return f"{checker} が印と違う — 印は「{wanted}」{said}"


@dataclass(frozen=True)
class Suite:
    """型の道具にかける file の組 1 つ。"""

    # 印字の名
    name: str
    directory: Path
    # dir からの path
    files: tuple[str, ...]
    # pyright の typeCheckingMode
    pyright_mode: str
    # mypy の強さの引数(既定は空)
    mypy_arguments: tuple[str, ...]
    # 印の在る行(印の無い行は、両方の道具が誤りを出さない)
    marks: tuple[MarkedLine, ...]


@dataclass(frozen=True)
class Versions:
    """型の道具の版(uv.lock の版)。"""

    pyright: str
    mypy: str


def locked_version(lock: str, name: str) -> str:
    """uv.lock の本文から、配布物 1 つの版を読む(版が 1 つに決まらなければ落とす)。"""
    versions = {
        package["version"] for package in tomllib.loads(lock)["package"] if package["name"] == name
    }
    if len(versions) != 1:
        raise ValueError(f"uv.lock の {name} の版が 1 つに決まらない: {sorted(versions)}")
    return versions.pop()


def checker_command(name: str, version: str) -> tuple[str, ...]:
    """型の道具を版を固定して起こす命令 — venv の外で uv tool run が起こし、核の venv は引数で指す。"""
    return ("uv", "tool", "run", "--from", f"{name}=={version}", name)


def locked_versions(lock: str) -> Versions:
    """uv.lock の本文から、型の道具の版を読む。"""
    return Versions(pyright=locked_version(lock, PYRIGHT), mypy=locked_version(lock, MYPY))


def run_suite(suite: Suite, python: Path, versions: Versions, timeout: float) -> tuple[Finding, ...]:
    """組 1 つを pyright と mypy にかけ、誤りを返す(道具が全部の file を読まなければ、道具が使えない事として落とす)。"""
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
            1, [checker], output, f"{checker} が読んだ file が {result.files} 個で、書いた {len(suite.files)} 個と違う"
        )
        for checker, result, output in checked
        if result.files != len(suite.files)
    ]
    if unread:
        raise unread[0]
    return tuple(finding for _, result, _ in checked for finding in result.findings)


def check_suite(suite: Suite, python: Path, versions: Versions, timeout: float) -> tuple[str, ...]:
    """組 1 つを型の道具にかけて印と照らし、結末を印字して、印と違う行を言う文の列を返す。"""
    findings = run_suite(suite, python, versions, timeout)
    judged = judge(suite.marks, findings)
    verify_core_install.print_result(
        f"{suite.name}(pyright {suite.pyright_mode}・mypy {' '.join(suite.mypy_arguments) or '既定'})"
        f" — file {len(suite.files)} 個・印の行 {len(suite.marks)}・誤り {len(findings)}",
        "\n".join(judged.failures) or None,
    )
    if judged.open_gaps:
        gaps = "・".join(
            f"抜け {gap} が {count} 行" for gap, count in sorted(Counter(judged.open_gaps).items())
        )
        print(f"        開いている抜け(印の通り): {gaps}", flush=True)
    return judged.failures


def probe_marks() -> Marks:
    """tests/type_probes の probe の全部の印。"""
    reads = tuple(
        read_marks(probe.read_text(encoding="utf-8"), probe.name) for probe in probe_files()
    )
    return Marks(
        tuple(marked for read in reads for marked in read.lines),
        tuple(problem for read in reads for problem in read.problems),
    )


def probe_files() -> tuple[Path, ...]:
    """tests/type_probes の probe(名の順)。"""
    return tuple(sorted(PROBES.glob("*.py")))


def probe_suite(directory: Path, marks: Marks) -> Suite:
    """tests/type_probes の probe を dir に写し、strict の組を返す。"""
    directory.mkdir(parents=True)
    probes = probe_files()
    for probe in probes:
        shutil.copyfile(probe, directory / probe.name)
    files = tuple(probe.name for probe in probes)
    return Suite("型の probe", directory, files, "strict", ("--strict",), marks.lines)


def readme_suite(installed: verify_core_install.Installed, package: str, directory: Path) -> Suite:
    """README 1 つの Python の例(核だけで走る物)を dir に書き、既定の強さの組を返す — 例に印は無い(全部の行が誤り無し)。"""
    directory.mkdir(parents=True)
    paths = tuple(
        verify_core_install.write_example(example, directory)
        for example in verify_core_install.type_checked_examples(installed, package)
    )
    files = tuple(path.relative_to(directory).as_posix() for path in paths)
    return Suite(f"{package} の README の例", directory, files, "standard", (), ())


def check_python(
    python_request: str, work: Path, versions: Versions, timeout: float
) -> tuple[str, ...]:
    """Python の版 1 つで、新しい venv に核だけを入れ、probe と README の例を型の道具にかけて、落ちた事を言う文の列を返す。"""
    place = work / f"py{python_request.replace('+', '-')}"
    place.mkdir(parents=True)
    print(f"\n== Python {python_request}: 新しい venv に doeff を extra 無しで入れる", flush=True)
    installed = verify_core_install.install(python_request, place)
    build_kind = "free-threading" if installed.free_threaded else "GIL"
    print(f"  interpreter {installed.version}({build_kind})・{installed.python}", flush=True)
    problems = verify_core_install.install_problems(installed)
    verify_core_install.print_result(
        "入った物がちょうど核の 4 個で、doeff の 3 個はこの checkout から組んだ wheel",
        "\n".join(problems) or None,
    )
    marks = probe_marks()
    verify_core_install.print_result("probe の印が読める", "\n".join(marks.problems) or None)
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
    """Python の版 1 つの結末。"""

    # uv に渡した Python の指定
    python: str
    failures: tuple[str, ...]


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """引数を読む — Python の版と、作業の dir を残すか。"""
    parser = argparse.ArgumentParser(
        description=(
            "核の 4 個だけを入れた新しい venv で、型の probe と README の Python の例を pyright と mypy にかけ、"
            "誤りの出る行が probe の印の通りである事を確かめる。"
        ),
    )
    parser.add_argument(
        "--python",
        action="append",
        dest="pythons",
        metavar="REQUEST",
        help=f"uv に渡す Python の指定(何度でも書ける・既定 {' と '.join(DEFAULT_PYTHONS)})",
    )
    parser.add_argument("--keep", action="store_true", help="終わった後も作業の dir を消さない")
    parser.add_argument(
        "--timeout",
        type=float,
        default=CHILD_TIMEOUT_SECONDS,
        help="子の process 1 つの制限時間(秒・既定 %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """入口 — Python の版ごとに確かめ、結末を印字して終了コードを返す。"""
    verify_core_install.print_in_utf8()
    arguments = _parse(argv)
    pythons = tuple(arguments.pythons or DEFAULT_PYTHONS)
    if shutil.which("uv") is None:
        print("uv が PATH に無い — https://docs.astral.sh/uv/ の手順で入れる", file=sys.stderr)
        return 2
    versions = locked_versions(LOCK.read_text(encoding="utf-8"))
    print(f"型の道具: pyright {versions.pyright}・mypy {versions.mypy}(uv.lock の版)", flush=True)
    # repo の外(型の道具が repo の pyproject.toml・pyrightconfig.json を読まず、例の import が checkout の doeff を拾わない)。
    work = Path(tempfile.mkdtemp(prefix="doeff-type-probes-"))
    print(f"作業の dir: {work}", flush=True)
    try:
        results = tuple(
            PythonResult(python, check_python(python, work, versions, arguments.timeout))
            for python in pythons
        )
    except subprocess.CalledProcessError as failed:
        print(
            f"\nvenv・install・型の道具が落ちた(確かめまでは進んでいない・終了コード {failed.returncode}): "
            f"{' '.join(map(str, failed.cmd))}",
            file=sys.stderr,
        )
        for output in (failed.stdout, failed.stderr):
            if output:
                print(output, file=sys.stderr)
        return 2
    finally:
        if arguments.keep:
            print(f"\n作業の dir を残した: {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print("\n== 結末", flush=True)
    for result in results:
        print(
            f"  Python {result.python}: "
            f"{'通った' if not result.failures else f'NG {len(result.failures)} 個'}",
            flush=True,
        )
    passed = not any(result.failures for result in results)
    print("\n全部通った" if passed else "\n落ちた所が在る — 上の NG の行を読む", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
