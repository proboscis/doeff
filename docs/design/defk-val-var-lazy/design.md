# val・var・lazy val・lazy var・session val・session var(doeff-hy)

- 状態: 実装済み(2026-09-26)。条文 = `docs/adr/defadr_doeff_hy_006_val_var_lazy.hy`(ADR-DOE-HY-006)
- 実装: `packages/doeff-hy/src/doeff_hy/binding_forms.py`(本体の書き換えと検査の定義点)・`lazy.py`(実行時の入れ物)・
  `session.py`(セッションの値の保存先のキー)・`macros.hy`(defk / deftest / module の直下)・`handle.hy`(defhandler)・
  `static_check.py`(doeff-hy-check の警告と赤)
- 検: `packages/doeff-hy/tests/test_val_var_lazy.py`・`test_static_check.py`・ADR-DOE-HY-006 の enforcement

## 1. 目的と operator の原文(2026-09-26)

- "i do want scala-semantic lazy and val var for doeff defk/deftest, so user can do like (lazy val some_variable !(effectful-expr))"
- "ah yes val as default and enforce var, that sounds good"
- "1. setv should be warned to use var/val. 2. we need val,var,lazy val, lazy var"
- "and we should actually migrate defhandler's lazyval and lazyvar to be more clear about the difference" → "yeah session it is..."
- "also, lets implement the session/lazy val var for defhandler/deftest/defk. for defhandler, we want to keep lazyval/lazyvar behavior unaffected for now and warn to migrate to session val / session var"
- "so that each handler's session values can be controlled by outer handler via effects" → 訂正 "i mean, value put/get itself is state-monad == effects so"
- "oh btw perhaps we want lazy val var for global scope, dont we?" → "i mean instead of setv we want val/var used"
- "hmm, but this makes me feel that what's the point of having lazy val if it can't use effects... but yeah you are right, global context has no handler so it must not have effectful state ments." → "nonono have lazyval"

名前の束縛を「一度だけ(val)」を既定にし、書き換える名前は var と宣言させる。値の遅延(lazy)と、セッションで共有する値(session)を
別の語に分けて、今まで同じ `lazy-val` の語に混ざっていた 2 つの意味(1 回の呼び出しの中の遅延と、セッションをまたぐ共有)をはっきりさせる。

## 2. 意味

| 形 | 書ける所 | 意味 |
|---|---|---|
| `(val x 式)` | defk・deftest・defhandler の節の本体・module の直下 | 一度だけ束縛する。同じ名前をもう一度束縛したら展開の誤り |
| `(var x 式)` | 同上 | 書き換えられる。本体の中の書き換えは `(:= x 新しい値)`。module の直下の書き換えは setv(§6 の決定 7) |
| `(lazy val x 式)` | 本体の一番外の並び・module の直下(効果を使わない式だけ) | Scala の lazy val。その呼び出し(defk の 1 回・deftest の 1 回・defhandler の節の 1 回の実行)の中で x を初めて使った時にだけ評価し、以後は覚えた値。使わなければ評価しない。初回が例外なら覚えず、次に使った時にもう一度評価する |
| `(lazy var x 式)` | 本体の一番外の並び | 初めて使った時に評価し、以後は `:=` で書き換えられる。初めて使う前に `:=` で書き換えたら初期値の式は一度も評価しない |
| `(session val x 式)` | defhandler の直下(節と並べる所)だけ | doeff のセッションの間、初めて使った時に 1 回だけ作って共有する(旧い `lazy-val` と同じ意味) |
| `(session var x 式)` | defhandler の直下だけ | セッションで持ち越す書き換えられる状態(旧い `lazy-var` と同じ意味)。書き換えは `(:= x v)` で、保存先への書き戻し(Put)は macro が宣言から判断する |
| `(<- x 効果)` | 今までどおり | val の別の書き方として扱う(束縛し直しの検査の対象) |

- 式は `!` で効果を書ける: `(lazy val x !(effectful-expr))`。
- val が禁じるのは**名前の束縛し直し**だけ。値の中身の書き換え(`(setv (get x k) v)`・`(.append x v)`)は対象外。
- lazy val / lazy var の参照は裸の名前(Scala と同じ)。macro が参照を「初回なら評価して覚える」式に書き換える。
- session val / session var は defk と deftest では使えない(隠れた状態を defk に持ち込まないため)。

