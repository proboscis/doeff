# 設計検証: 関数の中の handle・defhandler が外の関数に局所の名を作らない(依頼 lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W)

## この文書の目的

読み手(依頼主・後で handle.hy / macros.hy を変える実装者)が、次の 3 つを判断できるようにする。

- doeff main 2bf226a96 の直しが、どの責務をどこに置いたか
- 将来の変更がどこまでに収まると主張しているか
- その主張を何が機械で守り、何が守れていないか

## 0. 完了の範囲と記録の順

- 範囲: **実装完了まで**。直しは doeff main 2bf226a96ed1a2abb3e66dbaf1c5119a4e61098c(L2141・branch wt/handle-after-traverse-scope-r3)に入っている。
  基準の版(直す前)は a2254d66cfc52f9fa9c3b4829bff2d76191bccb3。
- 設計検証は実装の後に行った。依頼の文と配達の文面に設計検証の節が無く、先に実装して着地させた。終わりの検査
  (Stop の hook)が class=investigate の依頼に設計検証を求めたので、着地の後に行う。
- この本文の §1〜§5(要件・責務・事前の主張・強制方法)は、盲検 A・B を起動する**前**に書いて保存した
  (sha256 は evidence/design-pre-blind.sha256)。盲検の後に足す内容は §6 以降にだけ書き、§1〜§5 は書き換えない。

## 1. 固定した要件

- R1: 関数の本体(defk の本体・素の defn・deftest)の中で、handle・defhandler を traverse / for/do の後に書いても、
  走らない枝に書いた後で traverse / for/do を書いても、UnboundLocalError で落ちない
  (card ki-907f779b3290 の受入 cf1adadbd「defk の本体で traverse の後に書いた handle が落ちない事を確かめる検が doeff の main で緑
  (直す前は赤)」)。
- R2: handle・defhandler の展開は、それを含む関数に局所の名を作らない。例外は defhandler が定義する名と、Hy が作る一意の名
  (`_hy_` の頭)だけ。
- R3: handle の第 1 引数(本文)は書いた場所で、外の do の文脈で評価する(本文の `!` の yield は外の生成器の物)。
  節の終わり方の検めと handler の組み立ては、前と同じく本文の式より前に走る。
- R4: module の段・class の本体の handle・defhandler の展開と、型検査のための展開(doeff-hy-check)は変えない。

利用の状況: handle は defk の本体の中で `(<- r (handle (prog …) (Eff [x] (resume …))))` と書く。traverse / for/do の答えを使う
handler はこの位置に来る。defhandler は handler を作る関数(`(defn make [cfg] (defhandler h [k] …) …)`)の中にも書く。

## 2. 責務(module)と公開契約

