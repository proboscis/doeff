"""核の 5 個を repo の外の新しい venv に入れ、5 個の README の例を走らせて注記と比べる(取り組み doeff-publish・card ki-7746480f388d)。

出す前(手元で build した wheel)と出した後(PyPI の版)に、同じ 1 つの命令で「核の 5 個を入れた新しい環境で README の例が
通るか」を確かめる:

    uv run --no-project python tools/verify_release_install.py                  # この checkout から build した wheel
    uv run --no-project python tools/verify_release_install.py --wheels <dir>   # 置いてある wheel(CI の build の成果物など)
    uv run --no-project python tools/verify_release_install.py --pypi 0.5.0     # PyPI の 0.5.0

Python の版ごと(既定 3.11 と 3.14+gil — uv は素の 3.14 に free-threading の 3.14t も当てるので、GIL の在る方を名指す。
--python は何度でも書け、3.14t も渡せる)に:

1. 新しい venv を repo の外の一時の dir に作る(uv は repo の pyproject.toml の [tool.uv] を読まない)。
2. 核の 5 個を入れる。wheel の時は、5 個を --no-index で wheel の dir から依存なしで入れ(PyPI に同じ名と版が在っても、5 個は
   必ず手元の wheel になる)、5 個が宣言する他の依存を PyPI から入れる。PyPI の時は 5 個を ==版 で入れる。どちらも README の例が
   使う道具(pytest)を足し、`uv pip check` で入れた物の依存に食い違いが無い事を確かめる。
3. 入れた 5 個の版を印字する。
4. 入れた 5 個の README — wheel の metadata の本文(PyPI の頁が見せる README)— の例を、README ごとに新しい dir で上から順に
   走らせ、注記と比べる(約束は tools/readme_examples.py)。例は PYTHON*・DOEFF_*・VIRTUAL_ENV を除いた環境で、Hy の
   compile の置き場も新しい dir にして走らせる(初めて入れた利用者の機体と同じに、この機体の置き場を使わない)。

終了コード: 全部通れば 0・README の例か依存の食い違いが落ちれば 1・道具が使えなければ(build・venv・install の失敗)2。
標準ライブラリだけを使う(workspace を同期せずに走る)。
"""

from __future__ import annotations

import argparse
import io
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

# 同じ dir の module — script として走らせると、Python が script の dir を sys.path の頭に置く。
import readme_examples

REPO = Path(__file__).resolve().parents[1]
# 核の 5 個(区切り ms-680daa3cc6a7 の範囲)。README を読む順でもある。
CORE = ("doeff", "doeff-vm", "doeff-core-effects", "doeff-hy", "doeff-hy-pytest")
# Python の版ごとに wheel を組む物(PyO3 の拡張 — abi3 ではない)。
NATIVE = "doeff-vm"
DEFAULT_PYTHONS = ("3.11", "3.14+gil")
# README の例が使う道具(核の 5 個の依存ではない — 読み手が自分の project に入れている物)。
EXAMPLE_TOOLS = ("pytest",)
# 例 1 つの制限時間(秒)。初めての Hy の compile を含む(新しい置き場なので、doeff_core_effects の .hy も組む)。
EXAMPLE_TIMEOUT_SECONDS = 300
# 入れた venv の中で走らせ、入れた物の様子を JSON で印字する program。
_DESCRIBE = """
import importlib.metadata as metadata, json, sys, sysconfig
names = sys.argv[1:]
print(json.dumps({
    "version": sys.version.split()[0],
    "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    "versions": {name: metadata.version(name) for name in names},
    "requires": [requirement for name in names for requirement in metadata.requires(name) or []],
    "readmes": {name: metadata.metadata(name).get_payload() for name in names},
}))
"""
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_EXTRA_MARKER = re.compile(r";.*\bextra\s*==")
_T = TypeVar("_T")


@dataclass(frozen=True)
class FromWheels:
    """wheel の dir から入れる(既に組んだ物か、この checkout から組んだ物)。"""

    directory: Path


@dataclass(frozen=True)
class FromPyPI:
    """PyPI の 5 個の同じ版を入れる。"""

    version: str


Source = FromWheels | FromPyPI


@dataclass(frozen=True)
class Installed:
    """新しい venv に入れた物の様子(venv の interpreter が JSON で答えた物を、境界で型を確かめて持つ)。"""

    # venv の interpreter
    python: Path
    # interpreter の版(例 3.11.16)
    version: str
    # free-threading の build か
    free_threaded: bool
    # 核の 5 個の名 → 入った版
    versions: dict[str, str]
    # 核の 5 個が宣言する依存(PEP 508 の文)
    requires: tuple[str, ...]
    # 核の 5 個の名 → README(metadata の本文)
    readmes: dict[str, str]


@dataclass(frozen=True)
class ReadmeResult:
    """README 1 つの例を走らせた結末。"""

    package: str
    # 走らせた例の数
    ran: int
    # 走らせて落ちた例の数
    failed: int
    # 落ちた事・約束の外の囲み・走らせる例が無い事を言う文の列
    failures: tuple[str, ...]


