# 確かめ c11-s5-loader-bypass-negative

- シナリオ: S5
- 種類: negative
- 期待: 読み込みの口が印の行を除いた記録を照らし、環境変数の指紋で置き場の entry を使い回す実装(盲検 B の差分)は、足した 2 本の検で拒まれる
- 観測: B の差分を 5c16f9fc3 に当てると、置き場の路の振る舞いの検と印を読む module の構文木の検の 2 本だけが赤。外すと環境変数の節 17 passed

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/mut/mut-blind-b-loader-hooks-diff.txt | 76bad04b985e7758c746da89148884c9e46d3e7d5fec1c481b50de89eab57a4f |
| evidence/revision/blind-b-loader_hooks.diff.txt | 49b6faf7f8353e4cd74ef9f722c211be6df7247f52c06171f5a9458b617600d8 |
| evidence/revision/env-tests-at-c7ff7ed7d.txt | f5227a9ee5bceeaea99d389f17ab9c9fbab47a14ff51e6b7bba0ffb075f9fd39 |

各 file の版と命令は design.md の「証拠の索引」。
