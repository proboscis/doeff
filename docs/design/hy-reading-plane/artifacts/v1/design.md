# doeff-hy の code を読む面 — 定義を単位に、軸で辿る(案 D)と、その先の言語

作成: 2026-09-28(operator との直接の議論・会話 defk-view-discuss)。
追跡: proboscis/agora-redesign #849(defk の型・effect・tags を editor で読みやすく見せる — 範囲をこの文書で「doeff-hy の code 全体を読む面」へ広げた)。
見本: `compare-now-B-D.html`(今の表示・案 B・案 D を実物の defk 5 本で並べたもの)・`scala3-kyo.html`(同じ物を Scala 3 + Kyo で書いた形)。

## 0. 全体像(今 ⇒ 目標)

```
今                                                目標(3 つの層)
─────────────────────────────────────────         ─────────────────────────────────────────────────────────────
file の木(controllers/messaging/core/…)            [見る層]  読む面(webview・doeff-runner)
   │ 開く                                             入口 = 軸の切り替えと交差: tag の各 key / effect / 呼び出し / 型 / テスト / 置き場
   ▼                                                  カード = 定義 1 つを Scala 3 の見た目に印字した物(印字器 = 将来の言語の印字)
editor(source の行)                                   カードの名をクリック → 隣の定義のカード(file を跨ぐという概念が無い)
   + 装飾 A(名の行・型の行・effect の行・ラベル)             ▲ 読む
   + 「タグで閲覧」(定義の一覧・tag の格子)            [構造の層]  系の構造 = 定義 × 属性(hy-index・doeff-indexer)
   + 目次・移動・参照(hy-index を読む)                   定義: kind・完全修飾名・引数と型・答えの型・effect・契約・docstring
                                                        属性: tags(key ごとに 1 軸・軸の数は開いている)・呼び出し・テスト・位置
source の Hy(正本・変えない)                             ▲ 読み込む(file を歩くのはここだけ)
doeff-linter の editor-json(file ごと)               [置き場の層]  Hy の file(git・agent が書く。定義の直列化の形式にすぎない)
                                                        doeff-linter の editor-json(file ごとに出た物を定義ごとに切る)
```

- 今の入口は file の木で、1 本の木は領域の軸を 1 つしか表せない(operator: "a tree structure can never cover the entire axis of domain")。
- 目標では **Hy の file は定義を置いておく形式**(operator: "hy lang is just a way to store data")で、**系の構造は定義 × 属性の面**にある(operator: "we want our doeff-hy to be in different plane as a system structure")。読む面は構造の層を見る道具で、file は置き場の層に降りる。
- 将来の言語では構造の層が正本になり得る(Unison の形)。今は file が正本で、構造の層は file から作る索引。

## 1. 決めたこと(operator の裁定・2026-09-28 22:1x〜23:1x)

| 決めたこと | 出所(逐語) |
|---|---|
| 見せ方は **案 D(読むための別の面)**。同じ editor の装飾(A)は今のまま残し、折りたたみ(B)と行間の差し込み(C)は作らない | "perhaps D is the way to go" / "to me D reads far better, than pure lisp" |
| 最終的には **新しい言語**(静的な型・型に載る effect・Python の呼び出し)を目指す。ただし今は D と linter で賄う | "I see, so ultimately we want new lang but for now we go with D and linter to cover" |
| 「linter で賄う」= 言語の compiler が担うはずの検査(effect の宣言の突合・契約・match の網羅)は、当面 doeff-linter が担い続ける | 同上 |
| 対象は defk だけでなく **doeff-hy の code 全体**を読む面 | "actually we are not only talking about defk but the whole doeff-hy code reading plane" |
| **単位は定義で、file と dir の構造に縛らない。**変数の scope や何かを file に結びつけるのは意味が無い | "I feel it's nonesense to make a scope of variable or anything bound to a file and file structure" |
| **入口は木ではなく軸。**木は領域の軸を全部は表せない | "because a tree structure can never cover the entire axis of domain" |
| **系は tag の複数の軸で組織する。**1 本の file の木ではない | "the system should be more organized across multiple axis of tags rather than a single file structure tree" |
| **Hy は定義を置いておく形式で、doeff-hy の系の構造は別の面にある**(定義 × 属性の面) | "so hy lang is just a way to store data but we want our doeff-hy to be in different plane as a system structure" |

