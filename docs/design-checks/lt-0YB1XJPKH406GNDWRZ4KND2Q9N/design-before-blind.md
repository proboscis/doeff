# 設計検証 lt-0YB1XJPKH406GNDWRZ4KND2Q9N — 盲検の前の設計と主張

この文書の目的: 盲検 A・B を起こす前に、直した設計の責務・公開契約・将来の変更についての主張・強制方法を固定する。
盲検の後の結論は design.md に書き、この文書は書き換えない。

## 1. 完了の範囲

実装完了まで。

| 項目 | 状態 | 版 |
|---|---|---|
| defeffect・deff を `_scoped-definition` に通す | 実施済み・main に在る | L2159・0e366de99(branch の commit bf32c2041) |
| 関数の本体の中の deftest は pytest の item の記録を出さない | 実施済み・main に在る | 同上 |
| defsystem を `_scoped-definition` に通す | 実施済み・branch に在る(この記録と一緒に main へ入れる) | b3430852b |
| doeff_hy.macros が出す定義の macro を検体で数え、補助の名の漏れを名に依らず検める検 | 実施済み・branch に在る | b3430852b |
| record.hy の defrecord・defwire | 範囲の外(依頼元が別の card にした・報告 lt-D8WWWZRZADQTDPSTV0Q5DYV9KB の検収 lt-DPW067YV30NSGP05NG2FBBGN5J) | — |
| conductor.hy の defworkflow・defphase | 範囲の外(doeff_hy.macros が出さない別の DSL) | — |

## 2. 固定した要件

| id | 要件 | 出所 |
|---|---|---|
| R1 | 関数の本体の中に書いた defhandler の前に for/do の読みが在っても落ちない事を確かめる検が doeff の main で緑(直す前は赤) | card ki-93395c51de1e の受入 c23b275bc |
| R2 | defk の本体の走らない枝に書いた defeffect の後に、外の defk の最後の値の guard が落ちない事を確かめる検が doeff の main で緑(直す前は赤) | 同 c519e0f95 |
| R3 | 関数の中に書いた定義の macro の展開の頭の補助の import が、外の関数の読みを壊さない(defhandler・defeffect・deff) | 依頼 lt-0YB1XJPKH406GNDWRZ4KND2Q9N の文 |
| R4 | 関数の中の deftest は、外の関数を呼ぶたびに module の `__doeff_pytest_items__` に記録を積まない | 依頼の文の「関わる所見」・`_scoped-definition` の docstring の呼び手の負う事 |
| R5 | module と class の本体の展開・型検査のための展開を変えない(`__qualname__`・引数なしの `super()`・pyright の宣言の数) | `_scoped-definition` の docstring・前の直し(c30d79cc5・54b891fe6・2bf226a96)の決め |

## 3. 対象の版

- 基準の版(直す前): da660e83ed7b8c50a5a4c201e14d501b750bd8c3(L2159 の前の main)
- 実装の版: 0e366de997b251b85145c7489cf46a108fc45f36(L2159)と b3430852b(defsystem と検体の数え)。盲検には b3430852b の木を読ませる。

