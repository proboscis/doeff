"""doeff-hy の pytest plugin を、.hy を持たない配布物に置く不変の検。

pytest は pytest11 の entry point を持つ配布物の全 module に assert の書き換えの印を付ける
(`_pytest.config._iter_rewritable_modules`)。doeff-hy 自身が entry point を持つと、wheel で入れた repo で
`doeff_hy/macros.hy` を Python として書き換えようとして import が SyntaxError で落ちた(2026-09-26 の実測)。
"""

from __future__ import annotations

import importlib.metadata as metadata
import tomllib
from pathlib import Path

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
    assert "doeff-hy-pytest" in _project("doeff-hy")["dependencies"]


def test_installed_plugin_entry_point_belongs_to_doeff_hy_pytest() -> None:
    """入っている環境でも、pytest11 の doeff_hy を名乗る配布物は doeff-hy-pytest ちょうど 1 つ。"""
    owners = [
        dist.metadata["Name"]
        for dist in metadata.distributions()
        for entry in dist.entry_points
        if entry.group == "pytest11" and entry.name == "doeff_hy"
    ]
    assert owners == ["doeff-hy-pytest"], owners