| id | 実体(2bf226a96 の path と行) | 責務 | 持つ知識 | 隠す知識 |
| --- | --- | --- | --- | --- |
| scope-judgment | packages/doeff-hy/src/doeff_hy/macros.hy L676 `_in-function-body?`(新) | 展開の場所が「import すると外の関数の局所の名を作る所」かの判断(実行時の展開で、let と内包を飛ばした最も近い Python の scope が関数か) | Hy の compiler の scope の読み方(`hy.scoping` の nearest-python-scope・is-function-scope)、型検査のための展開では偽 | Hy の scope の内部の API を各 macro から |
| scoped-definition | macros.hy L653 `_scoped-definition` | 名を定義する macro(defk・defp・deftest・defmcp-tool・defhandler)の文の並びを、関数の中では `(setv name ((fn [] … name)))` に入れ、名だけを外へ束ねる | 包む形 | 包むかどうかを呼び手(定義の macro)から |
| handle-expansion | packages/doeff-hy/src/doeff_hy/handle.hy L739 `defmacro handle` | 本文に inline の handler を被せる Program の式。関数の中では import・検め・組み立てを `(((fn [] prelude (setv data h-expr) (fn [b] (WithHandler data b)))) body)` に入れ、本文は外で評価する | 評価の順(prelude → 組み立て → 本文の式 → WithHandler) | 補助の束ね方を本文から |
| defhandler-expansion | handle.hy L801 `defmacro defhandler` | 名つきの handler(引数なし = Program → Program の関数・引数あり = それを作る関数)の定義。文の並びを scoped-definition に渡す | handler の値の形・属性(`__doeff_body__` など) | — |
| handler-imports | handle.hy L53 `_do-import`・L65 `_clause-endings-import`・L74 `_vm-imports` | handler の展開が名で読む実行時の名(`_doeff_do`・`Resume`・`Transfer`・`Pass`・`WithHandler`・`_doeff_check_clause_endings(_once)`)の import の形。型検査のための展開では `Pass`・`WithHandler` だけ | 名と提供元の module | 提供元を展開の書き手から |
| handler-expr | handle.hy L432 `_rewrite-ops`〜L737 `_effect-parameter`(`_build-handler-expr`・`_build-clause`・`_passed-effects-source`) | 節の dispatcher の関数(`(_doeff-do (fn [effect k] (cond …)))`)の組み立て。節の本体は dispatcher の関数の中 | 節の形・resume / transfer の書き換え | — |
| clause-endings | packages/doeff-hy/src/doeff_hy/clause_endings.hy `check-clause-endings`・`check-clause-endings-once` | 節の終わり方と effect の型の再開の宣言の照合(ClauseEndingError = TypeError の子) | Resumption の規則 | — |
| traverse-expansion | macros.hy L1499 `traverse`・L1515 `for/do` | Traverse の effect の組み立て。外の scope の `_doeff_do` を名で読む | 件の関数の形 | — |
| static-view | packages/doeff-hy/src/doeff_hy/static_view.py(HANDLER_STATIC_NAMES・STATIC_HELPER_IMPORTS)と `static-view-enabled` の枝 | 型検査のための展開: 型付きの名は module の頭に 1 度だけ import し、展開は import をほぼ出さない | 型付きの名 | — |
| bytecode-guard | doeff_hy_bytecode_guard | 使った macro の digest が変わった Hy の module の古い .pyc を使わず compile し直す | 使った macro の digest | — |
| regression-test | tests/test_handle_scope.py(新) | R1〜R4 の検 | 8 つの形・import の名の表 `_HANDLE_IMPORT_NAMES` | — |

公開契約(変えていない):

- `(handle body clause…)` は Program(WithHandler の node)を返す式。
- `(defhandler name [params]? clause…)` は name に handler の関数(または handler を作る関数)を束ねる。
- 失敗の形:
  - 展開の時の SyntaxError(節の終わり方の名での検め)
  - handle を評価した時の ClauseEndingError(型での検め)
  - 走った時の fell-through の RuntimeError
- 停止・解放の所有者: すぐ呼ぶ関数の frame は handle を評価する間だけ在る。その閉包の cell は、handler の dispatcher と
  被せる関数が生きる間だけ持たれる。新しい資源・effect は持たない。

## 3. 事前の主張(変更シナリオ)

### S1(effects・適用): 関数の本体の中に handle・defhandler を任意の順・枝で書く

- 変更: 利用者が次の所に handle・defhandler を書く。
  - defk・素の defn の本体で、traverse / for/do の後
  - 走らない枝(`when`)の中。その後で traverse / for/do を書く
  - let の中・入れ子の handle の中
  - 本文に `!` を含む handle
- 主張: import の名をどの scope で束ねるかは、scope-judgment の判断と、handle-expansion の包み・scoped-definition の包みだけが
  決める。だから外の関数には名が局所として現れず、順や枝に依らず落ちない。handle の本文は包みの外に在るので、外の do の文脈で
  評価される。
- 予想の範囲:
  - 変える: scope-judgment を新設し、scoped-definition がそれを読む。handle-expansion・defhandler-expansion を変える。
    regression-test を足す。
  - 変えない: handler-imports・handler-expr・clause-endings・traverse-expansion・static-view・bytecode-guard。
- 前提:
  - 展開の中ですぐ評価される利用者の式は、本文と節の effect の型(検めの記述 `[#(型 …) …]`)だけ。effect の型は閉包で読める。
  - macro は書いた場所の compiler の scope で展開される(外の macro が先に展開しない)。
  - 利用者は handler-imports の名(`_doeff_do` など)を自分で局所に束ねない。

### S2(effects・適用): handler の展開が読む実行時の名を 1 つ足す

- 変更: 新しい節の操作のために、handler-imports(`_vm-imports`)に doeff の node の名を 1 つ足し、handler-expr の書き換え
  (`_rewrite-ops`)がその名を読む。
