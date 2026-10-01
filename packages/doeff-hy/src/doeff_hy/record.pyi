"""record.hy の公開面の型(型検査のための宣言 — 実行時は record.hy を読む・agora-redesign #2252)。

record.hy は Hy の module なので、pyright は中を読めず、defrecord の :check の展開が名指す
`doeff_hy.record.DoExpr`・`doeff_hy.record.EffectBase`・`doeff_hy.record.require_check` が全部 Unknown になる
(使う側の defrecord の行に書き手に直せない reportUnknownMemberType が出る)。ここで型を宣言する。

- DoExpr・EffectBase は record.hy が doeff から import した名の再公開(:check の式が Program / effect を返した形を
  isinstance で止めるのに使う)。
- require_check は deff(普通の関数)— dataclass の __post_init__ から呼ぶ。偽なら ValueError を上げ、答えは None。
- defrecord・defwire・defenum は macro で、Python の名ではないのでここには無い(require で取り込む)。
"""

from doeff import DoExpr as DoExpr
from doeff import EffectBase as EffectBase

def require_check(
    record: str, fields: tuple[str, ...], check: str, passed: bool, values: dict[str, object]
) -> None: ...
