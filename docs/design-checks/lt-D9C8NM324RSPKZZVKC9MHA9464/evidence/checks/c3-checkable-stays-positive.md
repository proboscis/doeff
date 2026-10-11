# 確かめ c3-checkable-stays-positive

- シナリオ: S1
- 種類: positive
- 期待: 環境変数を読まない macro(body)・素の値の macro(plain)・同じ Hy の import の form の兄弟の表(fixed)の使い手は照らせるまま。本物の doeff_hy の閉包は印にならない
- 観測: c7ff7ed7d の source で守りの検が緑(兄弟の名を辿る 5c16f9fc3 の束縛の読みでは 1 failed)。doeff の Hy の module 298 個の歩み 1,209 回で印 0(d793b6fde・5c16f9fc3)・逃げ道 _fold_opaque 0 回・直す前の検出の数えで当たり 0

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/env-tests-at-c7ff7ed7d.txt | f5227a9ee5bceeaea99d389f17ab9c9fbab47a14ff51e6b7bba0ffb075f9fd39 |
| evidence/revision/sibling-guard-red-at-5c16f9fc3.txt | 3343d96191acc47f949d311db5739f4807d53503d51a0c155d2d85f8ad6f9806 |
| evidence/first-fix/reach-names-at-4ca9fcd50.txt | f7809e50a1f40b95d15a119c42dfbbfeb2740d9dddae0977b9806e6869a30cac |
| evidence/first-fix/reach-marks-at-d793b6fde-a.txt | 2a2178c92fb9e7d3a35a6bb20ffcdd547ca7ee150109e3c214f382d4b6a8dddc |
| evidence/first-fix/reach-marks-at-d793b6fde-b.txt | 18de4eeb69d11a7348d67e1fd231214453bc618297f23ed2c46856ef7121a008 |
| evidence/revision/reach-at-5c16f9fc3-a.txt | 79262a04b8811c56310ce379b2d3179a2dbd5e27a10bf8fc875104d21ce39d37 |
| evidence/revision/reach-at-5c16f9fc3-b.txt | ccaf2043f5b68d900dc34ab80bb7c9a3f8dbb936df171b6cc815fceb6418e5c9 |

各 file の版と命令は design.md の「証拠の索引」。
