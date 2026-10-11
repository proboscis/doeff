# 確かめ c4-s1-within-walker-positive

- シナリオ: S1
- 種類: positive
- 期待: S1(cwd を照らせない読みに足す)を W の定数だけで模すと、module の属性・Python の import の橋・Hy の import のどの形の cwd の使い手も cwd2 で展開し直す。模さなければ cwd の形は照らせるまま(契約の外)
- 観測: c7ff7ed7d: 模した側で cwd の 3 形が全部 cwd2・模さない側で 3 形とも cwd1 のまま。5c16f9fc3 では模さない側の Hy の import の形が兄弟の名で印になった(後で直した)

| 証拠の file(記録の dir からの path) | sha256 |
| --- | --- |
| evidence/revision/s1-emulation-at-c7ff7ed7d.txt | 43622dd6b13a5f21497a43d4480eeb78fbcb14a316c31a518376aac9f32b1823 |
| evidence/revision/s1-emulation-at-5c16f9fc3.txt | 241054e68b62c63e4ffce326d47e3546e05865608767652318d409e53eae9ee5 |

各 file の版と命令は design.md の「証拠の索引」。
