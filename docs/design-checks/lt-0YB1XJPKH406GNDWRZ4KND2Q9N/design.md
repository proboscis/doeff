# 設計検証 lt-0YB1XJPKH406GNDWRZ4KND2Q9N — 関数の中に書いた定義の macro の補助の import と、module に 1 度だけ知らせる文

## この文書の目的

読み手が、次の 4 つを読み終えて言えるようにする。

- 関数の本体の中に書いた定義の macro(defeffect・deff・deftest・defsystem ほか)の展開について、どの判断をどこが持つか。
- 盲検 A・B が見つけた 2 つの失敗例が何で、どう直したか。
- 将来の変更 5 つが、どの責務の中で閉じるか。
- それを機械で守る検がどこに在り、どの範囲を見ないか。

盲検の前の主張は design-before-blind.md に固定してあり、この文書はそれを書き換えずに、盲検の後の結論を書く。

## 1. 完了の範囲

実装完了まで。直しはすべて実施し、検の正常例と失敗例を実行した。

| 版 | 中身 | 所在 |
|---|---|---|
| da660e83e | 基準の版(直す前) | main |
| 0e366de99(L2159・branch の commit bf32c2041) | defeffect・deff を `_scoped-definition` に通す。関数の中の deftest は pytest の item の記録を出さない | main |
| b3430852b | defsystem を `_scoped-definition` に通す。doeff_hy.macros の定義の macro を検体で数え、補助の名の漏れを名に依らず検める | branch wt/ki-93395c-design-check(この記録と一緒に main へ入れる) |
| 063a76057 | 盲検の後の直し: `_scoped-definition` の module-forms、deftest は記録を module-forms に渡す、検体ごとの状態の検、型検査のための展開の記録の検 | 同 |

範囲の外(依頼の外・この記録で直していない):

- record.hy の defrecord・defwire。関数の中で `doeff_hy`(defwire は `_doeff_ClassVar` も)を外の関数の局所の名にする。依頼元が別の card にした(検収 lt-DPW067YV30NSGP05NG2FBBGN5J)。
- conductor.hy の defworkflow。`_conductor_defworkflow` を外の関数の局所の名にする。doeff_hy.macros が出さない別の DSL の macro。

## 2. 固定した要件

| id | 要件 | 満たした証拠 |
|---|---|---|
| R1 | 関数の本体の中に書いた defhandler の前に for/do の読みが在っても落ちない事を確かめる検が main で緑(直す前は赤) | 検 `defhandler-in-defn-after-for-do` が main 0e366de99 で緑・2bf226a96 の親 0c8c3abaa で 3 failed(card の受入 c23b275bc の証拠) |
| R2 | defk の本体の走らない枝に書いた defeffect の後に、外の defk の最後の値の guard が落ちない事を確かめる検が main で緑(直す前は赤) | 検 `defeffect-in-untaken-branch` が main 0e366de99 で緑・基準の版で `UnboundLocalError: _doeff_effect_base`(受入 c519e0f95 の証拠・commit bf32c2041 の本文に 10 failed / 40 passed → 50 passed) |
| R3 | 関数の中の定義の macro の補助の import が外の関数の読みを壊さない | evidence/runs/related-tests-after-fix.txt(180 passed)・検体ごとの名に依らない局所の検 |
| R4 | 関数の中の deftest は外の関数を呼ぶたびに記録を積まない | 同・evidence/runs/S2-positive.txt(記録の件数 [0, 0, 0, 0]) |
| R5 | module と class の本体の展開・型検査のための展開を変えない | evidence/expansion/six-expansions.txt(module・class の実行時と型検査の 4 つが基準の版と 1 字も同じ)。関数の中の型検査のための展開は、deftest の記録の文だけが消える(§4 の修正 2 — 実行時の展開に合わせた意図した変更) |

## 3. 責務(盲検の後)