- 主張: 変えるのは handler-imports と handler-expr だけ。関数の中の handle では足した名も包みの中で import され、defhandler では
  scoped-definition の包みの中で import されるので、外の関数の局所にならない。scope-judgment・scoped-definition・
  handle-expansion・defhandler-expansion は変えない。
- 予想の範囲:
  - 変える: handler-imports・handler-expr。
  - 検の側: regression-test の `_HANDLE_IMPORT_NAMES` は handler-imports の名の写しなので、名ごとの検に入れるにはここにも足す。
    `test_host_function_gets_only_its_own_names` は名の表に依らずに新しい名も捉える。
- 前提: 新しい名も handler の展開の import で束ね、利用者の module の globals に頼らない。

### S3(effects・適用): 関数の外に handle・defhandler を書く・型検査のための展開

- 変更: module の段・class の本体に handle・defhandler を書く。doeff-hy-check が型検査のための展開をする。
- 主張: scope-judgment が偽を返すので、展開は直す前と 1 字も変わらない。module の段の import は今までどおり module の
  globals に置かれ、defhandler の `__qualname__` は変わらない。
- 予想の範囲: 変える module は無い。
- 前提: 型検査のための展開の切り替え(`static-view-enabled`)は scope-judgment の中で読む。

### S4(storage・適用): 直す前の macro で compile した .pyc が残っている

- 変更: 関数の中に handle を書いた利用者の Hy の module が、直す前の handle.hy で compile され、.pyc
  (PYTHONPYCACHEPREFIX・code store)に残っている。その module を、直した版の doeff-hy で読み込む。
- 主張: handle の macro の関数の code が変わったので、bytecode-guard の digest が変わる。その module は compile し直される。
  利用者の module・保存先・bytecode-guard は変えない。
- 予想の範囲: 変更なし(bytecode-guard の既存の仕組みで足りる)。
- 前提: 利用者の module は bytecode-guard を通る import(doeff_hy_bytecode_guard の入った venv)で読まれる。

### 対象外の軸

- hardware: 展開は compile の時の form の組み立てで、生成した code は機体に依らない。関数の中の handle の評価ごとに、
  関数を 2 つ作って 1 つ呼ぶ手間が増えるだけ。
- distribution: 1 つの process の中の名の束ね方の変更で、process・機体をまたぐ状態を足さない
  (Pod の間で共有する保存先の面は S4 で扱う)。
- concurrency: handle の評価ごとに新しい frame と閉包を作り、作った後に書き換えない。足した共有の可変の状態は無い。
  節の終わり方の検め(`check-clause-endings`)は状態を持たない。defhandler の「1 度だけ検める」印は handler の値に置く
  前からの形で、変えていない。
- simulation: 時刻・乱数・I/O・handler への依りを足さず、Program が出す effect の列は変わらない。決定的な模擬の環境での
  振る舞いは前と同じ。

## 4. 実装(2bf226a96)

- macros.hy:
  - `_in-function-body?` を足した(L676)。
  - `_scoped-definition` の判断をこれに替えた。
  - docstring の呼び手の一覧に defhandler を足した。
- handle.hy:
  - handle は `(_in-function-body? _hy-compiler)` が真の時だけ、次の形に展開する。
    `(((fn [] ~@prelude (setv __doeff-handler-data__ ~h-expr) (fn [__doeff-body__] (WithHandler __doeff-handler-data__ __doeff-body__)))) ~body)`
    prelude = `_do-import`・`_vm-imports`・`_clause-endings-import`・`(_doeff-check-clause-endings "handle" [specs])`。
    偽の時は前と同じ `(do ~@prelude (WithHandler ~h-expr ~body))`。
  - defhandler は文の並びを `_scoped-definition` に渡す(引数あり・なしの両方)。
- tests/test_handle_scope.py: 29 件。

## 5. 強制方法

