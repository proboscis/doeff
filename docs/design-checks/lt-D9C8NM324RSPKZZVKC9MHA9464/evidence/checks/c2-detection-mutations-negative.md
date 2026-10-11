# 確かめ c2-detection-mutations-negative

- シナリオ: S1
- 種類: negative
- 期待: 検出の各所(束縛の文・code の名・関数の中の import・値として当たる environ・getenv の関数)・印を返す所・行の照らしの記録側・Hy の import の解き・逃げ道の印を 1 つずつ外すと、それを狙った検だけが赤になる
- 観測: d793b6fde の変異 7 つ・5c16f9fc3 の変異 2 つは、どれも狙った検だけが赤(数も狙いと一致)

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/first-fix/mut/summary.txt | 6454e2ff175ed56f14734252cd86149325e9e85537913b3595796e9e462071c9 |
| evidence/revision/mut/summary.txt | 3ecffd45dd1a28fa4c213dece7b9c53c5a3fc213a1f67977001e2b162b44ae27 |
| evidence/revision/mut/mut-hy-import-left-unresolved.txt | e802648aafbca3645e0035aad096fad2c1fdbcccc186e513d87b0f2f6d7fcf36 |
| evidence/revision/mut/mut-opaque-fold-without-mark.txt | 73da32232cf8c2acd2e1befce68e42d67c26bd018c14b549b8bb76118c072b73 |

各 file の版と命令は design.md の「証拠の索引」。