| id | 責務 | 持つ知識・判断 | 隠す知識 | 公開の形 | effect | 寿命 | 守る事 |
|---|---|---|---|---|---|---|---|
| M1 | `_function-scope?`・`_in-function-body?`(macros.hy) | 展開した場所が関数の本体の中か(`_function-scope?` は型検査の展開でも同じ答え。`_in-function-body?` は型検査の展開では偽) | hy.scoping の読み方 | 2 つの述語 | なし(compiler の scope と static view の旗を読むだけ) | なし(展開ごとに呼ぶ) | module・class の本体で偽 |
| M2 | `_scoped-definition`(macros.hy) | 定義の文の並びの組み方と、module の読み込みで 1 度だけ走る文(module-forms)をどこで出すか | 包む形・module-forms を出す場所の判断を定義の macro から隠す | `(_scoped-definition compiler name forms [module-forms])` | なし(form を作るだけ) | なし | 関数の中では `name` と Hy の一意の名だけを外の関数に束ね、module-forms を出さない(実行時・型検査とも)。module・class では `(do ~@forms ~@module-forms)` |
| M3 | 定義の macro(defk・deff・defp・defpp・deftest・defeffect・defsystem・defmcp-tool・defhandler、defp を並べる defpipeline) | 何を定義し、どの補助を import し、どの属性を置くか | 補助の出所 | macro の書き方 | なし(展開の時の純関数) | なし | 定義の文を forms に、module に 1 度だけ知らせる文を module-forms に渡す。forms はどの module の状態も変えない |
| M4 | 展開の本体を作る関数(defeffect-form・declaration-setters・defsystem-form・`_build-fn-with-contracts`) | effect 型の class・宣言の属性・系の記述の作り方 | dataclass・seal・記述の形 | form を返す関数 | なし | なし | 返す form は定義して名に束ねるだけ(どの module の状態も変えない) |
| M5 | pytest の item の記録の書き手と読み口(pytest_items.py の record-function・record-at-import・module_record_texts) | 記録の形(JSON)と置き場(`ITEMS_ATTR`) | 記録の置き場と符号化 | record-function(記録の式を返す)・module_record_texts(読み口) | record-at-import が module の globals へ追記する | module の読み込みの時に 1 度(module-forms として) | 記録の置き場を知るのはこの module だけ |
| M6 | doeff-adr の収集(lazy_collection.py) | 記録と実物の module の突き合わせ | — | plugin の内部 | 合わない時に module を import する | 収集ごと | M5 の読み口から読む(置き場の綴りを知らない) |
| M7 | 契約の検(tests/test_nested_definition_scope.py・tests/test_handle_scope.py) | 関数の中の定義が外の関数に作ってよい名、どの module の状態も変えない事、module・class の展開を包まない事 | — | pytest の検 | なし | 検の実行ごと | 定義の macro は検体で数え、名と状態の両方を検体ごとに検める。記録は M5 の読み口で読む |

盲検の前からの変更点: M1 に `_function-scope?` を足した。M2 が module-forms を持つ(判断を deftest から移した)。M5 の読み口を M7 が使う。

## 4. 盲検の結果・再現・修正

起動の記録は blind/blind-meta.md。返答は未加工で blind/blind-a-return.md・blind/blind-b-return.md に在る。

### 盲検 A — 記録を作る定義の macro を足すと、記録の判断を写す事になる

- **失敗例**: S2 のとおり、記録を作る定義の macro `defbench` を doeff_hy.macros に足し、文の並びを `_scoped-definition` に渡す。外の関数を呼ぶたびに `__doeff_pytest_items__` に記録が積まれ、契約の検は 98 passed のまま通った。
- **設計者の再現**: evidence/a/A-repro-before.txt(版 5ddb4e482 に A の差分)。98 passed、記録の件数 [0, 1, 2, 3]、`verify_records` が「記録の test_bench_inner が module に無い」と返した。**成立**。
- **分類**: 隠すはずの知識の漏洩。「この文は module の読み込みで 1 度だけ走るか」の判断が deftest の本体(M3)にだけ在った。そのため、記録を作る新しい macro は M1 に問う判断を写すしかなかった。検の欠けもあった: module の globals の検の入力は、手で並べた `_CASES` だけだった。
- **型検査の展開の所見**: 関数の中の deftest は、型検査のための展開では記録の文と `import doeff_hy.pytest_items` を外の関数に出していた。実行時は 0e366de99 から出さないので、投影が実行時とずれていた。A の a-files/probe_static_view.out.txt と evidence/expansion/six-expansions.txt の function-static の差で見た。**成立**。

### 盲検 B — defeffect-form が別の module の索引へ登録すると、呼ぶたびに積み増す

- **失敗例**: defeffect-form が返す form に `(doeff_hy.declarations.register-effect ~name)` を足し、`doeff_hy.declarations` の `EFFECT-INDEX` へ登録させる。関数の中の defeffect は外の関数を呼ぶたびに索引を積み増した。契約の検は 97 passed のまま通った。
- **設計者の再現**: evidence/b/B-repro-before.txt。97 passed(1 回目の撃ちは機体の検の枠の待ちで 300 秒の timeout になり、撃ち直した)、索引は 0 → 2、1000 回の呼びで 1000 個の型が残った。**成立**。
- **分類**: 共同の不変条件(forms はどの module の状態も変えない — S4 の主張)を見る検の欠け。検は定義を書いた module の globals しか見ていなかった。判断の所在としては、A と同じく「1 度だけ走る文」を M4 が置ける形だった。