| 守る事 | 強制方法 | 実装の場所 | 実行の経路 | 検出の範囲 |
| --- | --- | --- | --- | --- |
| R1: 関数の中の handle・defhandler が落ちない | 契約の検(実行) | tests/test_handle_scope.py `test_handle_inside_function_runs`(8 形) | root の pytest(4 時間に 1 度の全体テストの root の段。マージの列は検を走らせない) | 8 つの形。他の書き方は見ない |
| R2: 外の関数に局所の名を作らない | 契約の検(code object の co_varnames・co_cellvars を読む) | 同 `test_outer_function_binds_no_handle_import`(名の表 `_HANDLE_IMPORT_NAMES` と突き合わせる)・`test_host_function_gets_only_its_own_names`(名の表に依らず、`_hy_` の頭と定義した名の外に局所が無い) | 同上 | 名ごとの検は表に在る名だけ。表に依らない検は handle・defhandler(引数あり・なし)だけを書いた 3 つの関数 |
| R2: module の globals を変えない(scoped-definition の呼び手が負う事) | 契約の検 | 同 `test_handle_inside_function_leaves_module_globals_unchanged` | 同上 | 8 形を 2 度呼ぶ |
| R3: 本文は外の do の文脈・検めは本文より前 | 契約の検 | 同 `handle-body-bang-after-traverse`(本文の `!`)・`test_handle_checks_clause_endings_before_the_body`(検めが落ちる時に本文の式が評価されない) | 同上 | 本文の式の評価の順 |
| R4: module・class の段は包まない | 契約の検 | 同 `test_module_and_class_level_definitions_are_not_wrapped`(`__qualname__`・走る事・module の globals の `WithHandler`) | 同上 | module の段の handle と defhandler・class の本体の defhandler |
| R4: 型検査のための展開は変えない | 既存の検 | packages/doeff-hy/tests/test_static_check_defhandler.py ほか | packages の pytest(同じ全体テスト) | 既存の検が見る形 |
| scope の判断の 1 点 | 規則なし(docstring に読み手を書いた) | macros.hy `_in-function-body?` の docstring | なし | なし — 別の macro が `is-function-scope` を直に呼んでも止める物は無い |

型・interface: macro は Hy の form を返す関数で、form の形を型で表す既存の機構は無い。型検査(pyright・doeff-hy-check)は
展開の結果の Python を見るが、「関数の局所に名を作らない」を型で表せないので、契約の検で補う。

---

以下は盲検 A・B の後に足した節。§1〜§5 は盲検の前の版のまま(その sha256 は evidence/design-pre-blind.sha256・写しは evidence/design-pre-blind.md)。

## 6. 盲検の結果と再現

盲検の材料と記録は evidence/ に在る。

| 記録 | file |
| --- | --- |
| 入力 | blind-input.md |
| 依頼文 | blind-a-prompt.md・blind-b-prompt.md |
| 返答 | blind-a-reply.md・blind-b-reply.md |
| 起動の記録 | blind-meta.md |

第 1 候補の gpt-6-astra はこの起動手段で選べず、Opus・effort xhigh で代用した。観測した model と effort は未確認。

| 所見 | 出所 | 主張の内か外か | 私の再現(evidence/) | 判定 |
| --- | --- | --- | --- | --- |
| R3 の反例: 本文の文の部分(`(do (f) prog)` の `(f)`・let の束ね・本文の `!`)が、節の終わり方の検めより先に走る。2bf226a96 だけの退行で、直す前の a2254d66c は検めが先 | A §5 | 内(R3・S1) | a-r3-order.log(2bf226a96 で 4 形のうち 3 形が本文が先、a2254d66c は 4 形とも検めが先)・r3-on-2bf226a96-test.log(改訂の検のうち順の 3 件が 2bf226a96 で赤) | 成立 → §7 で直した |
| 検めを「書いた場所ごとに 1 回」にする変更が、2bf226a96 の検をすべて通る。effect の型を閉包から読む handle では、後の評価で再開しない effect を resume する handler が黙って作られる | B | 内(S1・公開契約の失敗の形) | b-candidate-old-test.log(候補を当てた 2bf226a96 で 29 passed)・b-candidate-new-test.log(足した検 `test_handle_checks_clause_endings_at_every_evaluation` が DID NOT RAISE で拒否) | 成立 → §7 で検を足した |
| S2 の予想の範囲の外: 節の操作 throw を足すと、handle.hy の `_terminates`(L274・§2 の handler-expr の行の範囲 L432〜L737 の外)も変わる。clause-endings の `ENDING-OPS`・`RESUMING-OPS` を直さないと、Raise の節の throw をどの検めも止めない。型検査のための展開でも `TransferThrow` が import されずに読まれる | A §2〜§4・B §5 | 内(S2 の予想の範囲)。scope の主張は保たれた | s2-throw-run.log(throw の節の handle が traverse の後で答え -1・外の局所に TransferThrow が無い・Raise の節の throw が答え 42)・s2-throw-test.log(33 passed)・s2-throw-static.log(型検査の展開で TransferThrow の import が無い) | 成立(範囲の予想の誤り)→ §9 で引き継ぎ |
| class の本体だけに defhandler を書いた module で、その handler を使うと NameError `_doeff_do`。defhandler は `_scoped-definition` の呼び手になったが、class の本体の呼び手が負う事(補助を `_install-guard-globals` で関数の globals へ入れる)を満たしていない | A §6 | 外(R4 = 変えない範囲。前からの事) | a-class-only-defhandler-before.log・a-class-only-defhandler-revision.log(両方 NameError) | 成立(前からの事)→ §9 |
| module の段の handle の import が、利用者の module の同じ名(`Pass`)を上書きする | B §5 | 外(R4・前からの事) | b-module-level-overwrite-before.log・b-module-level-overwrite-revision.log(両方で上書き) | 成立(前からの事)→ §9 |

