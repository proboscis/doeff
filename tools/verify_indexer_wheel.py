"""doeff-indexer の wheel が、入れた後に命令として動く形かを確かめる(取り組み doeff-publish で見つけた不具合)。

PyPI の 0.1.3 の Linux x86_64 の wheel は、命令の binary を `python/doeff_indexer/bin/doeff-indexer` に持ち、
`doeff_indexer/bin/` には持たなかった。命令の入口(python/doeff_indexer/_cli.py)は package の `bin/` の下だけを探すので、
`pip install doeff-indexer` の後の `doeff-indexer --help` が "binary not found" で落ちた。

訳: binary は `.gitignore` で python の source から外れ、pyproject.toml の `include` が wheel に足す。maturin 1.12.0 は
足す file を project の根からの path のまま(`python/` つき)入れ、1.13.0 は python-source の下の path(`doeff_indexer/bin/`)に
直して入れる。0.1.3 は maturin-action が 1.12.0 を引いた CI で組まれ、そのまま upload された。

この道具は組んだ後・upload の前に、2 つを確かめる:

- 配置(全部の wheel): `doeff_indexer/bin/doeff-indexer`(Windows は `.exe`)が在り、Windows 以外は実行できる印を持ち、
  wheel の根に `python/` の下の物が無い。
- 入れた後の動き(`--smoke` の 1 つの wheel): 新しい venv に pip で入れ、`doeff-indexer --help` が通り、
  `find-interpreters` が `Program` の語も型の注釈も無い file の `# doeff: interpreter` を見つける。

- 一覧(`--expect-all`): 公開する 15 個(通常の abi3・free-threaded の 3.13・3.14 の 3 種類 × Linux の 2 つ・macOS の 2 つ・
  Windows)が全部そろう。0.2.0 の手動実行で 3.13 向けの 5 個が組まれず 10 個になっても、job も配置の検も通ったため。

使い方: python tools/verify_indexer_wheel.py [--expect-all] [--smoke <この機体で動く wheel>] <wheel>...
(標準ライブラリだけ — repo を checkout しただけの python で走るので、upload の前の CI の job からも呼べる)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PACKAGE = "doeff_indexer"
# `Program` の語も `@do` も型の注釈も無い file の印(docs/MARKERS.md の最初の例)。
SAMPLE_FILE = "markers_only.py"
SAMPLE = "from doeff import run\n\ndef my_interpreter(program):  # doeff: interpreter\n    return run(program)\n"
SAMPLE_INTERPRETER = "my_interpreter"
EXECUTABLE_BITS = 0o111
# 公開する wheel の一覧: 3 つの種類 × 5 つの OS = 15 個(PyPI の 0.1.3 と同じ数)。種類は (python tag, abi tag)。
# 0.2.0 の最初の手動実行は、maturin 1.15 が free-threaded の 3.13 の指定を拒否して 3.13 向けの 5 個が組まれず、10 個になった。
# それでも job は全部通り、配置の検も通った — 組む wheel の一覧を CI が持たなかったため。
EXPECTED_KINDS = (("cp310", "abi3"), ("cp313", "cp313t"), ("cp314", "cp314t"))
EXPECTED_PLATFORMS = ("linux-x86_64", "linux-aarch64", "macos-x86_64", "macos-arm64", "windows-x86_64")


def _is_windows(wheel: Path) -> bool:
    """wheel の名の最後の部(platform tag — `win_amd64` など)から、Windows の wheel かを読む。"""
    return wheel.stem.split("-")[-1].startswith("win")


def layout_problems(wheel: Path) -> list[str]:
    """wheel の中身が、命令の入口の探す所に binary を持つかを読み、足りない所を 1 行ずつ返す(空なら良い)。"""
    windows = _is_windows(wheel)
    binary = f"{PACKAGE}/bin/doeff-indexer{'.exe' if windows else ''}"
    with zipfile.ZipFile(wheel) as archive:
        infos = {info.filename: info for info in archive.infolist()}
    info = infos.get(binary)
    missing = (
        () if info is not None else (f"{wheel.name}: {binary} が無い(命令の入口はここだけを探す)",)
    )
    empty = (f"{wheel.name}: {binary} が空",) if info is not None and info.file_size == 0 else ()
    not_executable = (
        (f"{wheel.name}: {binary} が実行できる印を持たない",)
        if info is not None and not windows and not (info.external_attr >> 16) & EXECUTABLE_BITS
        else ()
    )
    stray = sorted(name for name in infos if name.startswith("python/"))
    misplaced = (
        (
            f"{wheel.name}: wheel の根に python/ の下の物が在る({', '.join(stray)}) — "
            "入れると site-packages/python/ に落ちる(maturin 1.12 以前の include の配置)",
        )
        if stray
        else ()
    )
    return [*missing, *empty, *not_executable, *misplaced]


def _platform_of(wheel: Path) -> str | None:
    """wheel の名の最後の部(platform tag)から、一覧の OS の名を読む(読めなければ None)。"""
    tag = wheel.stem.split("-")[-1]
    if tag.startswith("win_amd64"):
        return "windows-x86_64"
    if tag.startswith("macosx"):
        return "macos-arm64" if tag.endswith("arm64") else "macos-x86_64" if tag.endswith("x86_64") else None
    if "manylinux" in tag or tag.startswith("linux"):
        return "linux-aarch64" if "aarch64" in tag else "linux-x86_64" if "x86_64" in tag else None
    return None


def missing_wheels(wheels: list[Path]) -> list[str]:
    """公開する 15 個(種類 × OS)のうち、渡された wheel に無い物を 1 行ずつ返す(空なら全部ある)。

    数えるのは種類 × OS で、file の数ではない: macOS と Windows の free-threaded の job が通常の job と同じ abi3 の wheel を
    組んだ時、名の同じ wheel は 1 つの種類として数えられ、free-threaded の種類は足りないまま残る。
    """
    present = set()
    for wheel in wheels:
        parts = wheel.stem.split("-")
        if len(parts) >= 5:
            present.add((parts[2], parts[3], _platform_of(wheel)))
    return [
        f"{platform} の {python}-{abi} の wheel が無い"
        for python, abi in EXPECTED_KINDS
        for platform in EXPECTED_PLATFORMS
        if (python, abi, platform) not in present
    ]


def _venv_tool(venv: Path, name: str) -> Path:
    return (
        venv
        / ("Scripts" if sys.platform == "win32" else "bin")
        / (f"{name}.exe" if sys.platform == "win32" else name)
    )


def _run(argv: list[str | Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(part) for part in argv], capture_output=True, text=True, check=False)


def smoke_problems(wheel: Path, work: Path) -> list[str]:
    """wheel を新しい venv に入れて命令を撃ち、動かない所を 1 行ずつ返す(空なら良い)。work は空の dir。"""
    venv = work / "venv"
    created = _run([sys.executable, "-m", "venv", venv])
    if created.returncode != 0:
        return [f"venv を作れない: {created.stderr.strip()}"]
    # 命令の入口(_cli)は doeff も拡張 module も読まないので、依存(doeff — Python の口の effect と handler が読む)を入れずに、
    # PyPI へ出ずに wheel だけで入れて命令を撃つ。
    installed = _run(
        [
            _venv_tool(venv, "python"),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-deps",
            "--no-index",
            wheel,
        ]
    )
    if installed.returncode != 0:
        return [f"{wheel.name} を入れられない: {installed.stderr.strip()}"]
    command = _venv_tool(venv, "doeff-indexer")
    helped = _run([command, "--help"])
    if helped.returncode != 0:
        return [
            f"入れた後の `doeff-indexer --help` が code {helped.returncode}: {(helped.stderr or helped.stdout).strip()}"
        ]
    sample = work / "sample"
    sample.mkdir()
    (sample / SAMPLE_FILE).write_text(SAMPLE, encoding="utf-8")
    found = _run([command, "find-interpreters", "--root", sample])
    if found.returncode != 0:
        return [
            f"入れた後の `doeff-indexer find-interpreters` が code {found.returncode}: {found.stderr.strip()}"
        ]
    names = [entry["name"] for entry in json.loads(found.stdout)["entries"]]
    if SAMPLE_INTERPRETER not in names:
        return [
            f"`# doeff: interpreter` の印だけの file を find-interpreters が見つけない(見つけた物: {names})"
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="doeff-indexer の wheel の配置と、入れた後の動きを確かめる"
    )
    parser.add_argument("wheels", nargs="+", type=Path, help="配置を確かめる wheel")
    parser.add_argument(
        "--smoke", type=Path, help="新しい venv に入れて命令を撃つ wheel(この機体で動く 1 つ)"
    )
    parser.add_argument(
        "--expect-all",
        action="store_true",
        help="公開する 15 個(種類 × OS)が全部そろうかも確かめる(公開の前の job が付ける)",
    )
    args = parser.parse_args(argv)

    layout = [problem for wheel in args.wheels for problem in layout_problems(wheel)]
    if args.expect_all:
        layout = [*missing_wheels(args.wheels), *layout]
    if args.smoke is None:
        smoke: list[str] = []
    else:
        with tempfile.TemporaryDirectory(prefix="verify-indexer-wheel-") as work:
            smoke = smoke_problems(args.smoke, Path(work))
    problems = [*layout, *smoke]
    for problem in problems:
        print(f"verify_indexer_wheel: {problem}", file=sys.stderr)
    if not problems:
        print(
            f"verify_indexer_wheel: {len(args.wheels)} 個の wheel の配置が良い"
            + (" ・入れた後の命令が動く" if args.smoke else "")
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
