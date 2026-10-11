# 確かめ c5-s2-mark-origins-negative

- シナリオ: S2
- 種類: negative
- 期待: 値で照らす変更(S2)の主張「W の _bound だけ」を試す — 印を立てる所が _bound 以外にもあり、展開の時の読みが束縛の下に畳まれるなら、_bound だけを値で照らす形は展開の時の読みを見落とす
- 観測: c7ff7ed7d の who_marks: table は _bound・aliased は _state_value・got は function の名だけの枝・built と helper は function。both は mode-now の 2 度目の訪れが _first_visit で畳まれ、印は bound BUILT の下の 1 回だけ — 主張は偽(S2 の実際に記した)

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/s2-who-marks-at-c7ff7ed7d.txt | 1637604ce2906e446faa431069d617840bc080cea651a623d4ce0dfd964912ba |

各 file の版と命令は design.md の「証拠の索引」。
