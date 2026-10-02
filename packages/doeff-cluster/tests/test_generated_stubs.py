"""doeff-cluster の Hy の module の型の宣言のうち、doeff_hy.static_stub が作った .pyi が今の .hy と一致するか(#2826)。

一致の検 = 作り直した物 == commit された物。.hy の公開面(関数の契約・record の欄・定数)を変えたら、
`python -m doeff_hy.static_stub --write <.hy>` で .pyi を作り直して同じ commit に入れる(忘れると、名指された .hy が並んで赤になる)。
手で書いた .pyi(先頭に道具の印が無い物)は照らさない。
"""

from pathlib import Path

from doeff_hy.static_stub import stale_in

SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_generated_stubs_are_what_the_tool_makes() -> None:
    assert [f"{s.source.relative_to(SOURCE)}: {s.reason}" for s in stale_in(SOURCE)] == []