## 3. 構文と reader の実測

Hy 1.3.0 の reader の実測(`packages/doeff-hy/tests/test_val_var_lazy.py::test_bang_written_as_two_tokens_is_the_same_as_the_bang_form` に固定):

| 書いた字面 | reader が返す形 |
|---|---|
| `(lazy val x !(f a))` | `lazy` `val` `x` `!` `(f a)` の 5 要素 — `!` と `(f a)` は別の要素 |
| `(val x ! (f))` | `val` `x` `!` `(f)` — 空白の有無で変わらない |
| `(:= x 1)` | 先頭が Keyword `:=`(Symbol ではない) |
| `!x` | Symbol `!x`(1 つの名前 — `!` の印ではない) |
| `client.get` | `(. client get)` |

したがって宣言と `:=` の値の部分に限り、`! 式` の 2 つ組を `(! 式)` に畳む。`(g !(f))` のような一般の呼び出しの引数は畳まない
(引数の数が変わるため)。`!x` は印にならないので、変数の効果は `!(x)` ではなく `(! x)` と書く。

`(:= …)` は先頭が Keyword なので Hy の macro を通らない。本体の中は defk / deftest / defhandler の macro が本体を先に歩くので書き換えられるが、
**module の直下の `(:= x v)` は Hy が keyword の呼び出し `Keyword('=')(x, v)` として compile し、何も書き換えない**(実測)。
doeff-hy-check はこれを赤にする。

## 4. 展開の形

### 4.1 実行時の展開

| 形 | 展開 |
|---|---|
| `(val x e)` / `(var x e)` | `(setv x e)` |
| `(:= x v)`(var) | `(setv x v)` |
| `(lazy val x e)` | `(setv _doeff_lazy_x (LazyCell (fn [] (when False (yield None)) (return e'))))` — e' は e の `!` をその場の yield にした式 |
| lazy の参照 `x` | `(if (. _doeff_lazy_x ready) (. _doeff_lazy_x value) (yield ((. _doeff_lazy_x force))))` |
| `(:= x v)`(lazy var) | `((. _doeff_lazy_x assign) v)` |
| `(session val x e)` | 旧い `lazy-val` と同じ注入: x を参照する節の頭で `(Get key)`、`Some` でなければ e を評価して `(Put key (Some x))` |
| `(:= x v)`(session var) | `(do (setv x v) (yield (Put _lazy_x_key (Some x))))` |
| module の直下の `(lazy val x e)` | `(setv __doeff_module_lazies__ (with-module-lazy (globals) "x" (fn [] e)))` と、書き手が `__getattr__` を持たなければ `(setv __getattr__ (module-getattr (globals)))`(どちらも module の直下の束縛 — 関数が引数の名前空間を書き換えない)。同じ file の defk・deftest・defhandler の本体の参照は `((. (get (get (globals) "__doeff_module_lazies__") "x") get))`、他の module からの参照は PEP 562 の module の `__getattr__` |

- `LazyCell` は呼び出しごとに作る局所の値(本体の局所変数と同じ寿命)。`force` は初期値の関数を doeff の `do` で包んで実行し、成功した時だけ覚える。
- session の値は Python の隠れた場所(module の変数・handler の object・閉包)に持たず、必ず状態の効果(Get / Put)を通して読み書きする。
  キーは `doeff_hy.session.session_key(module, handler, name)` = `"<module の __name__>/<handler の名>/<変数の名>"`(名は Hy の綴りのまま)。
  旧い `lazy-val` / `lazy-var` と同じ文字列なので、旧い形から書き換えても同じセッションの値はそのまま引き継がれる(§6 の決定 9)。
- 外側の handler は、このキーの `Get` に値を答えれば初期化の式を走らせずに値を差し替えられ、`Put` を受ければ初期化と書き換えを観測できる
  (`test_outer_handler_answering_get_skips_the_session_initialiser_and_sees_put`)。

### 4.2 型検査のための展開(doeff-hy-check)

lazy val / lazy var(本体の中と module の直下)は書いた所で評価する `(setv x e)` に写し、参照は裸の名前のまま(型は同じ)。
session val / session var は実行時と同じ Get / Put の注入。code-quality の Hy の投影(`quality/hy_bindings.py`)も同じ写し(§9)。

