"""doeff-run の Hy の module の隣の型の宣言(.pyi)が、道具 doeff_hy._static_stub の作る物と同じかの検。

使い手の repo(agora-controllers の agent の環境の handler)が doeff_run の効果・型・模擬の handler を import する時、.pyi が無いと
strict の型の検査で Unknown になり、書き手に直せない赤が使う行の数だけ出る。.pyi は道具が .hy から作る生成物で、.hy を変えたら
`python -m doeff_hy._static_stub --write <.hy>` で作り直す(作り直した物 == commit された物)。
"""

from pathlib import Path

from doeff_hy._static_stub import stale_in

SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_generated_stubs_are_what_the_tool_makes() -> None:
    assert [f"{s.source.relative_to(SOURCE)}: {s.reason}" for s in stale_in(SOURCE)] == []