### 修正(063a76057)

| # | 修正 | 対応する失敗例 |
|---|---|---|
| 1 | `_scoped-definition` に module-forms を足し、どこで 1 度だけ走るかの判断を M2 だけが持つ。deftest は記録を module-forms に渡し、M1 に問わない | A |
| 2 | module-forms は関数の中では型検査のための展開でも出さない(`_function-scope?`)。関数の中の型検査のための展開から、deftest の記録の文と、その import が作る `doeff_hy` の局所の名が消える | A の型検査の所見 |
| 3 | 検 `test_definition_sample_leaves_module_state_unchanged`: 検体ごとに、初めの呼びの後で外の関数を 2 回呼び、定義を書いた module と読み込み済みの doeff の module の globals の名・値の id・入れ物の要素の id が変わらない | A(検の入力を検体にする)・B(定義を書いた module の外も見る) |
| 4 | 検 `test_static_view_records_only_module_level_deftest`: 型検査のための展開で、関数の中の deftest は記録の文を出さず、module の段の deftest は出す | A の型検査の所見 |
| 5 | module の段の deftest の記録を、`__doeff_pytest_items__` の綴りではなく M5 の読み口 `module_record_texts` で読む | S5 の検を組む時に設計者が見つけた(M7 が M5 の置き場を知っていた) |

修正後の再検証: A の形(記録を forms に置いた defbench)は検 3 が赤(evidence/runs/S2-negative-record.txt)。B の差分も検 3 が `('doeff_hy.declarations', 'EFFECT_INDEX')` を名指して赤(evidence/runs/S4-negative.txt)。正しく書いた defbench(記録を module-forms に渡す)は全部緑で、記録の件数は [0, 0, 0, 0](evidence/runs/S2-positive.txt)。

## 5. シナリオごとの予測と実測

| id | 軸 | 予測(盲検の前) | 実測 | 差の原因 |
|---|---|---|---|---|
| S1 | effects | 変わる: M4・M3。変わらない: M1・M2・M5・M6・M7 | defeffect-form に新しい補助の import を足す変異は declarations.hy(M4)の 1 行だけで、検は 75 passed。鍵の受け取り(M3)は変異に要らなかった。補助を `_scoped-definition` の外に置く変異は、名に依らない検だけが `_doeff_new_helper` を名指して赤にし、名の表の検は 10 件とも通った | M3 の変更は新しい鍵を受ける時だけ要る(予測は鍵の追加まで含めた) |
| S2 | effects | 変わる: M3・M7(検体 1 行)。変わらない: M1・M2・M4・M5・M6 | 盲検 A: 直す前は、記録を作る macro が M1 に問う判断を写すしかなかった(漏洩)。直した後は、defbench を足す変更は macros.hy(M3)と検の file(M7 — 検体と require の 2 か所)だけで、77 passed | 漏洩を修正 1 で M2 に寄せた。M7 の require は配線 |
| S3 | simulation | 変わる: M4・static_view。変わらない: M1・M2・M5・M6・M7 | 型検査の答えの形を変える変異は declarations.hy(M4)だけで 75 passed。盲検 A: 関数の中の型検査のための展開に記録の文が残っていた(実行時とずれ)。直した後は module・class の展開は基準の版と 1 字も同じで、関数の中の型検査の展開は記録の文だけが消える | 修正 2(M2 の判断を型検査の展開にも効かせた) |
| S4 | concurrency | 変わらない: M1〜M7 | 盲検 B: 直す前は、別 module の索引への登録を検が見なかった。直した後は 10 の検体すべてで、検 3 が緑。8 thread × 5 回の並べた呼びでも状態は変わらない。B の差分は検 3 が赤 | 修正 3(検が見る範囲を doeff の module へ広げた) |
| S5 | storage | 変わる: M5・M6。変わらない: M1・M2・M3・M4・M7 | 記録の置き場の名を替える変異は pytest_items.py(M5)だけで、nested の検 75 passed・doeff-adr と pytest_items の記録の検 7 passed。M6 は M5 の読み口から読むので変わらない。置き場の替えに A の形を重ねても、検 3 が新しい置き場の名 `('nested_definition_scope', '__doeff_pytest_records__')` で赤 | M6 は予測より狭く済んだ(読み口を通す)。M7 は修正 5 の前なら置き場の綴りを知っていて変わる必要があった |
| S6 | hardware | 対象外 | — | 展開は compile の時の純粋な form の組み立てで、機体の種類・資源に依らない |
| S7 | distribution | 対象外 | — | 展開は file ごとに compile の時に決まり、機体の数・配置に依らない。module・class の展開は変えていない(R5)ので、bytecode の焼きが運ぶ物も変わらない |

