"""doeff_cluster が型を持つ印 py.typed を持つ事の検。

使い手の repo の pyright strict は、py.typed の無い package の Python の module(型を書いた .py — 例 worker/entry/line_stamp.py)を
import すると「型の写しの無い module」(reportMissingTypeStubs)として赤にする。agora-controllers の検
controllers/agora_sim/tests/test_job_log_route.hy が送り手の部品(SHIP-LIMITS・JobLogShipper ほか)を読み、日次の型の測りが赤にした
(card ki-ae11a2a52ea0・親 ki-deb1a202954b)。.py の型をそのまま渡すのが根(doeff-hy-pytest の b2be79bbb と同じ形)。Hy の module の型は
今までどおり隣の .pyi が渡す。hatchling の wheel は package の dir の file を全部入れるので、配布物の設定は変えない。
失敗ケース: py.typed を消すと赤。
"""

from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster"


def test_the_package_ships_its_types() -> None:
    assert (PACKAGE / "py.typed").is_file()
