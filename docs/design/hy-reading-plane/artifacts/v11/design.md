# doeff-hy の code を読む面 — v11: 本体の文字の網羅 — lisp のまま残る form をできるだけ表に足す

作成: 2026-09-29 10:0x〜10:2x。v10(`../v10/design.md`)への追加 1 点。v10 までの決まりはそのまま生きる。
材料: `lisp-tally.md`(agora-controllers の `controllers/*/core/*.hy` 158 本に本線の linter 14cffa97 を当てて数えた表・
数える script = `lisp_tally.py`)。

## 1. 裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| 本体の文字は、表に無い form を lisp のまま出す今の形(v2 2.2 節)を保ちつつ、**表をできるだけ広げて読みやすい形に直す** | "and yeah try to convert to readable form as much as possible" |

席が示した「1 段目 / 2 段目」の分け方(先に do・cond・式の if・continue / return / raise・try / except・while、後で fn・内包・cut)は
operator が「できるだけ」で全部を採ったので、段は順番の目安にすぎない。**目標 = 実物で lisp のまま残る行を最小にする**。

## 2. 今の数(実物)

| | 行 |
|---|---:|
| 本体の行 | 12,264 |
| lisp の目印が残る行 | 3,524(29%) |

頭の form ごとの行数は `lisp-tally.md`。`setv`・`<-`・`when`・`:=`・`val`・`True`・`=`・`.append`(約 1,100 行)は表にある form だが、
`cond` / `do` / `try` の中に入っているために巻き込まれている。

## 3. 表に足す form と形(席の既定・形は戻せる)

| form | 行 | 形(v2 の決まり「Python 寄り・式は 1 行」に合わせる) |
|---|---:|---|
| `do` の塊 | 224 | `do` の字は消し、中の form をそのまま 1 段深く並べる(match の腕・if の枝の中の `do` も同じ) |
| `cond` | 137 | match と同じ縦の形: 条件 `→` 値(`→` は腕の最大幅で揃える・v5 U3 の決まり)。最後の `True` の腕は `else →` |
| 式の中の `if` | 342 | `x if c else y`(3 項)。枝が 1 行に収まらなければ縦の `if` / `else` に開く |
| `continue` / `return` / `raise` | 178 | keyword の色で `continue` / `return x` / `raise ValueError(…)` |
| `try` / `except` / `finally` | 58 | 縦の形 `try` / `except re.error as e` / `finally`(中身は 1 段深く) |
| `while` | 30 | `while reason is None`(中身は 1 段深く) |
| 式の中の `fn` | 87 | `ask ⇒ …`(lambda の矢印。引数が組なら `(a, b) ⇒ …`) |
| 式の中の内包 `lfor` / `gfor` / `sfor` / `dfor` | 93 | `[x for x in xs if p]` / `(…)` / `{…}` / `{k: v for …}`(U3 の内包の形を式の中でも使う。収まらなければ縦) |
| `cut` | 72 | `xs[a:b]` / `xs[a:]` / `xs[:b]`(step があれば `xs[a:b:s]`) |
| 比較・論理の頭(`=`・`!=`・`and`・`or`・`not`・`in`・`is`・`is-not`・`+` など) | 約 350 | Python の中置(`a == b`・`a and b`・`x not in xs`)。これらは cond の条件と `if` の条件に多い |
| `isinstance`・`tuple`・`.append` などの素の呼び | 約 150 | 呼びの形 `f(x)` / `xs.append(x)`(U2 の呼びの読み手に載る) |
| `;;` の註(本体の途中) | 34 | `# …` の註の色で残す |

- 直しても lisp のまま残る物: 知らない macro・`unquote` などの読み手の外・1 行に収まらない式の島。これは v2 の決まり(推測で描かない)のまま。
- 網羅の測り方 = `lisp_tally.py` を同じ 158 本に当てて、残る行の数と頭の内訳を便ごとに #910 に書く(目標: 3,524 → 500 未満)。

## 4. 戻し方

- 表は `packages/doeff-linter/docs/SPECIFICATION.md` 20 節と `src/project/body_view.rs` の 1 か所。form ごとに行を消せば元の lisp の島に戻る(面は描くだけ)。
