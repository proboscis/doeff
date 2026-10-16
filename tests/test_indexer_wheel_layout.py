"""doeff-indexer の wheel が、入れた後に `doeff-indexer` の命令として動く形で組まれる事の検(取り組み doeff-publish・
card ki-d20475e94d87 の見つけた不具合)。

PyPI の 0.1.3 の Linux x86_64 の wheel は、命令の binary を `python/doeff_indexer/bin/doeff-indexer` に持ち、命令の入口
(packages/doeff-indexer/python/doeff_indexer/_cli.py)が探す `doeff_indexer/bin/` には無かった。`pip install doeff-indexer` の
後の `doeff-indexer --help` は "binary not found" で落ちた。訳は maturin の版: binary は .gitignore で python の source から外れ、
pyproject.toml の `include` が wheel に足す。maturin 1.12 は足す file を project の根からの path のまま入れ、1.13 は
python-source の下の path にする。0.1.3 は maturin-action が 1.12 を引いた日の CI で組まれた。

守りは 2 つ(CI の側は tests/test_indexer_wheel_workflow.py):

1. 道具 tools/verify_indexer_wheel.py の配置の検が、0.1.3 の配置・実行の印の無い binary・根の python/ を名指す。
2. build の口(packages/doeff-indexer/pyproject.toml)の maturin の下限が 1.13。

本物の wheel を新しい venv に入れて命令を撃つ検(道具の `--smoke`)は、Rust を組むので pytest に入れない(冷たい build は
1 本 120 秒の上限を越える) — 組んだ wheel に当てる手元の確かめと、CI の job が持つ。
"""

from __future__ import annotations

import importlib.util
import re
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "verify_indexer_wheel.py"
PACKAGE = REPO / "packages" / "doeff-indexer"
# python-source の下に include の file を置くようになった maturin の版。
MATURIN_FLOOR = (1, 13, 0)

# PyPI の 0.1.3 の Linux x86_64 の wheel の、bin の dir の物(maturin 1.12 の配置)。
PYPI_0_1_3 = (
    "doeff_indexer/bin/.gitignore",
    "doeff_indexer/bin/.gitkeep",
    "python/doeff_indexer/bin/.gitignore",
    "python/doeff_indexer/bin/.gitkeep",
    "python/doeff_indexer/bin/doeff-indexer",
)
LINUX_WHEEL = "doeff_indexer-0.1.4-cp310-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
WINDOWS_WHEEL = "doeff_indexer-0.1.4-cp310-abi3-win_amd64.whl"
EXECUTABLE = 0o100755 << 16
PLAIN = 0o100644 << 16


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_indexer_wheel", TOOL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wheel(tmp_path: Path, name: str, entries: dict[str, int]) -> Path:
    """entries(wheel の中の path → 権限を ZipInfo の欄に置いた値)を持つ wheel を作る。"""
    wheel = tmp_path / name
    with zipfile.ZipFile(wheel, "w") as archive:
        for entry, attributes in entries.items():
            info = zipfile.ZipInfo(entry)
            info.external_attr = attributes
            archive.writestr(info, b"\x7fELF binary" if "doeff-indexer" in entry else b"")
    return wheel


def _versions(requirement: str) -> tuple[int, ...]:
    """`v1.15.0`・`maturin>=1.13` の数の並び(桁数を MATURIN_FLOOR に揃えて比べられるように 0 で埋める)。"""
    numbers = tuple(int(part) for part in re.findall(r"\d+", requirement))
    return numbers + (0,) * (len(MATURIN_FLOOR) - len(numbers))


def test_a_wheel_with_the_binary_in_the_package_bin_dir_passes(tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path,
        LINUX_WHEEL,
        {
            "doeff_indexer/__init__.py": PLAIN,
            "doeff_indexer/bin/.gitkeep": PLAIN,
            "doeff_indexer/bin/doeff-indexer": EXECUTABLE,
        },
    )
    assert _tool().layout_problems(wheel) == []


def test_a_windows_wheel_needs_the_exe_and_no_executable_bit(tmp_path: Path) -> None:
    good = _wheel(tmp_path, WINDOWS_WHEEL, {"doeff_indexer/bin/doeff-indexer.exe": PLAIN})
    assert _tool().layout_problems(good) == []
    # Windows の wheel に拡張子の無い binary が在っても、命令の入口は .exe を探す。
    bare = tmp_path / "bare"
    bare.mkdir()
    wrong = _wheel(bare, WINDOWS_WHEEL, {"doeff_indexer/bin/doeff-indexer": PLAIN})
    assert any("doeff-indexer.exe が無い" in problem for problem in _tool().layout_problems(wrong))


def test_the_pypi_0_1_3_layout_is_named_for_both_faults(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, LINUX_WHEEL, dict.fromkeys(PYPI_0_1_3, EXECUTABLE))
    problems = _tool().layout_problems(wheel)
    assert any("doeff_indexer/bin/doeff-indexer が無い" in problem for problem in problems)
    assert any("python/doeff_indexer/bin/doeff-indexer" in problem for problem in problems)


def test_a_stray_python_dir_is_named_even_when_the_binary_is_in_place(tmp_path: Path) -> None:
    # .gitignore が効かない作業木(git の外)で maturin 1.12 が組むと、binary が両方の path に入る。
    wheel = _wheel(
        tmp_path,
        LINUX_WHEEL,
        {
            "doeff_indexer/bin/doeff-indexer": EXECUTABLE,
            "python/doeff_indexer/bin/doeff-indexer": EXECUTABLE,
        },
    )
    problems = _tool().layout_problems(wheel)
    assert len(problems) == 1
    assert "python/" in problems[0]


def test_a_binary_without_the_executable_bit_is_named(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, LINUX_WHEEL, {"doeff_indexer/bin/doeff-indexer": PLAIN})
    assert any("実行できる印" in problem for problem in _tool().layout_problems(wheel))


def test_main_exits_nonzero_and_prints_each_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = _wheel(tmp_path, LINUX_WHEEL, dict.fromkeys(PYPI_0_1_3, EXECUTABLE))
    assert _tool().main([str(wheel)]) == 1
    assert capsys.readouterr().err.count("verify_indexer_wheel:") == 2


def test_the_build_entry_asks_for_maturin_from_the_layout_fix() -> None:
    requires = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))[
        "build-system"
    ]["requires"]
    (maturin,) = [requirement for requirement in requires if requirement.startswith("maturin")]
    assert _versions(maturin.split(",")[0]) >= MATURIN_FLOOR, maturin