## 4. 責務

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 | effect | 寿命 | 守る事 |
|---|---|---|---|---|---|---|---|
| M1 | `_in-function-body?`(macros.hy) | 展開の時の Hy の compiler の scope のうち、let と内包を飛ばした最も近い Python の scope が関数かどうか。型検査のための展開では偽 | hy.scoping の読み方・型検査の投影かどうかの読み方 | `(_in-function-body? compiler)` → 真偽 | なし(compiler の scope と static view の旗を読むだけ) | なし(展開ごとに呼ぶ) | module・class の本体と型検査のための展開で偽 |
| M2 | `_scoped-definition`(macros.hy) | 定義の文の並びを書かれた所に合わせて組む形: module・class では並べ、関数の中ではすぐ呼ぶ引数なしの関数に入れて名だけを束ねる | 包む形(すぐ呼ぶ関数・`setv`)を定義の macro から隠す | `(_scoped-definition compiler name forms)` → form | なし(form を作るだけ) | なし | 関数の中で外の関数に束ねる名は `name` と Hy の一意の名だけ。module・class の展開は `(do ~@forms)` |
| M3 | 定義の macro(defk・deff・defp・defpp・deftest・defeffect・defsystem・defmcp-tool・handle.hy の defhandler、defp を並べる defpipeline) | 何を定義するか、どの補助を import するか、どの属性を置くか | 補助の出所 | macro の書き方(書き手の source) | なし(展開の時の純関数) | なし | 文の並びを M2 に渡す。`forms` は定義して名に束ねるだけで、module の globals に記録を積まない |
| M4 | 展開の本体を作る関数(declarations.hy の defeffect-form・declaration-setters、system_form.hy の defsystem-form、macros.hy の `_build-fn-with-contracts`) | effect 型の class・宣言の属性・系の記述の作り方と、それに要る import | dataclass・seal・記述の形 | form を返す関数 | なし | なし | 返す form は M2 に渡せる(定義して名に束ねるだけ) |
| M5 | pytest の item の記録を書く側(pytest_items.py の record-function・record-at-import)と、deftest が記録を出すかの決め | 記録の形(JSON)と、記録を module の globals に積む文 | 記録の符号化 | 記録の式(form) | 実行時に module の `__doeff_pytest_items__` へ追記する | module の読み込みの時に 1 度(module の段) | 関数の中の deftest は記録を出さない。module の段の deftest は今までどおり記録する |
| M6 | doeff-adr の収集(lazy_collection.py の verify_records・check_no_unrecorded_items) | 記録と実物の module の突き合わせ・合わなければ import して集める | — | plugin の内部 | module を import する(合わない時) | 収集ごと | 変えない |
| M7 | 契約の検(tests/test_nested_definition_scope.py・tests/test_handle_scope.py) | 関数の中の定義が外の関数に作ってよい名、module の globals を変えない事、module・class の展開を包まない事 | — | pytest の検 | なし | 検の実行ごと | 定義の macro を足すと検体が要る。漏れは名に依らず捕まえる |

## 5. 変更シナリオと主張(盲検の前に固定)

| id | 軸 | 適用 | 変更 | 主張 | 変わる(予想) | 変わらない(予想) | 前提 |
|---|---|---|---|---|---|---|---|
| S1 | effects | 適用 | defeffect に新しい宣言の鍵を足し、展開が新しい補助を import する(例 `:retries` の鍵が `(import doeff_hy.retry_runtime [policy :as _doeff-retry-policy])` を足す) | 新しい import を defeffect-form が返す form の中に書けば、関数の中でも外の関数の局所の名にならない(M2 が包む)。M7 の名に依らない検が、補助の名の表に無い新しい名の漏れも捕まえる | M4(defeffect-form)・M3(defeffect の鍵の受け取り) | M1・M2・M5・M6・M7 | 新しい import を defeffect-form の返す form の中に置く |
| S2 | effects | 適用 | doeff_hy.macros に新しい定義の macro を足す(例 `defquery` — 名を定義し、頭で補助を import する) | 書き手は文の並びを M2 に渡すだけで、M1・M2 は変わらない。M2 を通さない macro は、M7 の検体の数え(検体の無い macro は赤)と名に依らない局所の検(検体を足せば漏れが赤)が止める | M3(新しい macro)・M7(検体 1 行) | M1・M2・M4・M5・M6 | 新しい macro の名が def で始まり、doeff_hy.macros が出す(そこで定義するか require して出し直す) |
| S3 | simulation | 適用 | 型検査のための展開(doeff-hy-check の投影 — 走らせずに pyright が読む)で、定義の macro の形を変える(例 defeffect の `__doeff_answer__` の形) | 型検査のための展開では M1 が偽なので M2 は包まず、関数の中でも module・class でも展開は今までどおり。変更は M4 と static_view の側で閉じる | M4・static_view | M1・M2・M5・M6・M7 | 投影は static_view の旗の下で展開する |
| S4 | concurrency | 適用 | 関数の中に定義を書いた外の関数を、何度も・並べて呼ぶ(検を作る関数・呼ぶごとに effect 型を作る関数) | 包んだ文の並びは呼ぶたびに評価されるが、module の globals を変えない(deftest の記録も出さない)。呼びごとの状態はすぐ呼ぶ関数の局所に閉じ、呼びの間で分け持つ物が無い | なし(今の形のまま) | M1〜M7 | 書き手の本体が自分で globals を変えない |
| S5 | storage | 適用 | pytest の item の記録の置き場を替える(module の globals の `__doeff_pytest_items__` から、例えば別の登録簿へ) | 替わるのは M5 の書く側と M6 の読む側だけ。「関数の中の deftest は記録しない」の決めは deftest に残り、M1・M2 は変わらない | M5・M6 | M1・M2・M3(deftest の決め以外)・M4・M7 | 記録は module の段の関数だけを表す |
| S6 | hardware | 対象外 | — | — | — | — | 展開は compile の時の純粋な form の組み立てで、機体の種類・資源に依らない |
| S7 | distribution | 対象外 | — | — | — | — | 展開は file ごとに compile の時に決まり、機体の数や配置に依らない。doeff-cluster の bytecode の焼き(DOEFF_HY_CODE_STORE)は展開の結果を運ぶだけで、module・class の展開は変えていない(R5) |

