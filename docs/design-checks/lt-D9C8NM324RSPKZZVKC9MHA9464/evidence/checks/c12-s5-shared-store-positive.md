# 確かめ c12-s5-shared-store-positive

- シナリオ: S5
- 種類: positive
- 期待: 印の無い使い手は、置き場を共有する別の作業木・process で解き直さず今に合い、照らしの回数は直す前と同じ
- 観測: process をまたぐ覚えの節の検(置き場を共有する別の木の 2 つ目の process が解き直さず合う)が c7ff7ed7d の source で緑。照らしの回数は 4ca9fcd50 と d793b6fde、38629119 と c7ff7ed7d で温まった後 同じ(照らし 802・歩み 17・stat 20)。9 file の focused が d793b6fde で 135 passed

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/touched-tests-at-c7ff7ed7d-source.txt | 40baa8fc8eaed760d4a58b263e18df235f733270819469292a1fafe2dacedb84 |
| evidence/first-fix/walk-count-4ca9fcd50-vs-d793b6fde.txt | dc95a94c8ee28537a0655192b53b741876018d9551dd4f552e36e02d1ba55f02 |
| evidence/revision/walk-count-final.txt | 4de3ae26d350d0675b3ceab0e49301a89b688f562be57bd81bbeef6d7ff26092 |
| evidence/first-fix/focused-at-d793b6fde.txt | aaae783014a780715a866e86a14194a0246ba1a7512dc5d8386fa1ce70de45d7 |

各 file の版と命令は design.md の「証拠の索引」。
