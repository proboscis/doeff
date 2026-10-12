# 盲検 A・B の前の主張(版 91b9b8bbc66d082d8bd357558e312690d1fef7aa)

盲検に渡した材料 blind/blind-common.md の §2(要件 R1〜R5)・§3(責務)・§4(公開契約 C1〜C7)・§5(強制方法)・§6(変更シナリオと事前の予測)が、盲検の前の主張そのもの
(sha256 14662e115bf4655ca9de052b4d9d37b6a6932ad8a49c197ab76d0d02b4018780)。失敗例を受けて書き換えた結論で上書きしない。
予備の評価 0(blind-meta.md)の後で直した版の主張で、評価 0 の前の主張(版 1e06853f3 — 変異は「Program で包んで Call にする」・自己検は今の macro の値を決め打つ)との差は design.md §6。

## 盲検に渡さなかった「予想する限界」(盲検の前に設計者が持っていた物)

- 変異は一番外の値だけを包む。内側の handler が受けた Traverse の中身(件の関数の形・items の型)を印字する例は見分けない。
- defhandler(handler を定義する文)と `<-`(束縛)が作る物の形は包めない(UNMUTATED_MACROS)。
- 例が import する lib(examples/ の外の package)が macro で作った値は、変異の外(R4 の裏)。
- 静的な読み(表の検 `test_example_macros_are_mutated_or_named`・前の card の印の検)は、`(require doeff-hy.macros [名 …])` の綴りだけを読む。`*`・`:macros`・別名の綴りは読めない。
- 前の card の印の検(C2)は、各 macro の 1 段の展開が印を持つ事に依る — 展開の段を足す作り替えは、表への行を求めて赤になる(設計どおり)。
- 変異の対象の macro が使えない(名が表に無い・包み方を入口が知らない)時は、入口が 0 以外で終わる。黙って空振りしない。
