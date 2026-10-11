# 確かめ c1-environment-forms-negative

- シナリオ: S1
- 種類: negative
- 期待: 環境変数で決まる値を読む 12 の形(付録 3 の dict・list・別名の environ・getenv・読み込みの時の補助の関数・Python の module の表・展開の時の直の読み・補助の関数の読み・関数の中の import・Hy の import・既定値・閉包の cell)の使い手は、直す前の版で環境変数を替えた process が古い展開を使い、直した版では compile し直して新しい値を使う
- 観測: 4ca9fcd50 で形 9 つが 9 failed(probe でも 8 形が 'plain')→ d793b6fde で 12 passed。38629119 を含む b9f436404 で盲検 A の 3 形が 3 failed → c7ff7ed7d の source で環境変数の節 17 passed

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/first-fix/forms-probe-at-4ca9fcd50.txt | 426e1689a383fa55b5fb15d9ae09eeddf1eacf64756a1add789b5ed55807c420 |
| evidence/first-fix/red-at-4ca9fcd50.txt | f6435134151a5bd0aaba729942302594b2b09659369540788f0c85b979028d98 |
| evidence/first-fix/env-tests-at-d793b6fde.txt | 41a318915d5574826ca8d324359075070cf0f59df5967e9d96c8dd8f78050a07 |
| evidence/revision/red-at-b9f436404.txt | 815156df97284e0bb4b1166cd6d6147da0c9a145282da00a27a012992ec3a3da |
| evidence/revision/env-tests-at-c7ff7ed7d.txt | f5227a9ee5bceeaea99d389f17ab9c9fbab47a14ff51e6b7bba0ffb075f9fd39 |

各 file の版と命令は design.md の「証拠の索引」。