読む面の細目(2.3 節)は席が推奨を 1 つ選んで既定にした。どれも表示だけの決定で戻せる(CLAUDE.md の two-way door の規則)。operator は細目に反対していないが、明示的に選んでもいない。

## 2. 読む面の設計

### 2.1 単位と軸

- **単位 = 定義**(defk・deff・defhandler・deftest・defrecord・defenum・defeffect・defwire・defn・defclass・最上位の setv)。1 つの定義 = 1 枚のカード。identity は完全修飾名(今は module の path + 名。将来の言語では file と独立の名前空間)。
- **軸 = 定義に付いている属性で、どれからでも入れる**。木(file と dir)は軸の 1 つの射影にすぎず、既定の入口にしない。
- **tags は key ごとに 1 軸で、軸の数は開いている。**今は `:context` と `:role` の 2 軸だが、新しい key(例: 業務の領域・寿命・持ち主)を足せば軸が増え、索引と面は key を列挙せずに扱う。

| 軸 | 何で辿るか | 今ある物 |
|---|---|---|
| tag: context | `:tags {:context …}` | 「タグで閲覧」の格子(hy-index) |
| tag: role | `:tags {:role …}`(entry → program → judgment → protocol → intent → type → foundation の層の順にも並ぶ) | 同上 |
| tag: その他の key | `:tags {…}` に足した key | (無し — key を固定しない索引にする) |
| effect | その effect を使う定義 / その effect を解く handler | editor-json の effects(#800)・effectProviders.ts |
| 呼び出し | 呼び手 ↔ 呼び先 | callGraph.ts・CodeLens「呼び出し元 N 箇所」 |
| 型 | その型を受ける / 返す定義(defrecord から辿る) | editor-json の signature |
| テスト | その定義を呼ぶ deftest | callGraph.ts(deftest も定義) |
| 置き場 | file・行(agent の報告・差分・traceback から辿るためだけ) | hy-index の位置 |

- 軸は交差できる(context = messaging × effect = ReadInput)。結果は定義の集合で、木ではない。
- 1 枚のカードから隣のカードへ(名をクリック)。同じ面に複数のカードを積める。file を跨ぐという概念は面に無い。

### 2.2 描く form の表(agora-controllers での出現数・2026-09-28)

| doeff-hy の form | 出現 | Scala 3 の見た目 |
|---|---|---|
| `defk` | 5,364 | `def f(x: T)(using E1, E2): R =` + 本体(effect の無い defk は `using` を省く) |
| `deff` | 380 | `def f(x: T): R =`(effect 無しの印。defk と区別できる目印を付ける) |
| `defn` | 248 | `def f(x): …`(型の無い素の関数 — 目印つき) |
| `defhandler` | 341 | `handler name(解く effect …)(足す effect …)` + effect ごとの `case E(a) => … resume(v)` |
| `deftest` | 1,890 | `test "name":` + 本体 |
| `defrecord` | 1,058 | `case class R(a: T, b: U)` + `/** doc */` |
| `defwire` | 151 | `case class W(…) derives Wire`(外との受け渡しの型 — 目印つき) |
| `defenum` | 19 | `enum E: case A, B` |
| `defeffect` | 175 | `effect E(fields…): Answer` |
| `defclass` | 334 | `class C(…)`(Python の class — 目印つき) |
| `fnk` | 23 | `(x: T) => …` |
| `check` / `session` / `handle` / `loop` / `do!` | 21 / 87 / 3 / 2 / 1 | 表に足すまで lisp のまま目印つき(2.4 の制約 2) |
| 本体の `<-` / `val` / `var` / `:=` / `setv` | 14,942 / 7,656 / 713 / 937 / 7,765 | `val x: T <- e` / `val x = e` / `var x = e` / `x = e` / `x = e` |
| 本体の `when` / `if` / `match` / `for` / `lfor` / `gfor` / `sfor` | 4,062 / 2,540 / 382 / 2,388 / 1,819 / 1,406 / 170 | `if … then` / `if … then … else` / `v match` + `case` / `for x <- xs do` / `[… for … in …]` / `(… for … in …)` / `{… for … in …}` |
| 最上位の `setv` | (7,765 の一部) | `val NAME = …`(定義として 1 枚のカード) |
| `import` / `require` | 9,201 / 1,145 | カードの「使う物」の欄(file の頭に並べない)。`require` は出さない |
| `;;;`(file の頭の説明)/ `;;` | 12,462 / 15,631 | module のカードの説明 / `//` |
| 自作の `defmacro` | 0 | — |

### 2.3 見え方の細目(席が既定にしたもの・戻せる)

| 項目 | 既定 | 退けた案 | 理由 |
|---|---|---|---|
| 面の出し方 | `.hy` を開いたら読む面(その file の定義のカード)。入口は軸の切り替え。source は切り替えで開く | 横に並べる(Markdown の preview の形) | operator は手で編集しないので source は必要な時だけ |
| effect の置き場 | Scala の通り 2 行目に `(using ReadInput, WriteInput): R =` | 頭を 1 行で閉じて下に小さく / 全部 1 行 | effect が引数と別の行に出る(#849 の「effect は型の行から外す」と合う) |
| 束縛 | `val x: T <- e`(effect を通す)と `val x: T = e`(値) | `x: T <- e` / `T x <- e`(今の表示) | 矢印の違いだけで effect を通したかが分かる |
| 本体の制御の形 | Scala の形 | lisp のまま | 閉じ括弧が消えるのは D だけの利点 |
| 式の綴り | Python のまま(`d[k]`・`a + b`・`[s for …]`・`x is None`・`f(a, key=v)`) | Scala に寄せる | 意味が 1 対 1。寄せると list / set / generator の内包の違いが消える |
| None を含む型 | `Option[T]` | `T \| None` | Scala の読み方 |
| 名前 | kebab のまま(`run-input-request`) | camelCase | 検索・linter の知らせ・source と同じ綴り |
| 型の名 | Python のまま(`dict`・`frozenset`) | Scala の名(`Map`・`Set`) | 今の実行時の型と同じ名。**言語が自前の標準の型を持つと決めた時に切り替える**(印字器の 1 か所で写像する) |
| tags | 小さな字の注釈の行 `@turn @judgment`(軸の入口でもある — クリックでその tag の集合へ) | 右端のラベル(今と同じ) | Scala の注釈の位置 |
| docstring | def の上に `/** … */` | 本体の頭に文字列のまま | Scaladoc の位置 |
| 本体の区切り | Scala 3 の字下げ | `= { … }` | 括弧を 1 つも出さない |

### 2.4 制約(議論で足したもの)

1. **読み戻せる印字にする。** 印字した文を読み戻すと元の Hy と等しい(印字器と将来の parser が互いに逆写像)。これで印字の出力がそのまま将来の言語の文法になり、5,364 本の defk の移行道具も印字器そのものになる。「読めればよい」で作ると、言語に進む時に印字器を作り直すことになる。
2. **描けない form は lisp のまま目印を付けて出す。** 変換の仕方が決まっていない form(2.2 の表に無い物)は推測で描かない。`(setv …)` を `val` と描くような嘘が一番の害。
3. **source の位置をカードに小さく残す。** agent の報告・git の差分・traceback は source の file と行で来る。`conversation_input.hy:85` から該当のカードの該当の行へ行ける。
4. **linter の知らせは違反の場所へ**(#849 の要件)。カードの行と source の行の対応を正確に持つ。問題の欄は source 側のまま。
5. **軸の情報は索引(hy-index)から取り、面が自分で file を歩かない。** 索引が定義 × 属性の正本(docs/design/hy-definition-index の決定と同じ)。

### 2.5 面が自前で持つ機能

- 軸の切り替えと交差(2.1 の表)。結果 = カードの一覧
- カードの名をクリック → 隣のカード。「呼び出し元 N 箇所」→ 呼び手のカードの一覧
- 束縛の型・effect の絵の hover
- 面の中の検索(名・型・tag)
- 「source を開く」→ 同じ場所の file の行

### 2.6 既にある物と足す物

| | 既にある(doeff-runner 0.6.27) | 足す |
|---|---|---|
| 索引 | hy-index(doeff-indexer: 定義の一覧・kind・tags・位置) | effect・型・呼び出しの属性を索引に載せる(今は editor-json と callGraph.ts に散る) |
| 入口 | 「タグで閲覧」(tag の格子)・目次・移動・参照 | 軸の切り替えと交差。「タグで閲覧」を軸の 1 つとして取り込む |
| 描画 | 装飾 A(名の行・型の行・effect の行)・呼びの置き換え `f(a, b)`(editor-json の rewrites) | 印字器(定義 1 つ → Scala 3 の見た目の文)。rewrites を土台にする |
| 面 | 無し | webview のカードの面 |

### 2.7 戻し方

- 読む面は設定で切れるようにする(切れば今の装飾 A と「タグで閲覧」だけになる)。全体を戻す時は該当の commit を revert する。
- 細目(2.3)はどれも印字器の 1 か所を変えるだけで戻る。

## 3. その先の言語 — 要件と候補(決めていない・記録だけ)

### 3.1 operator の要件(逐語)

- "i want scala level type system + algebraic effects support and maybe macro, and acceptable performance. and should be statically typed"
- "what i need is algebraic effects, and ability to call python like pyo3"
- "a language can be separated from runtime right? so with a new language we can both compile to python or binary"
- "Kyo looks cool but requires scala's for syntax and would require lifting per line in for <-" → effect を持つ行ごとの印(`<-`・`.now`)を書かない形が望ましい
- "I feel it's nonesense to make a scope of variable or anything bound to a file and file structure" / "because a tree structure can never cover the entire axis of domain" → 名前空間と scope を file に縛らない(Scala の package は file と独立。Unison は定義を内容の hash で持ち、名は属性)

### 3.2 数えた事実(2026-09-28・agora-controllers)

- `.hy` 907 file・`defk` 5,364・`<-` 14,942・`val` 7,656・`deftest` 1,890・`defrecord` 1,058・`defhandler` 341・`defeffect` 175・自作の `defmacro` 0(macro の集合は doeff-hy の物で閉じている)
- 外部の module は 10 個弱: httpx 11・pydantic 8・yaml 5・tomllib 4・websockets・aiohttp・jsonschema・sqlite3・certifi・pickle 各 1。重い Python(agent の起動・LLM の SDK)は doeff の package の側で、中身は subprocess と HTTP
- 役割の分布: judgment 2,499・entry 1,435・program 705・foundation 701・protocol 649・type 272・intent 216。純粋な論理(judgment・protocol・type・intent)= 3,636(56%)
- `:pre` / `:post` は見た限りほぼ `(: x T)` の型の注釈 — 静的な型があれば契約の仕組みと linter の契約の検査は消える
- Hy の file は他にも agent-control-plane 240 以上・proboscis-ema 171 以上・pr-review 112 以上・argus 66 以上・herdr-hud ほか、10 repo 以上(深さ 4 までの数)。加えて defadr(ADR も Hy)・doeff-linter・VS Code 拡張・skill が Hy に結びついている
- doeff-vm-core(Rust 7,099 行)は pyo3 が optional feature で、effect の runtime は既に Python から切り離せる設計

### 3.3 言語と runtime の分離 — 共通の物と backend ごとの物

| 層 | Python に compile | binary に compile |
|---|---|---|
| parser・型検査・effect の検査 | 共通(1 回書く) | 同左 |
| effect の handler の仕組み(限定継続) | generator か doeff-vm | doeff-vm-core |
| memory の管理 | Python の GC | Rust に変換すれば Rust の所有権。LLVM 直なら GC を自作(年単位) |
| 標準の型 | Python の型を借りる | 自前。Python の `dict` を言語の型にすると binary 側で Python の runtime を書き直す |
| Python の呼び出し | 素通し | pyo3 の形の FFI(`Py[T]`・変換・GIL) |
| 意味の細部(int の桁・文字列・dict の順・float・例外) | Python から借りる | 言語の仕様として全部決める |
| 名前空間 | Python の module = file | 自前(file と独立) |

分離はできる。安いのは front-end と effect の runtime。高いのは標準の型・memory・意味の仕様で、2 つ目の backend を持った瞬間に発生する。

### 3.4 候補の評価

| 候補 | 型 | effect が型に載る | 行ごとの印 | macro | 性能 | Python の呼び出し | 名前空間 | 成熟 | agent の知識 |
|---|---|---|---|---|---|---|---|---|---|
| Scala 3 + Kyo + ScalaPy | 高 | 載る(`A < (E1 & E2)`) | `.now` が要る(直接形。for 構文は不要) | inline・macro | JVM。Scala Native で binary も | ScalaPy(CPython を埋め込む) | package(file と独立) | 高 | Scala 高・Kyo 中 |
| Scala 3 の capability + capture checking | 高 | 載る(実験段階) | 無し | 同上 | 同上 | 同上 | 同上 | 実験 | 低 |
| Flix | Scala 風 | 載る | 無し | 無し | JVM | Java 経由(Jep) | namespace | 研究発・小 | 低 |
| Effekt | Scala 風の構文 | 載る | 無し | 無し | JS・LLVM・Chez | 無し | module | 研究・小 | 低 |
| Koka | 中 | 載る(row) | 無し | 無し | C + Perceus | C の FFI だけ | module = file | 研究・小 | 低 |
| Unison | 高(Haskell 風) | 載る(abilities) | 無し | 無し | 自前 | 無し | 内容の hash・名は属性(file が無い) | 小 | 低 |
| OCaml 5 | 高(型クラス無し) | 載らない | 無し | ppx | 速い | pyml | module = file | 高 | 中 |
| Rust + doeff-vm-core | 中 | 載らない | — | 高 | 速い | pyo3 | mod(file とほぼ同じ) | 高 | 高 |
| 自作 | 自由 | 自由 | 自由 | 自由 | backend 次第 | 自前 | 自由 | 0 | 0 |

- 「印なし + Scala 級の型 + Python の呼び出し」を全部満たす成熟した物は 2026-09 時点で無い。Kyo の直接形は印 1 つ(`.now`)で妥協した所に立つ。印なしは言語が effect を持つ必要がある(Effekt / Flix / Koka / Unison、または Scala の capability)。
- 「定義を file に縛らない」を一番徹底しているのは Unison(定義は内容の hash、名と置き場は属性、editor は面から定義を出し入れする)。読む面の 2.1 の形はこれの読み専用版。
- 自作の高い所は backend ではなく front-end(effect row の推論つきの型検査は博士論文 1 本分。Koka は 1 人で 10 年超、Effekt は 1 研究室で 6 年)。型検査の健全性の bug は黙って通る。
- Kyo の直接形の制約: for ループの本体で effect を使うには `Kyo.foreach` か `while`。macro の中の非局所 return は避ける。印の付け忘れは compile で止まる(doeff では実行時)。
- 見本(`scala3-kyo.html`): effect の定義(ArrowEffect)・record と契約(型)・Program(`defer` + `.now`)・handler(本番は ScalaPy・テストは Map)・テスト・compiler が止める誤り 4 つ・対応表・得失。

### 3.5 未決(言語に進む時に決めること)

- 印なしを要件に置くか(置くなら Kyo は外れ、Effekt / Flix / Unison / Scala の capability / 自作になる)
- backend を Python + Rust 変換に絞るか(LLVM 直は memory の自作を伴う)
- 標準の型を Python の型にするか自前にするか(読む面の型の名に跳ね返る)
- 名前空間を file からどこまで離すか(Scala の package 程度か、Unison のように定義を内容で持つか)
- 実験: controller 1 本(conversation_input・defk 8 本ほど)を候補で書いて、agent の精度・compile の周回時間・Python の呼び出しの痛さを測る(operator はまだ選んでいない)

## 4. 次の手順

1. main が読む面の実装の issue を立てる(2 節の単位と軸・form の表・制約 5 つを受け入れ条件に)。
2. 言語は別の issue に 3 節の要件と未決を写す(着手しない)。
