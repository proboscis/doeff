# 確かめ c10-s4-inprocess-positive

- シナリオ: S4
- 種類: positive
- 期待: 環境変数を読まない macro の使い手は、同じ process の中で覚えられて今に合い、os.environ の書き換えの後も別の thread でも今に合う
- 観測: 5c16f9fc3: body の digest は 16 進・照らしは 2 回とも真・_REMEMBERED に probe_s4.macros.body・書き換えの後も別の thread でも真

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/s4-inprocess.txt | 8ecdb309a60cbe2a4cda580ed3e61fdafa9e9ab297fd09d6188537f87fd9c5dd |

各 file の版と命令は design.md の「証拠の索引」。
