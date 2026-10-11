"""doeff_cluster の worker/entry が型を持つ印 py.typed を持つ事の検。

使い手の repo の pyright strict は、型を持つ印の無い Python の module(型を書いた .py — worker/entry/line_stamp.py)を import すると
「型の写しの無い module」(reportMissingTypeStubs)として赤にする。使い手の repo の検が job の log の
送り手の部品(SHIP-LIMITS・JobLogShipper ほか)を読み、日次の型の測りが赤にした(card ki-ae11a2a52ea0・親 ki-deb1a202954b)。.py の型を
そのまま渡すのが根(doeff-hy-pytest の b2be79bbb と同じ考え)。
置き場: doeff_cluster は __init__.py の無い名前空間 package で、pyright は名前空間 package の最上位の py.typed では印と読まず、module の在る
sub-package の dir の py.typed を読む(実測 2026-10-12: 最上位に置いた時は赤が残り、worker/entry に置くと消えた — PEP 561 の名前空間
package の置き方)。Hy の module の型は今までどおり隣の .pyi が渡す。hatchling の wheel は package の dir の file を全部入れるので、配布物の
設定は変えない。
失敗ケース: worker/entry の py.typed を消すと赤。
"""

from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "worker" / "entry"


def test_the_worker_entry_modules_ship_their_types() -> None:
    assert (ENTRY / "line_stamp.py").is_file()
    assert (ENTRY / "py.typed").is_file()