## 6. 強制方法(盲検の後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M3 が M2 を通る(関数の中で補助の名を外の関数に束ねない) | 定義 1 つだけを書いた関数の局所が、定義した名と `_hy_` の頭の名の外に無い(名の表に依らない) | tests/test_nested_definition_scope.py `test_function_with_only_a_definition_binds_only_its_names` | pytest の testpaths の tests — 4 時間ごとの全体の回と、手の名指し | 検体の形 1 つで見る。macro の鍵ごとに出す import が変わる形は、検体がその鍵を書いた時だけ見る(defeffect の検体は :pre・:absent を書く) |
| 定義の macro を足したら検体も足す | doeff_hy.macros の `_hy_macros` の def の付く名の集まりが、検体の鍵の集まりと等しい | 同 `test_every_definition_macro_has_a_nested_sample` | 同上 | doeff_hy.macros が出さない macro(record.hy・conductor.hy・doeff-adr の defadr・defsemgrep)は数えない |
| forms がどの module の状態も変えない・module-forms は関数の中で出ない(S4・R4) | 検体ごとに、初めの呼びの後で 2 回呼び、定義を書いた module と読み込み済みの doeff の module の globals の名・値の id・入れ物の要素の id を比べる | 同 `test_definition_sample_leaves_module_state_unchanged` | 同上 | doeff の外の module(名の頭が doeff でない)と、入れ物の中の入れ物(2 段目)の書き換えは見ない。初めの呼びで 1 度だけ起きる登録は見ない |
| 1 度だけ走る文の判断を M2 だけが持つ | 構造: deftest は module-forms に渡すだけで M1 を読まない。M1 の読み手は docstring に挙げた 2 か所(`_scoped-definition`・handle) | macros.hy | 検ではなく構造。上の状態の検が、判断を写さずに forms へ置いた形を赤にする | 新しい macro が M1 を直に読んで判断を写す事は止めない(写しても状態の検は通る — 正しく写した時) |
| 型検査のための展開で、関数の中の記録の文を出さない | 型検査のための展開の Python の source に、関数の中の deftest の `record_at_import` が無く、module の段の deftest には在る | 同 `test_static_view_records_only_module_level_deftest` | 同上 | deftest の記録だけを見る。他の定義の macro の型検査の展開が関数の中で出す import(defeffect の `_doeff_dataclass` など)は、R5 に従い基準の版のまま変えていない |
| module・class の展開を包まない(R5) | module と class の定義の `__qualname__`・引数なしの `super()`・module の段の deftest の記録(M5 の読み口で読む) | 同 `test_module_and_class_level_definitions_are_not_wrapped`・tests/test_handle_scope.py | 同上 | 型検査のための展開の 1 字の不変は検ではなく、展開の比較(evidence/expansion)で見た |
| 関数の中の定義の後で、外の関数の先の読みが未束縛にならない | traverse / for/do・文の guard・最後の値の guard・module の段の `doeff_hy` を先に読む形を走らせる | 同 `test_nested_definition_runs`・`test_outer_function_binds_no_helper_name`、tests/test_handle_scope.py | 同上 | 形は列挙した物だけ(名に依らない局所の検が補う) |

型・interface で表さない理由: 定義の macro は Hy の form を返す展開の時の関数で、form の中の名の束ね方と、文が何度走るかは型で表せない。compile した code object の局所の名と、呼んだ後の module の状態で検める。

## 7. 検証の記録

各実験は evidence/scripts/mutants.sh.txt が変異の差分(evidence/mutants/)を当てて名指した検だけを撃ち、外す。出力は evidence/runs/。
変異は全部、版 063a76057 の上で当てて外した(外した後の `git status` は空 — 各 run の末尾)。
検を撃った 3 つの作業木(設計者の `doeff-wt-ki-93395c-dc`・盲検の `doeff-blind-a-lt0YB1`・`doeff-blind-b-lt0YB1`)の `.venv` は、
それぞれ自分の木の `packages/doeff-hy/src`・`packages/doeff-adr/src` を読む事を `doeff_hy.__path__` で確かめた。