## 5. 誤り(展開を止める)と警告(止めない)

展開の誤り(SyntaxError → Hy の HyMacroExpansionError)は修正の案内を含む文にする(書き手は agent なので、文がそのまま修正の指示になる)。

| 場合 | 扱い |
|---|---|
| 新しい構文の名前の束縛し直し(`(val x …)` を 2 回・val の名前を setv / `<-` / for で束縛・var の名前を setv で束縛) | 展開の誤り |
| val / session val への `:=`、宣言していない名前・setv で作った名前への `:=` | 展開の誤り |
| lazy の名前の影(for・let・fn の引数・内包表記の変数・with・except で同じ名前) | 展開の誤り |
| lazy の参照が fn・内包表記・入れ子の定義(defk・defhandler・handle の節など)の中 | 展開の誤り(「先に `(val x-value x)` で取り出す」と案内) |
| lazy の参照が宣言より前(自分の式の中を含む) | 展開の誤り |
| lazy の宣言が if・when などの中 | 展開の誤り(本体の一番外の並びにだけ書ける) |
| defk・deftest の旧い `lazy` / `lazy-val` / `lazy-var` / `set!` | 展開の誤り(新しい形を案内) |
| defk・deftest の `session val` / `session var` | 展開の誤り |
| defhandler の直下の `(val …)` / `(var …)` / `(lazy val …)` | 展開の誤り(節の本体か session を案内) |
| module の直下の `lazy var`・`session`・効果を使う式・同じ名前の 2 回目 | 展開の誤り |
| defn・fn・class・let の中の `(val …)`(本体の macro の外) | 展開の誤り(関数は defk で書く) |
| 本体の中の `:=` で module の var を書き換える | 展開の誤り(効果の状態か session var を案内) |
| defhandler の旧い `lazy` / `lazy-val` / `lazy-var` / `set!` | **動きは変えず**、展開の時に `DeprecationWarning` + doeff-hy-check の警告(`doeff-hy-legacy-lazy`) |
| 本体の setv(名前を束縛する物)・module の直下の setv | doeff-hy-check の警告(`doeff-hy-setv`) |
| 旧い書き方どうしの同じ名前の束縛し直し(setv / `<-` / 拡張代入) | doeff-hy-check の赤(`doeff-hy-rebind`)— §8 の 2 段階の 1 段目 |
| module の直下の val の名前の setv・module の直下の `(:= …)` | doeff-hy-check の赤 |

束縛し直しの数え方(doeff-hy-check の赤):

- for の変数・with / except の変数は数えない。引数(defk の引数・deftest の fixture・節の欄と effect / k)は最初の束縛として数える。
- 互いに排他な枝(if の then と else・cond の各枝・try の本体と except の各節・match の各節)の束縛どうしは数えない
  (`(if c (setv r 1) (setv r 2))` は赤にしない)。枝の前か後の束縛とは数える。
- ループの本体の中の束縛は 1 回の繰り返しの分として数える(`(for [x xs] (setv y (f x)))` は赤にしない。前で束縛した名前をループの中で束縛し直すと赤)。
- 拡張代入(`+=` など)は常に書き換えとして赤。
- 入れ子の fn・内包表記・do の文脈(fnk・do!・for/do)の中の束縛は数えない(別の関数の scope)。

## 6. 決めた項目(two-way door)と理由・戻し方

operator が決めた項目: 構文の語(val・var・lazy val・lazy var・session val・session var)、val が既定で var を強制、defhandler の旧い形は動きを変えず警告、
session の値は状態の効果(Get / Put)を通すこと、module の直下の val / var と lazy val(効果を使わない式だけ)。

main 席(依頼の文)が決めた項目:

