# 確かめ c9-s4-inprocess-negative

- シナリオ: S4
- 種類: negative
- 期待: 同じ process の中で os.environ を書き換えた後も、別の thread でも、環境変数を読む macro の使い手の記録は古く、process の中の覚えに入らない
- 観測: 5c16f9fc3: direct の digest は印・照らしは 2 回とも偽・書き換えの後も偽・別の thread でも偽・_REMEMBERED に入らない

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/s4-inprocess.txt | 8ecdb309a60cbe2a4cda580ed3e61fdafa9e9ab297fd09d6188537f87fd9c5dd |

各 file の版と命令は design.md の「証拠の索引」。