| check | シナリオ | 種類 | 期待 | 観測 | 証拠 |
|---|---|---|---|---|---|
| C1 | S1 | 正常 | defeffect-form に補助の import を足しても、変わるのは M4 だけで検は全部緑 | declarations.hy 1 行・75 passed | runs/S1-positive.txt |
| C2 | S1 | 失敗 | 補助を `_scoped-definition` の外に置くと、名に依らない局所の検が新しい名を名指して赤 | `{'Inner', '_doeff_new_helper'}` で 1 failed・名の表の検 10 passed | runs/S1-negative.txt |
| C3 | S2 | 正常 | 記録を module-forms に渡す defbench を足すと、M3 と M7 だけが変わり全部緑・記録は積まれない | 77 passed・記録の件数 [0, 0, 0, 0]・problems [] | runs/S2-positive.txt |
| C4 | S2 | 失敗 | 記録を forms に置く defbench(盲検 A の形)を、状態の検が赤にする | `('nested_definition_scope', '__doeff_pytest_items__')` で 1 failed・記録 [0, 1, 2, 3] | runs/S2-negative-record.txt |
| C5 | S2 | 失敗 | 検体の無い定義の macro を、検体の数えが赤にする | `'defbench'` の余りで 1 failed | runs/S2-negative-sample.txt |
| C6 | S3 | 正常 | module・class の実行時と型検査の展開が基準の版と 1 字も同じ。関数の中の型検査の展開は記録の文だけが消える | 4 つが同じ・function-static の差は記録の 2 組 | expansion/six-expansions.txt |
| C7 | S3 | 正常 | 型検査の答えの形を変える変異が M4 だけで閉じ、検は全部緑 | declarations.hy のみ・75 passed | runs/S3-positive-change.txt |
| C8 | S3 | 正常 | 型検査のための展開の記録の検と、module・class を包まない検が緑 | 2 passed | runs/S3-positive.txt |
| C9 | S3 | 失敗 | 関数の中で型検査の展開に module-forms を出す変異を、型検査の記録の検が赤にする | `record_at_import` が関数の中に在り 1 failed | runs/S3-negative.txt |
| C10 | S4 | 正常 | 検体ごとの状態の検が全部緑 | 10 passed | runs/S4-positive.txt |
| C11 | S4 | 正常 | 8 thread × 5 回の並べた呼びの後も、検体ごとの状態が変わらない | 変わった検体 0 | runs/S4-positive-threads.txt |
| C12 | S4 | 失敗 | 盲検 B の索引への登録を、状態の検が赤にする | `('doeff_hy.declarations', 'EFFECT_INDEX')` で 1 failed | runs/S4-negative.txt |
| C13 | S5 | 正常 | 記録の置き場の名を替えても M5 だけが変わり、nested の検は全部緑 | pytest_items.py のみ・75 passed | runs/S5-positive.txt |
| C14 | S5 | 正常 | 置き場の名を替えても doeff-adr(M6)と M5 の記録の検が緑 | 7 passed | runs/S5-positive-adr.txt |
| C15 | S5 | 失敗 | 置き場を替えた上で記録を forms に置くと、状態の検が新しい置き場の名で赤にする | `('nested_definition_scope', '__doeff_pytest_records__')` で 1 failed | runs/S5-negative.txt |
| C16 | S2・S4 | 正常 | 直した版で変えた所に関わる検が緑 | 180 passed | runs/related-tests-after-fix.txt |

盲検 A・B の失敗例が直す前の版(5ddb4e482)で成り立った記録は evidence/a/A-repro-before.txt・evidence/b/B-repro-before.txt に
在る(design-check.json の blind_reproduction)。これは検が通った事が欠陥の観測で、拒否の観測ではないので checks に数えない。
直した後の拒否は C4・C12。

## 8. 残る限界と未確認

- 状態の検が見るのは、定義を書いた module と名の頭が doeff の module だけ。doeff の外の module への登録、入れ物の 2 段目の書き換え、初めの呼びで 1 度だけ起きる登録は見ない。
- 新しい定義の macro が M1 を直に読んで判断を写す事は、構造でも検でも止めていない。正しく写せば、どの検も通る(結果は正しい)。
- 型検査のための展開で、関数の中の定義の macro が出す import(defeffect の `_doeff_dataclass` など)は、R5 に従い変えていない。外の関数が先にその名を読む形で、pyright がどう読むかは確かめていない(未確認)。
- class の本体の deftest は module の段の関数として記録される(盲検 A の補足・基準の版と同じ)。直していない。
- doeff-adr の defadr・defsemgrep は record-function を直に呼ぶ(盲検 A のコードの読み)。module の段に書く宣言で、doeff_hy.macros が出さないので、検体の数えの外。
- 全数の検は撃っていない(4 時間ごとの全体の回で測る)。撃ったのは evidence/runs の名指した file だけ。
