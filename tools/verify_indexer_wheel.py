"""doeff-indexer の wheel が、入れた後に命令として動く形かを確かめる(取り組み doeff-publish・card ki-d20475e94d87 の見つけた不具合)。

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

使い方: python tools/verify_indexer_wheel.py [--smoke <この機体で動く wheel>] <wheel>...
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
    # 依存は無い(Rust の命令と拡張 module だけ)ので、PyPI へ出ずに wheel だけで入れる。
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
    args = parser.parse_args(argv)

    layout = [problem for wheel in args.wheels for problem in layout_problems(wheel)]
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