## 7. 改訂(doeff main f4d94cfc25203306b743abdc9ff6411bb0bdc42b・L2147・commit 4a9aafe04・f4d94cfc2)

- handle-expansion(handle.hy `defmacro handle`):
  - 関数の中では、被せる関数を本文より前の文で一意の名(`(hy.gensym "handle_install")`)に束ね、それから本文に当てる。
    展開は `(do (setv _hy_gensym_handle_install_N ((fn [] prelude … (fn [b] (WithHandler data b))))) (_hy_gensym_handle_install_N body))`。
  - Hy が本文の文の部分を前へ出しても、それは束ねる文より後に来る。
  - 外の関数に残る名はこの一意の名だけ。`_hy_` の頭で、R2 の例外に当たる。束ねた直後に読むので、未束縛にならない。
- gensym は関数の中の枝だけで引く(f4d94cfc2)。4a9aafe04 は枝の外で引いていたので、module の段の handle でも Hy の gensym の
  番号が進み、同じ module の後の展開の一意の名の番号がずれた(動きは同じ)。
- regression-test に足した検:
  - 検めが本文より先に走る事を、本文の 3 つの形(素の式・do の前の文・let の束ね)で見る
    (`test_handle_checks_clause_endings_before_the_body[…]`)
  - defk の本文の `!` が、検めが落ちる時に外の effect を出さない事
    (`test_handle_checks_clause_endings_before_the_body_performs_an_effect`)
  - 同じ handle を Ping で評価した後に Halt で評価しても検める事
    (`test_handle_checks_clause_endings_at_every_evaluation`)
- 再検証:

  | 版 | tests/test_handle_scope.py の結果 | 記録 |
  | --- | --- | --- |
  | 改訂の 1 つ目の commit(681fc8de1・gensym を引く場所を直す前) | 33 passed | s1-revision-test.log |
  | main f4d94cfc2(着地) | 33 passed | landed-f4d94cfc-test.log |
  | 2bf226a96 | 3 failed(順の検だけ) | r3-on-2bf226a96-test.log |
  | 直す前 a2254d66c | 27 failed / 6 passed | s1-prefix-test.log |

  - 本文の 4 形は main f4d94cfc2 で 4 形とも検めが先(landed-f4d94cfc-r3-order.log)。
  - 関わる検(test_handle_scope・test_handle_macro・test_nested_definition_scope・doeff-hy の test_static_check_defhandler)は、
    登記した先端 ce444dfb2(doeff-hy と検の file は f4d94cfc2 と同じ)で 102 passed(related-tests.log)。
  - S2 の試し(s2-throw-*・s2-misplaced-import-*)は 681fc8de1、S3 の負の例(s3-always-wrap-test.log)と s2-throw-static.log は
    358cd8257 で走らせた。どちらも handle.hy の関数の中の枝の形は着地と同じ(違いは gensym を引く場所と、載せ直しで入った
    defmcp-tool の型検査の展開の変更)。S3 の比べ(s3-expand-compare.txt)は main f4d94cfc2 で走らせた。