@dataclass(frozen=True)
class PythonResult:
    """Python の版 1 つの結末。"""

    request: str
    installed: Installed
    # `uv pip check` が落ちた時の出力(通れば None)
    dependency_conflict: str | None
    readmes: tuple[ReadmeResult, ...]

    def passed(self) -> bool:
        """この版で、依存の食い違いも README の例の落ちも無いか。"""
        return self.dependency_conflict is None and not any(
            readme.failures for readme in self.readmes
        )


def normalize(name: str) -> str:
    """配布物の名を PyPI と同じ形に揃える(PEP 503)— 依存の名と核の 5 個の名を比べるため。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def dependency_requirements(requirements: Iterable[str]) -> list[str]:
    """核の 5 個が宣言する依存のうち、PyPI から入れる物(核の 5 個と extra の依存を除く)。

    核の 5 個は手元の wheel から入れたので、ここで名指すと PyPI の同じ名の物と取り違える余地が出る。extra の依存は、
    README の例が使わない(pip install doeff では入らない)。
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
    """uv を 1 回呼ぶ(落ちたら CalledProcessError — 道具が使えない事として終了コード 2 になる)。"""
    print(f"$ uv {' '.join(arguments)}", flush=True)
    subprocess.run(["uv", *arguments], cwd=cwd, check=True, text=True)


def build(wheels: Path, pythons: Sequence[str]) -> None:
    """この checkout の核の 5 個の wheel を wheels に組む(doeff-vm は Python の版ごと)。"""
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
    """venv の interpreter に、入れた核の 5 個の版・依存・README を聞く(JSON の境界 — 欄の型をここで確かめる)。"""
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
    """JSON の値が期待の型である事を確かめる(違えば、venv の interpreter の答えが壊れている)。"""
    if not isinstance(value, kind):
        raise TypeError(f"venv の interpreter の答えに {kind.__name__} でない値が在る: {value!r}")
    return value


def _text_map(value: object) -> dict[str, str]:
    """JSON の名 → 文の対応を確かめて持つ。"""
    return {_typed(key, str): _typed(item, str) for key, item in _typed(value, dict).items()}


def _refresh() -> list[str]:
    """uv の cache に在る核の 5 個の古い写しを使わず、取り直すための引数。"""
    return [part for name in CORE for part in ("--refresh-package", name)]


def install(source: Source, python_request: str, place: Path) -> Installed:
    """place に新しい venv を作り、核の 5 個と README の例の道具を入れる。"""
    venv = place / "venv"
    _uv(["venv", "--no-project", "--python", python_request, str(venv)], place)
    python = venv_command(venv, "python")
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
    """入れた物の依存の食い違いを `uv pip check` で読む(無ければ None)— 核の 5 個の互いの版の制約も、ここで確かめる。"""
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
    """README の例を走らせる環境 — 初めて入れた利用者の機体に近づける。

    この機体の PYTHON*・DOEFF_*・VIRTUAL_ENV を除き、Hy の compile の置き場を新しい dir にする(この機体の置き場に
    既に組んだ物が在ると、初めての compile を確かめられない)。
    """
    kept = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("PYTHON", "DOEFF_")) and name != "VIRTUAL_ENV"
    }
    return {**kept, "DOEFF_HY_CODE_STORE": str(place / "hy-code-store")}


