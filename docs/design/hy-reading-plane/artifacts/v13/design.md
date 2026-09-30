# v13 — 読む面に linter の違反の印と説明の吹き出しを出す

出自 = operator 2026-09-30 15:0x(herdr pane・linter-discuss)。逐語 "i want the linted part highlighted in the reading
view of the hy file, and show info on hover so i can tell what's wrong why. here linter show DOEFF205 on start-turn but the
viewer doesnt show it on read view so i can't tell" → 案を示して "sounds good"。

## なぜ start-turn の DOEFF205 が読む面に出ないか(v12 時点の観測)

| 読む面の描き方(今) | DOEFF205 の場合 |
|---|---|
| 本体の行(`val str launch ⇐ …`)に、その source 行に始まる違反の印(規則 ID の札 + `title` 属性の hover に `規則: message`)— `read/render.ts` の bodyBlock | 違反の位置は 403 行の `start-turn` の名(定義の頭)。頭の行は本体の行ではないので印が付く行が無い |
| カードの足に「違反 N」の札(`title` に `規則: message`)— renderCard の foot | 付く。ただし足は Hy source の箱の下にあるので、source を開くと画面の外 |
| Hy source の箱(sourceBox) | 違反の印は一切描かない |
| file の先頭に付く違反(import の向き 101/102/103・置き場 114/115・宣言にない依存 116) | `read/model.ts` は `within(definition.fullRange, …)` で定義の範囲に入る違反だけをカードへ配る → どのカードにも入らず読む面に一切出ない |
| hover の中身 | 1 行だけ。linter が持つ `hint`(直し方)・`explanation.reason`(なぜ)・`law`・`standing`(既知か新規か)・Jev の p は使っていない |

## After(合意した案)

```
Before(今)                              After(案)
┌ defk start-turn [assignment row …]     ┌ defk start-turn̲̲̲̲̲̲̲̲̲̲ ●critical DOEFF205   ← 頭: 名前に下線 + 札
│ :pre … :post …                          │ :pre … :post …
│ val str launch ⇐ launch-of-lease(…)     │ val str launch ⇐ launch-of-lease(…)
│ val ? _launched ⇐ RecordTurnLaunch(…)   │ val ? _launched ⇐ RecordTurnLaunch(…)  DOEFF120 ← 本体の行(今もある)
│ Hy source (read only) 403–434           │ Hy source (read only) 403–434
│  403 (defk start-turn [assignment …]    │  403 (defk start-turn̲̲̲̲̲̲̲̲̲̲ [assignment …]   ← source: 範囲に下線
└ 足: [違反 1] task_attempt.hy:403        └ 足: [違反 1] task_attempt.hy:403
                                            hover(どの印でも同じ吹き出し):
                                            ┌──────────────────────────────────────┐
                                            │ DOEFF205 形の検めと判断が混ざる  critical │
                                            │ 既知(登録簿) · Jev p=0.79              │
                                            │ なぜ: 入力の形の検め(辞書の鍵・isinstance │
                                            │  ・空の検め)と業務の判断が 1 つの定義に…  │
                                            │ 直し方: 形の検めは protocol の境目で      │
                                            │  defwire の型に parse し、この定義は…     │
                                            │ [違反の表で見る] [editor で開く]           │
                                            └──────────────────────────────────────┘
```

| 論点 | 決めた事 | 理由・代案 |
|---|---|---|
| 1. 印の場所 | 違反の range が (a) 定義の頭 → カードの頭の名に下線 + 札、(b) 本体の行 → 今の本体の札に加えて source の同じ行に下線、(c) file の先頭(101/102/103/114/115/116)→ file の見出しに帯 | 今は (a) が足だけ・(c) は読む面に一切出ない。(c) は違反の配り方を「定義の範囲」から「file 全体」へ広げないと消えたまま |
| 2. hover の中身 | 規則 ID + 規則の短い名(linter の rules[].title)+ 重大さ + 既知/新規 + Jev の p + なぜ(explanation.reason)+ 直し方(hint)+ 法の文(あれば)+ 2 つのボタン(違反の表で見る・editor で開く) | 今は title 属性の素の tooltip(1 行・遅い・改行が崩れる)。自前の吹き出しにすれば pixel の見た目(#841)とも揃う |
| 3. Jev の規則(201〜205)の「どこが」 | 案 A: 定義の頭に付けるだけ。案 B(Jev に証拠の行も答えさせ、その行に印)は linter 側の変更で、判定の較正(#1040)と同じ枠で後から | 今の linter は定義単位で判定し、証拠の行を返さない |
| 4. source の箱 | 開いた時だけ印を描く(今の hidden の仕組みのまま)。範囲(start〜end)を level の色で下線 | source を常に出す案は面が重くなる(定義 1 万件)ので採らない |

戻し方: 面の変更だけ(linter の出力は変えない)。revert で戻る。