## 6. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M3 が M2 を通る(関数の中で補助の名を外の関数に束ねない) | 契約の検: 定義 1 つだけを書いた関数の局所(`co_varnames`・`co_cellvars`)が、定義した名と `_hy_` の頭の名の外に無い。補助の名の表に依らない | tests/test_nested_definition_scope.py の `test_function_with_only_a_definition_binds_only_its_names`(検体は `_DEFINITION_SAMPLES`) | pytest(testpaths の tests)— 日次の全体の回・手の名指し | 検体の形 1 つで見る。macro の鍵ごとに出す import が変わる形(例 defeffect の `:absent` が足す import)は、検体がその鍵を書いた時だけ見る |
| 定義の macro を足したら検体も足す | 契約の検: doeff_hy.macros の `_hy_macros` の def の付く名の集まりと `_DEFINITION_SAMPLES` の鍵の集まりが等しい | 同 `test_every_definition_macro_has_a_nested_sample` | 同上 | doeff_hy.macros が出さない macro(record.hy・conductor.hy・doeff-adr の defadr など)は数えない |
| 関数の中で外の関数が先に読む補助が未束縛にならない | 契約の検: 外の関数が先に traverse / for/do・文の guard・最後の値の guard・module の段の `doeff_hy` を読む形を走らせる | 同 `test_nested_definition_runs`・`test_outer_function_binds_no_helper_name`、tests/test_handle_scope.py | 同上 | 形は列挙した物だけ |
| 関数の中の定義が module の globals を変えない(R4) | 契約の検: 外の関数を 2 回呼んで module の globals の名と入れ物の要素の数が変わらない(deftest を含む) | 同 `test_nested_definition_leaves_module_globals_unchanged` | 同上 | 書き手の本体が自分で globals を変える物は見ない |
| module・class の展開を包まない(R5) | 契約の検: module と class の定義の `__qualname__`・引数なしの `super()`・module の段の deftest の記録 | 同 `test_module_and_class_level_definitions_are_not_wrapped`・test_handle_scope.py | 同上 | 型検査のための展開の 1 字の不変は検でなく、直した時の展開の比較(証拠)で見た |
| 型検査のための展開を包まない(R5) | M1 が static view で偽を返す(型ではなく関数の 1 行) | macros.hy `_in-function-body?` | 型検査の投影(doeff-hy-check)の展開 | 検は無い。直した時の展開の比較で見た |

型・interface で表さない理由: 定義の macro は Hy の form を返す展開の時の関数で、form の中の名の束ね方は型で表せない。Python の compile の後の code object の局所の名で検める。