| # | 決めたこと | 理由 | 戻し方 |
|---|---|---|---|
| 1 | 書き換えの語は `(:= x v)` | Scala の再代入 `x = v` に近く、Python の `:=` とも読みが合う | `binding_forms.parse_assignment` の頭の判定を別の語へ替える |
| 2 | lazy var を使う前に `:=` で書き換えたら、初期値の式は評価しない(捨てる) | 使わない値のために効果を走らせない(lazy の目的) | `LazyCell.assign` の前に `force` を呼ぶ |
| 3 | lazy の参照は裸の名前 | Scala と同じ。`(force x)` の類を書かせない | `_lazy_reference` を外し、参照の関数を足す |
| 4 | `<-` は残し、val の別の書き方として扱う | 既存の数万の `<-` を書き換えずに済む | `_effect_bind` の束縛を数えないようにする |
| 5 | setv の警告は defk・deftest・defhandler の節の本体と module の直下の setv(名前を束縛する物だけ) | 添字・属性への代入は値の中身の書き換えで val の対象外 | `_setv` / `module_findings` の所見を外す |
| 6 | module の直下の lazy val は効果を使わない式だけ・module の直下の lazy var は作らない | module には handler が無く、最初に使った呼び出しの handler の下で作った値が、以後の別のセッション・別の handler の組でも使い回される。書き換える状態は var か defhandler の session var | `module_declaration` の誤りを外す |

席(この実装の担当)が決めた項目:

| # | 決めたこと | 理由 | 戻し方 |
|---|---|---|---|
| 7 | module の直下の var の書き換えは setv(var の名前の setv は警告しない) | `(:= …)` は module の直下では Hy の macro を通らない(§3 の実測)。本体の中から module の var を `:=` で書き換えるのは隠れた状態になるので誤り | reader macro などで module の直下の `:=` を扱えるようにした時に、`module_findings` の var の免除を外す |
| 8 | 束縛し直しの赤は互いに排他な枝どうし・for の変数・ループの 1 回分を数えない | 1 つの実行の経路で 2 回束縛する物だけが「束縛し直し」 | `_branch` / `_loop` で状態を合流させず直列に歩く |
| 9 | session val / session var のキーは旧い lazy-val / lazy-var と同じ文字列 | 旧い形から書き換えても同じセッションの値が引き継がれる(移行が 1 行の書き換えで済む) | `session_key` の形を替える(旧い形との引き継ぎは切れる) |
| 10 | defhandler の旧い形の警告は `DeprecationWarning`(展開の時・`(pass)` の廃止の警告と同じ仕組み)と doeff-hy-check の警告の両方。setv の警告は doeff-hy-check だけ | 旧い lazy は 115 節・set! は 61 節で数が少ない。setv は本体だけで 3 万 6 千件あり、展開ごとの Python の警告にすると出力が埋まる | `_extract-lazy-clauses` と `set!` の `warnings.warn` を外す |
| 11 | handle / defhandler の節は外の defk の lazy を参照できない(先に val で取り出す) | 節の本体は handle の macro が別に書き換える。外の書き換えは届かない | 節の書き換えに外の lazy の名前を渡す |
| 12 | module の直下の lazy val は、同じ module の defn・deff・module の直下の式の裸の参照からは使えない(NameError) | 書き換えが届くのは doeff-hy の macro の本体だけ。他の module からは PEP 562 で引ける | deff の macro にも本体の書き換えを通す |
| 13 | 節の `:when` の番が読む session の値(session val / var・旧い lazy-val / lazy-var)は、番の前に取り出す。番が読まない名前は今までどおり番が通った後(本体が使う節でだけ) | 番も「使う所」なので、lazy の意味(初めて使った時に作る)のまま番の前で作るのが正しい。誤りにして案内する案は、handler の状態で番を掛ける普通の書き方(`:when (< used limit)`)を禁じるので採らない。直す前は取り出しが番の後にあり、番の中の名前は節の関数の局所変数として未定義(UnboundLocalError)だった(2026-09-26・main 席の依頼・席が決定) | `handle.hy` `_build-clause` の guard-prefix を lazy-prefix へ戻す |

module の直下の lazy val の経緯: いったん「効果を使わない lazy の利点は import の時の計算を遅らせるだけ」として外したが、
operator の指示 "nonono have lazyval"(2026-09-26)で、効果を使わない式に限って許す形へ戻した。外した時の原文 = "hmm, but this makes me feel that
what's the point of having lazy val if it can't use effects... but yeah you are right, global context has no handler so it must not have effectful state ments."

## 7. session の値と外の handler

- 仕組みは doeff の状態の効果(Get / Put = state monad)のまま。専用の型つきの効果は作らない(operator の訂正 "value put/get itself is state-monad == effects so")。
- 外の handler と検査は `doeff_hy.session.session_key(module, handler, name)` でキーを引く。README と本記録に形を明記し、キーの形を変える時は ADR を改める。
- 提案(今回は作らない・operator の判断に回す): 型のある専用の効果(例 `SessionValue(handler, name)`)は、キーの文字列を組む手間と綴り違いを型で防げる。
  足すなら Get / Put と二重にならないよう、既定の handler がその効果を Get / Put へ写す 1 段にし、session val / session var の展開はその効果だけを出す形にする。

