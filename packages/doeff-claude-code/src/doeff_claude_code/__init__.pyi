"""doeff_claude_code の入口の型の宣言(手で書く — 隣に .hy が無いので、道具の一致の検は照らさない)。

doeff_claude_code は Hy の module だけの namespace package で、実行時の __init__ を持たない(hy を読み込んだ process が中の module を
import する)。入口の宣言が無いと、pyright は package そのものの import(package の dir の一覧 __path__ から隣の file の path を作る使い手)を
「型の宣言が無い」(reportMissingTypeStubs)と読む(#4257)。中の module の宣言(values.pyi・lines.pyi など)は doeff_hy.static_stub が
.hy から作る。入口が公開し直す名は無い — 使い手は中の module から名を import する。
"""