def venv_command(venv: Path, name: str) -> Path:
    """venv の中の命令 1 つ(python・doeff など)の path — Windows の venv は Scripts\\<名>.exe、他の機体は bin/<名>。"""
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def print_in_utf8() -> None:
    """道具の印字(日本語)を UTF-8 で書く — Windows の pipe(CI の log)の既定は locale の符号(cp1252 など)で、日本語を書けずに落ちる。

    子の process の環境は変えない: 子は利用者の機体と同じく locale の符号で書き、subprocess の text の読みも locale の符号で読む。
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def _print_result(example: readme_examples.Example, failure: str | None) -> None:
    """走らせた例 1 つの結末を 1 行で(落ちたら、その訳を続けて)印字する。"""
    print(f"    {'ok' if failure is None else 'NG'}  {example.name}", flush=True)
    if failure is not None:
        print("        " + failure.replace("\n", "\n        "), flush=True)


def check_readmes(installed: Installed, place: Path, timeout: float) -> tuple[ReadmeResult, ...]:
    """入れた 5 個の README の例を、README ごとに新しい dir で上から順に走らせる。"""
    environment = example_environment(place)

    def check(package: str) -> ReadmeResult:
        """README 1 つを走らせて結末にする。"""
        examples = readme_examples.fences(installed.readmes[package], f"{package} の README")
        directory = place / "examples" / package
        directory.mkdir(parents=True)
        print(f"  {package} {installed.versions[package]} の README", flush=True)
        problems = [problem for example in examples for problem in example.problems()]
        for problem in problems:
            print(f"    NG  {problem}", flush=True)
        ran = sum(1 for example in examples if example.runs() and not example.problems())
        # 走らせる例が無い README は、確かめた事が無いので通さない(例を全部 excerpt にした時など)。
        empty = () if ran else (f"{package} の README に、走らせる例が無い — 確かめた事が無い",)
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
    """Python の版 1 つで、新しい venv に入れて README の例を走らせる。"""
    place = work / re.sub(r"[^A-Za-z0-9.]+", "-", f"py{python_request}")
    place.mkdir(parents=True)
    print(f"\n== Python {python_request}: 新しい venv に核の 5 個を入れる", flush=True)
    installed = install(source, python_request, place)
    build_kind = "free-threading" if installed.free_threaded else "GIL"
    print(f"  interpreter {installed.version}({build_kind})・{installed.python}", flush=True)
    print(
        "  入った版: " + "・".join(f"{name} {installed.versions[name]}" for name in CORE),
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
    """版 × README の結末の表(最後に印字して、どこが落ちたかを 1 目で見せる)。"""

    def cell(readme: ReadmeResult) -> str:
        """README 1 つの欄 — 通った数 / 走らせた数(落ちた数・他の落ちた事の数)。"""
        others = len(readme.failures) - readme.failed
        return (
            f"{readme.ran - readme.failed}/{readme.ran}"
            + (f"(落ち {readme.failed})" if readme.failed else "")
            + (f"(他に NG {others})" if others else "")
        )

    header = ["Python", *CORE, "依存の食い違い"]
    rows = [
        [
            f"{result.request}({result.installed.version})",
            *(cell(readme) for readme in result.readmes),
            "無し" if result.dependency_conflict is None else "在る",
        ]
        for result in results
    ]
    return "\n".join(" | ".join(row) for row in (header, *rows))


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """引数を読む — 入れ方(この checkout から組む・置いてある wheel・PyPI の版)と Python の版。"""
    parser = argparse.ArgumentParser(
        description="核の 5 個を repo の外の新しい venv に入れ、5 個の README の例を走らせて注記と比べる。",
    )
    chosen = parser.add_mutually_exclusive_group()
    chosen.add_argument(
        "--wheels",
        type=Path,
        metavar="DIR",
        help="組まずに、DIR の wheel を入れる(既定: この checkout から組む)",
    )
    chosen.add_argument(
        "--pypi",
        metavar="VERSION",
        help="PyPI の核の 5 個の VERSION を入れる(例 0.5.0)",
    )
    parser.add_argument(
        "--python",
        action="append",
        dest="pythons",
        metavar="REQUEST",
        help=f"uv に渡す Python の指定(何度でも書ける・既定 {' と '.join(DEFAULT_PYTHONS)})",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        metavar="DIR",
        help="wheel・venv・例を置く dir(repo の外・空か無い dir — 既定は一時の dir)",
    )
    parser.add_argument("--keep", action="store_true", help="終わった後も作業の dir を消さない")
    parser.add_argument(
        "--timeout",
        type=float,
        default=EXAMPLE_TIMEOUT_SECONDS,
        help="例 1 つの制限時間(秒・既定 %(default)s)",
    )
    return parser.parse_args(argv)


def _work_dir(requested: Path | None) -> Path:
    """作業の dir を決める — repo の中だと uv が repo の [tool.uv] を読み、新しい環境にならないので断る。"""
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="doeff-release-check-"))
    work = requested.resolve()
    if work.is_relative_to(REPO):
        raise SystemExit(f"--work-dir {work} は repo {REPO} の中に在る — repo の外の dir を渡す")
    if work.exists() and any(work.iterdir()):
        raise SystemExit(f"--work-dir {work} が空でない — 空か無い dir を渡す")
    work.mkdir(parents=True, exist_ok=True)
    return work


def main(argv: Sequence[str] | None = None) -> int:
    """入口 — 入れ方を決め、Python の版ごとに確かめ、表を印字して終了コードを返す。"""
    print_in_utf8()
    arguments = _parse(argv)
    pythons = tuple(arguments.pythons or DEFAULT_PYTHONS)
    if shutil.which("uv") is None:
        print("uv が PATH に無い — https://docs.astral.sh/uv/ の手順で入れる", file=sys.stderr)
        return 2
    work = _work_dir(arguments.work_dir)
    print(f"作業の dir: {work}", flush=True)
    try:
        if arguments.pypi is not None:
            source: Source = FromPyPI(arguments.pypi)
        elif arguments.wheels is not None:
            source = FromWheels(arguments.wheels.resolve())
        else:
            print(f"\n== この checkout({REPO})から核の 5 個の wheel を組む", flush=True)
            build(work / "wheels", pythons)
            source = FromWheels(work / "wheels")
        results = [check_python(source, python, work, arguments.timeout) for python in pythons]
    except subprocess.CalledProcessError as failed:
        print(
            f"\nbuild か install が落ちた(README の例までは進んでいない・終了コード {failed.returncode}): "
            f"{' '.join(failed.cmd)}",
            file=sys.stderr,
        )
        if failed.stderr:
            print(failed.stderr, file=sys.stderr)
        return 2
    finally:
        if arguments.keep:
            print(f"\n作業の dir を残した: {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print("\n== 結末(通った例 / 走らせた例)", flush=True)
    print(_summary(results), flush=True)
    passed = all(result.passed() for result in results)
    print("\n全部通った" if passed else "\n落ちた所が在る — 上の NG の行を読む", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