## 8. 移行と 2 段階

### 8.1 defhandler の旧い形

動きは一切変えない。展開の時に `DeprecationWarning` を出し、doeff-hy-check が `doeff-hy-legacy-lazy` の警告を出す。
書き換え: `(lazy x …)` / `(lazy-val x …)` → `(session val x 式)`、`(lazy-var x …)` → `(session var x 式)`、`(set! x v)` → `(:= x v)`。
旧い lazy の本体は複数の形を並べられたが、新しい形は式 1 つなので、手順が要る時は `(do …)` で包む。

### 8.2 defk の旧い lazy

旧い `lazy` / `lazy-val` / `lazy-var` / `set!` は defk の中でもセッションをまたぐ状態の意味だった。新しい `(lazy val …)`(その呼び出しの中の遅延)と
意味が衝突するので、展開の時点で誤りにした。本線の 6 repo(§10)で defk・deftest の中の使用は 0 件なので、壊れる所は無い。

### 8.3 束縛し直しの強制の 2 段階

1. **今回**: 新しい構文どうし(`(val x …)` を 2 回など)と、新しい構文の名前を旧い書き方で束縛し直すのは展開の誤り。
   旧い書き方どうしの束縛し直しは doeff-hy-check の赤(`doeff-hy-rebind`)。
2. **後で**: 旧い書き方どうしの束縛し直しも展開の誤りにする。条件 = 対象の各 repo の本線で `doeff-hy-rebind` が 0 件
   (測り方 = 各 repo の本線で `doeff-hy-check` を .hy 全体に当てて数える)。手順 = `binding_forms._Walker._bind` と `_expression` の
   拡張代入の枝の `self._note(RULE_REBIND, …)` を `raise self._error(…)` に替え、ADR-DOE-HY-006 の R6 を改めて、同じ便で各 repo の doeff の
   pin を上げる。

### 8.4 既存の品質検査との関係(2 段階の 1 段目の置き場)

依頼の前提は「doeff-hy-check の赤が code-quality の変更した file だけの検査に出て、触った file から直っていく」だったが、実測すると合わない:

- code-quality(各 repo の着地の門・pre-commit・日次)は doeff-hy-check を呼ばない。自前の Hy の投影(`quality/hy_projection.py`)で macro の形を写して pyright に渡す。
- doeff-hy-check はどの門にも配線されていない(`.agents/land-queue.toml`・pre-commit・CI を grep して 0 件)。

したがって今回の赤は doeff-hy-check を手で当てた時にだけ見える。

推奨(別の便): code-quality の Hy の投影に束縛し直しの規則(`hy-rebind`)を足す。code-quality は「触った行に足した違反だけを新しい違反として赤にし、
既存の違反は基底で扱う」ので、「触った所から直っていく」はこの形で実現できる。ただし日次の全体検査(`--scope all`)は既存の違反を件数で照合するので、
入れる前に各 repo の既存の件数(§10 の `doeff-hy-rebind`)を基底へ登録する作業が要る。

今回 code-quality に入れたもの: 新しい構文の投影(`quality/hy_bindings.py` — 宣言と `:=` を setv に写す。入れないと `(val x …)` が
未定義の `val(x, …)` の呼び出しとして偽の赤になる)と、deftest の本体の `!` の投影(macro は展開するのに投影が展開していなかった)。

**門へつないだ(2026-09-26 の続きの便)**: 推奨どおり code-quality の Hy の投影に規則 `hy-rebind`(subject `hy:rebind`)を足した
(code-quality `aa2cbcc`・`quality/hy_rebind.py` = `binding_forms._Walker` の束縛の数え方と `module_findings` の赤の写し)。
実 repo 6 つで `rewrite_body` と同じ本体に当てた件数を突き合わせて差 0。既存の件数は各 repo の基底へ先に登記した(doeff 100・
agora-controllers 1,590・agent-control-plane 489・argus 70・herdr-hud 73・ai-cli 104・merge-queue 118・pr-review 177・custody 21・
kubeacp 14・dotfiles 131 — code-quality の投影で数え、module 契約の在る検査対象の file だけ)。その後に dotfiles の code-quality の
pin を上げた(dotfiles `de88c0b0f`)。変更の走行では触った行に足した束縛し直しだけが赤、日次の全体検査は登記の件数で緑。

