# doeff-hy の code を読む面 — v3: val / var の書き分けと、実体ごとの source の表示

作成: 2026-09-28 23:3x。v2(`../v2/design.md`)への追加 2 点。v2 の他の決まりはそのまま生きる。
見本: `entity.html`(v2 の見本に 2 点を足したもの・画面 = `entity-render.png`)。

## 1. 追加の裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| 本体の束縛は **val / var の書き方**で、書き換えられるかどうかをはっきり見せる | "good, i want val/var style for mutability clearness" |
| 実体(defk など)ごとに、**元の source(Hy の文字)を出すボタン**を置く | "and a button to show raw source per defk (entity)" |

## 2. 本体の束縛の書き方(v2 2.2 の該当の行を置き換える)

意味の正本 = ADR-DOE-HY-006(`docs/design/defk-val-var-lazy/design.md`): val = 一度だけ束縛・var = 書き換えられる(書き換えは `:=`)・`<-` は val の別の書き方・setv は val / var へ移すよう警告。

| 元の form | 見せ方 | 意味 |
|---|---|---|
| `(<- x T e)` | `val T x ⇐ e` | 一度だけ。effect を通す矢印 |
| `(val x e)` | `val T x = e`(型が分からなければ `val ? x = e`) | 一度だけ |
| `(var x e)` | `var T x = e` | 書き換えられる |
| `(:= x v)` | `x := v` | 書き換え(val / var の語が無い行 = 書き換え。`:=` で `=` と区別する) |
| `(lazy val x e)` / `(lazy var x e)` | `lazy val T x = e` / `lazy var T x = e` | 初めて使った時に評価 |
| `(session val x e)` / `(session var x e)` | `session val T x = e` / `session var T x = e` | defhandler の直下だけ |
| `(setv x e)`(本体の中) | `setv x = e` + 警告の印(val / var へ) | 書き換えられるかどうかが読めない — linter の警告をここに出す |
| `(setv NAME e)`(module の直下) | `val NAME = e` のカード | ADR-DOE-HY-006 §6 の決定 7(module の直下は setv のまま) |

## 3. source の表示ボタン(v2 2.1 の頭の部品に足す)

- カードの頭の右端に `source` のボタン。押すとカードの中に、その実体の元の Hy の文字(source の行番号つき・色付け)が開く。もう一度押すと閉じる。
- 隣に `editor で開く`(source の file の該当の行へ)。v1 の「source を開く」はこれに統合する。
- 開いた source は読むためだけ(編集しない — v1 R2)。
- 索引が持つ実体の位置(file・開始と終わりの行)から切り出す。面が自分で file を歩かない(v1 制約 5)のは変わらず、切り出しは索引の口で行う。

## 4. 戻し方

- val / var の語は本体の文字の表の行を戻すだけ。
- source のボタンは部品 1 つを外すだけ。
