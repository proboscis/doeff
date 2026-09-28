# doeff-hy の code を読む面 — v2: 構文をなぞらず、実体を HTML の部品で見せる

作成: 2026-09-28 23:1x(operator の追加の裁定を受けて v1 = `../v1/design.md` を改める)。v1 の 0 節(3 層の全体像)・1 節の決めたこと・2.1 節(単位と軸)・2.2 節(描く form の表)・2.4 節の制約 2〜5・2.5〜2.7 節・3 節(その先の言語)はそのまま生きる。**v1 の 2.3 節(Scala の構文の細目)と制約 1(読み戻せる印字)をこの文書が置き換える。**
見本: `entity.html`(実物の run-input-request・judged・ReadInput・Judgment・conversation-input-store を実体のカードにしたもの・画面 = `entity-render.png`)。

## 1. 追加の裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| **Scala の `using` のような構文はなぞらない。defk のような実体を HTML でよりよく見せる** | "so we are not to follow syntax like using. we want better html visualization of entity like defk" |
| **Hy は data と code を自由な構造で置いておく保存の形式として、この用途に向いている**(置き換える対象ではない) | "in my understanding hy is great at storing arbitrary data and code in freely structured manner, for our purpose" |

v1 で「Scala 3 の見た目に印字する」と書いた所は、「実体の部品を HTML で見せる。文字で出すのは本体だけ」に読み替える。v1 の見本 `compare-now-B-D.html` の案 D と `scala3-kyo.html` は「頭に何を出したいか」「言語ならどうなるか」の参考として残す。

## 2. 実体のカード

### 2.1 部品(実体の種類ごとに欄が変わる)

| 実体 | 頭 | 欄 | 本体(文字) | 関係(下の帯) |
|---|---|---|---|---|
| defk / deff / defn | 種類のバッジ・名・tags のチップ | 引数のチップ(名と型)→ 答えの型 / 使う effect(絵・それを解く handler へのリンク・経由の定義)/ 契約(型でない述語だけ。無ければ欄ごと出ない)/ 説明 | あり | 呼び手・呼び先・テスト・使う型・置き場(file と行) |
| defeffect | 同上 | 欄のチップ → 答えの型 / 説明 | 無し | 使う定義・解く handler・置き場 |
| defrecord / defwire | 同上 | 欄のチップ(名と型)/ 説明 | 無し | 返す定義・受ける定義・置き場 |
| defenum | 同上 | 値のチップ | 無し | 使う定義・置き場 |
| defhandler | 同上 | 解く effect / 使う effect / 説明 | effect ごとの本体 | 被せる場所・置き場 |
| deftest | 同上 | 被せる handler(偽物) | あり | 呼ぶ定義・置き場 |
| 最上位の setv | 同上 | 型(分かれば) | 式 1 行 | 使う定義・置き場 |

- 契約のうち型(`(: x T)`)は引数と答えの型の中へ溶ける。型でない述語(長さ・範囲など)だけ「契約」の欄に残る。
- tags は key ごとに 1 チップ(`context: messaging`・`role: program`)。チップをクリックするとその軸の集合へ(軸の入口を兼ねる)。
- effect の絵は pixel art(#849 の装飾 A と同じ絵)。

### 2.2 本体の文字の決まり(文字で出すのはここだけ)

| 元の form | 見せ方 |
|---|---|
| `(<- x T e)` | `T x ⇐ e`(effect を通す矢印。行の背景を薄く付ける) |
| `(val x e)` / `(setv x e)` / `(var x e)` / `(:= x e)` | `T x = e`(型が分かれば)/ `? x = e`(型が分からない時。別の型で埋めない) |
| `(! (E a))` / `(E a)` | 絵 + `E(a)` |
| `(f a b)` / `(f a :k v)` / `(.m o a)` / `(get d k)` / `(. o a)` | `f(a, b)` / `f(a, k=v)` / `o.m(a)` / `d[k]` / `o.a`(#849 の呼びの置き換えと同じ) |
| `(when c …)` / `(if c a b)` | `when c` + 字下げ / `if c … else …` |
| `(match v (A) x _ y)` | `match v` + `A → x` / `_ → y`(矢印で揃える) |
| `(for [x xs] …)` / `lfor` / `gfor` / `sfor` | `for x in xs` + 字下げ / `[… for x in xs]` / `(… for …)` / `{… for …}` |
| `(return v)` / `(resume v)` | `return v` / `resume v` |
| 表に無い form | lisp のまま目印を付けて出す(推測で描かない) |
| 行番号 | source の行(報告・差分・traceback から辿るため) |

### 2.3 v1 から退けた物

- `def f(x: T)(using E): R =`・`/** … */`・`=`・Scala 3 の字下げ・`Option[T]`・注釈の行 `@turn` — 構文をなぞる細目は全部退ける。
- 制約「読み戻せる印字」は実体のカードには当てない(カードは言語の文法ではない)。本体の文字の表(2.2)は将来の言語の式の綴りと揃えられるが、それは言語の issue で決める。

## 3. 「その先の言語」への含意

Hy が保存の形式として残るなら、言語の仕事は「新しい表面の構文」ではなく、次の 2 つに分かれる。

1. **Hy の data の上の検査**(型・effect の宣言の突合・match の網羅)= 今は doeff-linter が担う(v1 の R6)。静的な型に進むなら、この検査を強くする(型の推論・効果の推論)。
2. **見る面** = この文書の実体のカード。

新しい表面の構文(Scala 風の文法・parser)が要るのは、agent が書く側の精度や、runtime の分離(binary)を求めた時だけ。v1 の 3 節の候補の表は、その時のために残す。

## 4. 戻し方

- 実体のカードは設定で切れる(切れば装飾 A と「タグで閲覧」だけ)。
- 2.1 の欄と 2.2 の文字の決まりは、それぞれカードの部品 1 つ・印字の表 1 行を変えるだけで戻る。
- この v2 を取り消す時は superseding の decision を足す(v1 の記録は書き換えない)。