## 8. 予測と実際の変更範囲

| シナリオ | 盲検の前の予測(§3) | 実際 | 差の理由 |
| --- | --- | --- | --- |
| S1 | scope-judgment(新)・scoped-definition・handle-expansion・defhandler-expansion・regression-test | 予測どおり。加えて、盲検の後に handle-expansion を再び変え、regression-test に 5 件足した | handle-expansion の再変更は、handle-expansion の中で閉じた欠陥(Hy が本文の文の部分を式より前へ出す事を、評価の順の主張が見落とした)。他の責務へは漏れていない。検の追加は盲検 B が示した検の穴 |
| S2 | handler-imports・handler-expr(名ごとの検の表の写しも) | handler-imports・handler-expr に加えて、handle.hy `_terminates`・clause-endings(Raise の検めを保つなら)・static-view(型検査の展開の名)。scope の主張は保たれた(足した名は外の関数に現れない)。名ごとの検の表は更新しなくても、表に依らない検が誤った置き場を捉える | 節の操作の名と性質の知識が、handle.hy・clause_endings.hy・static_view.py に写しで在る(変更の前からの形)。今回の scope の分離とは独立した知識の漏れ。予想を後から広げず、範囲の外として §9 へ |
| S3 | 変更なし | 変更なし(改訂の後、module・class の段の展開と関数の中の型検査の展開が直す前と 1 字も同じ)。途中の 4a9aafe04 は gensym の番号をずらした | gensym を引く場所の誤り。handle-expansion の中で直した(f4d94cfc2) |
| S4 | 変更なし | 変更なし(直す前に compile した .pyc を、改訂の版が compile し直した) | — |

## 9. 範囲の外・引き継ぎ

- 節の操作の名の一覧の写し(S2 の差): card ki-b4c015d8ba1f に切った(ki-907f779b3290 と relates-to)。
  直し案は、操作の名と性質を 1 か所の表に置き、`_terminates`・`clause-ops`・`_rewrite-ops`・型検査の展開の名がそこから読む形。
- class の本体だけの defhandler の NameError: class の本体の handle の NameError(card ki-edab7c8fbb55)と同じ家族なので、
  その card の発言に書いた。`_scoped-definition` の呼び手が負う事のうち、class の本体の分を defhandler が満たすのは、その card の仕事。
- module の段の handle・defhandler の import が利用者の同じ名を上書きする事: 前からの事で、R4(変えない)の範囲。
  今回は直していない。

## 10. 強制方法(改訂後)

§5 の表に次を足す(どれも tests/test_handle_scope.py。root の pytest = 4 時間に 1 度の全体テストの root の段で走る):

| 守る事 | 検 | 検出の範囲 |
| --- | --- | --- |
| R3: 検めは本文の文の部分も含めて本文より先 | `test_handle_checks_clause_endings_before_the_body[expression / statement-before-the-program / let-binding]`・`…_performs_an_effect` | 本文の 4 つの形 |
| 公開契約: handle を評価するたびに型で検める(状態を持たない) | `test_handle_checks_clause_endings_at_every_evaluation` | 同じ場所を 2 つの型で評価する形。検めの関数が状態を持つ事そのものは見ない |

残る限界:

- 名ごとの検(`_HANDLE_IMPORT_NAMES`)は handler-imports の写し。誤った置き場の検出は、表に依らない
  `test_host_function_gets_only_its_own_names` の 3 つの関数に頼る(s2-misplaced-import-test.log で確かめた)。
- scope の判断の 1 点(`_in-function-body?`)を守る静的な規則は無い。
- 展開が 1 字も変わらない事(S3)の比べは手で行い、検の経路には繋いでいない。経路に繋いだ守りは
  `test_module_and_class_level_definitions_are_not_wrapped`(s3-always-wrap-test.log で、常に包む変更を拒否する事を確かめた)。

## 11. 未確認

- 盲検の実際の model と effort(blind-meta.md)。
- 型検査(pyright・doeff-hy-check)は手で走らせていない(決まりにより 4 時間に 1 度の全体テストで測る)。
  型検査のための展開の文字列は、直す前と同じ事を確かめた(S3)。
