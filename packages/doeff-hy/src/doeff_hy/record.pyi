"""record.hy の公開面の型(型検査のための宣言 — 実行時は record.hy を読む・agora-redesign #2252)。

record.hy は Hy の module なので、pyright は中を読めず、defrecord の :check の展開が名指す
`doeff_hy.record.DoExpr`・`doeff_hy.record.EffectBase` が Unknown になる
(使う側の defrecord の行に書き手に直せない reportUnknownMemberType が出る)。ここで型を宣言する。

- DoExpr・EffectBase は record.hy が doeff から import した名の再公開(:check の式が Program / effect を返した形を
  isinstance で止めるのに使う)。
- 偽の答えを断る require_check は内側の doeff_hy._record_check(Python の module — pyright が直に読む)に在る。
  doeff_hy.record は macro だけを見せる境界なので、名を置かない(ADR-DOE-API-001 R14・card ki-a28c20b93638)。
- defrecord・defwire・defenum は macro で、Python の名ではないのでここには無い(require で取り込む)。
"""

from doeff import DoExpr as DoExpr
from doeff import EffectBase as EffectBase
