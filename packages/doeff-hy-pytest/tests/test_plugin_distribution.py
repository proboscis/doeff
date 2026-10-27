"""doeff-hy の pytest plugin を、.hy を持たない配布物に置く不変の検。

pytest は pytest11 の entry point を持つ配布物の全 module に assert の書き換えの印を付ける
(`_pytest.config._iter_rewritable_modules`)。doeff-hy 自身が entry point を持つと、wheel で入れた repo で
`doeff_hy/macros.hy` を Python として書き換えようとして import が SyntaxError で落ちた(2026-09-26 の実測)。
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
from pathlib import Path

import tomllib
from packaging.requirements import Requirement

PACKAGES = Path(__file__).resolve().parents[2]


def _project(package: str) -> dict[str, object]:
    """配布物の宣言(pyproject.toml の [project])を読む — install の形(editable か wheel か)に依らず宣言を検めるため。"""
    with (PACKAGES / package / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_doeff_hy_itself_declares_no_pytest_plugin() -> None:
    """.hy を持つ配布物 doeff-hy は pytest11 の entry point を持たない(持つと pytest が .hy に書き換えの印を付ける)。"""
    assert "pytest11" not in _project("doeff-hy").get("entry-points", {})


def test_the_plugin_lives_in_a_distribution_without_hy_sources() -> None:
    """plugin の名 doeff_hy は doeff-hy-pytest が持ち、その配布物の source に .hy は無い。doeff-hy が依存として引く。"""
    entry_points = _project("doeff-hy-pytest")["entry-points"]
    assert entry_points["pytest11"] == {"doeff_hy": "doeff_hy_pytest"}
    assert not list((PACKAGES / "doeff-hy-pytest" / "src").rglob("*.hy"))
    # 依存は版の系の指定(>=0.7.0,<0.8 の形 — tests/test_core_packages_version.py)を伴うので、PEP 508 の名で比べる。
    assert "doeff-hy-pytest" in {
        Requirement(line).name for line in _project("doeff-hy")["dependencies"]
    }


def test_installed_plugin_entry_point_belongs_to_doeff_hy_pytest() -> None:
    """入っている環境でも、pytest11 の doeff_hy を名乗る配布物は doeff-hy-pytest ちょうど 1 つ。"""
    owners = [
        dist.metadata["Name"]
        for dist in metadata.distributions()
        for entry in dist.entry_points
        if entry.group == "pytest11" and entry.name == "doeff_hy"
    ]
    assert owners == ["doeff-hy-pytest"], owners


def test_the_plugin_shows_only_the_two_declarations() -> None:
    """入口が出す名は検ごとの宣言 2 個だけで、hook は pytest が拾う属性として在る(card ki-ad38495b7c15)。測りの中は _ で
    始まる module に在り、_ の無い module は移し替えの間だけ残す旧い path で、名を 1 つも出さない。"""
    import doeff_hy_pytest

    assert doeff_hy_pytest.__all__ == ["declare_process_tree_cpu", "declare_step_budget"]
    assert callable(doeff_hy_pytest.pytest_terminal_summary)
    source = PACKAGES / "doeff-hy-pytest" / "src" / "doeff_hy_pytest"
    open_modules = sorted(
        path.stem for path in source.glob("*.py") if not path.name.startswith("_")
    )
    assert open_modules == ["budget", "python_calls"]
    for name in open_modules:
        assert importlib.import_module(f"doeff_hy_pytest.{name}").__all__ == [], name


def test_the_plugin_package_ships_its_types() -> None:
    """doeff_hy_pytest は型を持つ印 py.typed を持つ — 使い手の検が declare_step_budget を import した時に、pyright strict が
    型の写しの無い module(reportMissingTypeStubs)として赤にしないため(agora-redesign #2670 の 3)。"""
    assert (PACKAGES / "doeff-hy-pytest" / "src" / "doeff_hy_pytest" / "py.typed").is_file()
