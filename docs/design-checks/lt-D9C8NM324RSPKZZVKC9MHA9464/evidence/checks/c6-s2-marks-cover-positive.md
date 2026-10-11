# 確かめ c6-s2-marks-cover-positive

- シナリオ: S2
- 種類: positive
- 期待: 今の設計(印)は、S2 が値で照らそうとする形(表・別名・getenv・補助の関数・展開の時の読み・両方)を全部 照らせない側で扱う
- 観測: who_marks で 6 形とも digest が environment-read。環境変数の節の検で built・helper などの形が環境変数を替えた process で compile し直す

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/s2-who-marks-at-c7ff7ed7d.txt | 1637604ce2906e446faa431069d617840bc080cea651a623d4ce0dfd964912ba |
| evidence/revision/env-tests-at-c7ff7ed7d.txt | f5227a9ee5bceeaea99d389f17ab9c9fbab47a14ff51e6b7bba0ffb075f9fd39 |

各 file の版と命令は design.md の「証拠の索引」。
