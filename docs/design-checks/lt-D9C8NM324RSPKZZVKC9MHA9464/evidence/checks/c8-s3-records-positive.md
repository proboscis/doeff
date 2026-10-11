# 確かめ c8-s3-records-positive

- シナリオ: S3
- 種類: positive
- 期待: R の形を変えずに、印の無い記録は process と JSON の境界をまたいで今に合い(置き場を共有する別の木の 2 つ目の process が解き直さず合う)、印の行は記録の側でも今の側でも古い
- 観測: 触った検の file 2 つ(process をまたぐ覚えの節・行の照らしの検を含む)が 5c16f9fc3 で 82 passed + 時間切れ 2 本の単独の緑、c7ff7ed7d の source で 84 passed。行の照らしの検は環境変数の節 17 passed の中

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/touched-tests-at-5c16f9fc3.txt | 5601d6e1c5c3229e358788506aaabe998143c8e530c48b2f3aa5392b1ffc2c2d |
| evidence/revision/touched-tests-rerun-at-5c16f9fc3.txt | 1323da33310f475f779f1d3a30005094df5890f91670e1bb7180c4fa99d63b8b |
| evidence/revision/touched-tests-at-c7ff7ed7d-source.txt | 40baa8fc8eaed760d4a58b263e18df235f733270819469292a1fafe2dacedb84 |
| evidence/revision/env-tests-at-c7ff7ed7d.txt | f5227a9ee5bceeaea99d389f17ab9c9fbab47a14ff51e6b7bba0ffb075f9fd39 |

各 file の版と命令は design.md の「証拠の索引」。