- `doeff-hy-setv`(setv の警告)は code-quality に載せない: 所見に警告の段が無く、載せれば全部が赤になる。案内は doeff-hy-check が出す。
- 登記だけを増やす便は fast の段で基底を測るようにした(code-quality `registration.grown_entry_paths`)— 測らないと増量の規則が
  「実観測 0 件を超える」と読んで断った(#411 と同じ形の穴)。
- 2 段目(旧い書き方どうしも展開の誤りにする)の条件の測り方は、各 repo の基底の `hy-rebind` の登記が 0 になったこと、で読める。

## 9. 検

- `packages/doeff-hy/tests/test_val_var_lazy.py`: val の 2 回目は誤り / var と `:=` / `!(f)` と `(! (f))` が同じ(reader の実測を含む) /
  lazy val の 4 性質(使わなければ評価しない・2 回使っても 1 回・呼び出しが変われば再評価・初回が例外なら再評価) / lazy var の使う前の `:=` /
  fn・内包表記の中の参照は誤り / 影は誤り / defk の旧い lazy は誤り / defk・deftest の session は誤り / session val・var が旧い lazy-val・lazy-var と同じ動き
  (警告も確かめる) / キーが旧い形と同じ / 外の handler が Get に答えれば初期化は走らず Put を観測できる / 節の中の val・var・lazy / deftest でも効く /
  setv の警告 / 束縛し直しの赤(for の変数と排他の枝は数えない) / module の直下の val・lazy val・誤り・setv の警告
- `packages/doeff-hy/tests/test_static_check.py::test_binding_findings_are_reported_on_their_lines`: doeff-hy-check が警告と赤を書いた行に出す
- `packages/doeff-hy/tests/test_lazy_and_threading.py`: defk の旧い lazy の 2 本を「誤りになり新しい形を案内する」検へ書き換えた

## 10. 実測(2026-09-26・各 repo の origin/main・新しい解析 `binding_forms.rewrite_body` をそのまま当てた)

| repo | defk | defk の束縛し直し | deftest | defhandler | 展開の誤りになる本体 | defk / deftest の旧い lazy | defhandler の旧い lazy 節 / set! の節 | 本体の setv の警告 | module の直下の setv の警告 | 束縛し直しの赤 |
|---|---|---|---|---|---|---|---|---|---|---|
| agora-controllers | 2,594 | 461 | 1,995 | 228 | 0 | 0 | 2 / 5 | 9,536 | 2,962 | 1,877 |
| proboscis-ema | 1,441 | 198 | 1,073 | 235 | 0 | 0 | 106 / 53 | 8,117 | 2,538 | 556 |
| doeff | 1,216 | 215 | 1,140 | 135 | 0 | 0 | 7 / 3 | 6,605 | 1,072 | 1,289 |
| agent-control-plane | 1,305 | 167 | 1,372 | 0 | 0 | 0 | 0 / 0 | 5,864 | 2,389 | 702 |
| argus | 814 | 114 | 848 | 0 | 0 | 0 | 0 / 0 | 3,499 | 725 | 483 |
| herdr-hud | 11 | 0 | 318 | 0 | 0 | 0 | 0 / 0 | 3,206 | 310 | 77 |
| 計 | 7,381 | 1,155(15.6%) | 6,746 | 598 | 0 | 0 | 115 / 61 | 36,827 | 9,996 | 4,984 |

- reader が読めなかった file(agora-controllers 37・argus 3・agent-control-plane 4・herdr-hud 4・proboscis-ema 1 — shebang の file と reader macro の file)は
  字面で `(lazy` 等を探し、コードの中の使用が 0 件であることを確かめた(文字列の中だけ)。
- 既存の `(val …)` / `(var …)` / `(:= …)` / `(session val …)` の使用は 0 件(文字列の中の 3 件だけ)— 新しい構文と衝突する名前は無い。
- 依頼の文の「defk の 13%(6,597 個中 856 個)」との違い: 今回の数え方は拡張代入を含み、互いに排他な枝を数えない(§5)。
