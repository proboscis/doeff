# 設計検証: class の本体に直に書いた doeff-hy の handle・defhandler の節が、展開の import した名を読む(card ki-edab7c8fbb55)

依頼 lt-CZC8SGS3C6DFDGSGG64YF49DH9(class investigate・依頼者 m-JMAH2ATPWCCNBDF31QXCNTMACZ)・担い手 c-Z48YJMZ0CSH4120XECK1TQWS0P(claude-opus-5-5)。

## 0. 完了の範囲

実装完了まで。直しは doeff main 08ddfc707b(L2167・2026-10-12 12:40 JST 着地)。変更の前の版は 8fcefa7c6(08ddfc707 の親)。
handle.hy・macros.hy・tests/test_handle_scope.py は 8fcefa7c6 と、直しを書いた時の基 fe951a108 で同じ中身。
この file の §1〜§5 は盲検の前に書いた(保存した版の sha256 は evidence/design-pre-blind.sha256)。§6 から後は盲検の後に書いた。

## 1. 固定した要件

- R1: class の本体に直に書いた handle の節が、Transfer(末尾の resume)・Resume(末尾でない resume)・Pass(`:when` の外れ)を
  読んで走る(card の受入 ce8ea7c83「class の本体に直に書いた handle の節が Transfer・Resume を読んで走る事を、doeff-hy の検で
  確かめて main で緑(直す前は NameError で赤)」)。
- R2: class の本体に直に書いた defhandler(引数なし・引数あり・`session var` を持つ)が走る
  (同じ家族の所見 — 設計検証 lt-D4D7 の盲検 A の補足: class の本体だけの defhandler が NameError '_doeff_do')。
- R3: class の本体の handle・defhandler は、module の globals に名を足さない。節は module の globals の同じ名
  (書き手が別の物に束ねた Transfer など)に左右されない。
- R4: module の段と関数の本体の中の実行時の展開、型検査のための展開(doeff-hy-check)は変えない。class の本体の defhandler の
  name の `__qualname__` は `Class.name` のまま。
- R5: class の本体の handle の本文(第 1 引数)は書いた場所(class の本体)で評価する — 本文は class の段の名を読める。
  節の終わり方の検めと handler の組み立ては、本文の式より前に走る(関数の中の handle と同じ順)。

利用の状況: handle・defhandler は普通 module の段か defk の本体に書く。class の本体には、handler を class の属性として
まとめる形(`(defclass Handlers [] (defhandler retry [n] …) (defhandler log …))`)や、class の属性に Program を置く形
(`(defclass Job [] (setv program (handle …)))`)で書かれる。

## 2. 責務(module)と公開契約

直した版 08ddfc707 の path と行。

| id | 実体 | 責務 | 持つ知識 | 隠す知識 |
| --- | --- | --- | --- | --- |
| scope-judgment | packages/doeff-hy/src/doeff_hy/macros.hy L679 `_in-function-body?`・L689 `_in-class-body?`(新) | 展開の場所の判断: 関数の本体の中か(import すると外の関数の局所の名を作る所)・class の本体に直にか(import した名が class の属性になり、展開の中で作る関数から見えない所) | Hy の compiler の scope の読み方(`hy.scoping` の nearest-python-scope・is-function-scope・ScopeFn — defclass は関数でない ScopeFn を作る)。型検査のための展開ではどちらも偽 | Hy の scope の内部の API を各 macro から |
| scoped-definition | macros.hy L653 `_scoped-definition` | 名を定義する macro の文の並びを、関数の中では `(setv name ((fn [] … name)))` に入れる。module と class の本体では並べる | 包む形と、呼び手が負う事(docstring)。class の本体で関数が名で読む補助は、`_install-guard-globals` で globals へ入れるか、組む関数の中で import して閉包から読ませる | 包むかの判断を呼び手から |
| guard-globals | macros.hy L559 `_install-guard-globals` | defk・deftest・defmcp-tool・do! の関数の module の globals に補助を setdefault で入れる | 補助の名と値 | — |
| handle-expansion | packages/doeff-hy/src/doeff_hy/handle.hy L739 `defmacro handle` | 本文に inline の handler を被せる Program の式。関数の本体の中と class の本体では、import・検め・組み立てを `(setv <gensym> ((fn [] prelude (setv data h-expr) (fn [b] (WithHandler data b)))))` に入れ、`(<gensym> body)` で本文を書いた場所で評価する。module の段では並べる | 評価の順(prelude → 組み立て → 本文 → WithHandler) | import の名の束ね方を本文と書いた場所から |
| defhandler-expansion | handle.hy L811 `defmacro defhandler`(L927〜L931 の `imports`・`outer-imports`・`inner-imports`) | 名つきの handler(引数なし = Program → Program の関数・引数あり = それを作る関数)の定義。class の本体では import を handler を組む関数(引数なしはすぐ呼ぶ関数・引数ありは name の関数そのもの)の中に置く。それ以外は定義の前に並べ、文の並びを scoped-definition に渡す | handler の値の形・属性(`__doeff_body__`・`__doeff_name__`・宣言) | — |
| handler-imports | handle.hy L53 `_do-import`・L65 `_clause-endings-import`・L74 `_vm-imports`、defhandler の `lazy-imports` | 展開が名で読む実行時の名(`_doeff_do`・`Resume`・`Transfer`・`Pass`・`WithHandler`・`_doeff_check_clause_endings(_once)`、session の `Some`・`Get`・`Put`・`_doeff_session_key`)の import の形 | 名と提供元の module | 提供元を展開の書き手から |
| handler-expr | handle.hy L432 `_rewrite-ops`〜L717 `_effect-parameter`(`_build-clause`・`_build-handler-expr`・`_passed-effects-source`)、L198 `_build-lazy-init-forms` | 節の dispatcher の関数 `(_doeff-do (fn [effect k] (cond …)))` の組み立てと節の操作の書き換え。節の本体は dispatcher の関数の中で、上の名を名で読む。節の `<-` の束ねは `__import__` で引く(書いた場所に依らない) | 節の形・resume / transfer の書き換え | — |
| clause-endings | packages/doeff-hy/src/doeff_hy/clause_endings.hy | 節の終わり方と effect の型の再開の宣言の照合 | Resumption の規則・操作の名の性質 | — |
| static-view | packages/doeff-hy/src/doeff_hy/static_view.py と `static-view-enabled`・`_static-view?` の枝 | 型検査のための展開(型付きの名は module の頭に 1 度だけ import) | 型付きの名 | — |
| bytecode-guard | packages/doeff-hy/src/doeff_hy_bytecode_guard | 使った macro の digest が変わった Hy の module の古い .pyc を使わず compile し直す | 使った macro の digest | — |
| regression-test | tests/test_handle_scope.py | R1〜R5 と、関数の中の handle・defhandler の検 | §4 の検 | — |

公開契約(変えていない):

- `(handle body clause…)` は Program(WithHandler の node)を返す式。失敗: 展開の時の SyntaxError・評価の時の ClauseEndingError(TypeError の子)。
- `(defhandler name [params]? ["doc"]? {decl}? clause…)` は name に handler の関数(引数ありなら handler を作る関数)を束ね、
  `__doeff_body__`・`__doeff_name__`・宣言の属性を置く。
- 展開の内部(どこで import するか・一意の名)は公開契約ではない。

状態と寿命:

- handle の被せる関数(と節の dispatcher の閉包)は、書いた場所の一意の名(gensym)の束縛と Program が生きる間。class の本体では
  一意の名は class の属性として残る(Hy が class の本体の `fn` に作る `_hy_anon_*` の関数と同じ扱い)。
- defhandler の handler の値は name の束縛の寿命。import した名は組む関数の局所(と、節の関数・被せる関数の閉包の cell)。
- session var の値は Get / Put の effect で state の handler が持つ(鍵は module の `__name__`・handler の名・変数の名)。

## 3. 将来の変更シナリオと事前の主張(盲検の前)

| id | 軸 | 適用 | 変える要求 | 主張 | 予想する変更範囲 |
| --- | --- | --- | --- | --- | --- |
| S1 | effects | 適用 | 利用者が class の本体に handle・defhandler を直に書く(引数なし・引数あり・session var・class の本体の let の中・関数の中の class・同じ class に 2 つ・本文が class の段の名を読む) | 名をどの scope で束ねるかは scope-judgment と handle-expansion・defhandler-expansion だけが決める。節の関数・組む関数・被せる関数は閉包(か組む関数の局所)から名を読み、module の globals を読まず書かない | scope-judgment(`_in-class-body?` の新設)・handle-expansion(枝の条件)・defhandler-expansion(import の置き場)・regression-test。scoped-definition は docstring だけ。handler-imports・handler-expr・clause-endings・static-view・bytecode-guard・guard-globals は変えない |
| S2 | effects | 適用 | 新しい節の操作(例 `throw` → VM の node `TransferThrow`)のため、handler-imports に node の名を 1 つ足し、handler-expr の書き換えがそれを読む | class の本体でも、足した名は handler-imports の import の並び(handle の prelude・defhandler の `imports`)を通って組む関数の中で import される。scope-judgment・handle-expansion の枝・defhandler-expansion の置き場の分け方は変えない | handler-imports・handler-expr。節の操作の名の性質の写し(clause-endings の ENDING-OPS・static-view の型付きの名)は前からの写しで、card ki-b4c015d8ba1f の範囲(この変更の分離とは別) |
| S3 | effects | 適用 | 既存の利用者(module の段の handle・defhandler、defk・素の defn の本体の中の handle・defhandler)と doeff-hy-check の型検査 | `_in-class-body?` は module の段・関数の中・型検査のための展開で偽なので、それらの展開は直す前と 1 字も同じ。module の段の import は今までどおり module の globals に置く | 変更なし |
| S4 | storage | 適用 | 直す前の doeff-hy で compile した利用者の Hy の module(.pyc・code store)が残ったまま、直した doeff-hy に上げる | bytecode-guard が使った macro(handle・defhandler)の digest の変化で compile し直すので、古い展開(NameError になる形)の .pyc は使われない。bytecode-guard は変えない | 変更なし(bytecode-guard の既存の働き) |
| — | hardware | 除外 | — | 展開と Python の名の引きだけの変更で、機体の資源・命令の種類に依らない | — |
| — | distribution | 除外 | — | 1 つの process の中の名の引きの変更。handler の値を別の process へ名で送る形は無く、class の本体の defhandler の name の `__qualname__` は変えない(R4)。節の関数・被せる関数は前から名で引けない入れ子の関数 | — |
| — | concurrency | 除外 | — | class の本体は定義の時に 1 度、定義する thread で評価される。閉包の cell は handler を返す前に 1 度書かれ、後は読むだけ。共有して書き換える状態を足さない(session の値は Get / Put の effect で state の handler が持つ — 前と同じ) | — |
| — | simulation | 除外 | — | 時刻・乱数・外の IO を足さない。展開は source から決まる(一意の名の番号は compile の口が正準化する) | — |

S1 の前提: class の本体の handle の本文は、class の本体の段で評価できる式(class の段に `yield` は無い)。effect の型と節が読む名は
module の段か外の関数に在る(class の段の名は節の関数から前も後も見えない — Python の名の引きの決まり)。

## 4. 強制方法(検査の実体・設定・検出範囲)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| R1・R2(class の本体の節・組む関数が名を読める) | 実行の契約テスト: class を 1 つだけ置いた module(module の段に defk・handle・defhandler を置かない — それらの import が module の globals に名を置いて穴を隠すため)で、handle 3 形と defhandler 3 形を走らせ 101 を得る | tests/test_handle_scope.py L366 `test_class_body_handle_clauses_read_their_operations`(`_CLASS_ONLY_HEADER` L308・`_CLASS_BODY_CASES` L315) | pytest の root の testpaths `tests`(pyproject.toml L108)。4 時間ごとの全体の検証と、手で file を名指した実行 | 検が走らせる形(節の操作 Transfer・Resume・Pass、session var)の外の名は、その名を使う節を class の本体で走らせる検が無ければ捉えない |
| R3(module の globals を読まず書かない) | 実行の契約テスト: module の段に Transfer・Resume・Pass を文字列に束ねた上で R1・R2 の 6 形を走らせ、module の globals の名の集まりが class だけの module と同じ事と、書き手の名が変わらない事を確かめる | 同 L383 `test_class_body_handle_leaves_module_globals_alone`(`_MODULE_OWN_NAMES` L375) | 同上 | module の globals だけを見る。class の属性は見ない |
| R4(module の段・関数の中の展開と `__qualname__`) | 実行の契約テスト: module の段の handle の import が module の globals に在る(`WithHandler`)・defhandler の `__qualname__`(`top_h`・`Holder.inner_h`)・class の本体の defhandler が走る。関数の中の 8 形・外の関数の局所に import の名が無い事・module の globals を変えない事 | 同 L284 `test_module_and_class_level_definitions_are_not_wrapped`・L174〜L216 の関数の中の検 | 同上 | 展開の字の一致は検に無い(盲検の前の確かめは §5 の 1 回の比べ) |
| R5(本文の評価の場所と順) | 関数の中の handle の順の検(本文の 3 形で検めが先・本文の `!` の effect が先に出ない・評価ごとの検め)。class の本体は同じ枝を通る | 同 L243・L252・L267 | 同上 | class の本体での順を直に確かめる検は無い(同じ枝を通る事に依る) |
| 型・静的な規則 | Hy の macro の展開の場所の決まりは型で表せない(展開の時の compiler の scope の判断)。semgrep・型検査の規則は足していない | — | — | 静的には強制していない。上の実行の契約テストだけ |

走らせ方: doeff の作業木で `make sync` の後、`uv run pytest tests/test_handle_scope.py -q`。

## 5. 盲検の前に分かっている限界と確かめ(盲検には渡さない)

- 直す前と後の展開の比べ: module の段(handle・引数なし / 引数あり / session の defhandler)・関数の中の handle・defhandler・class の
  本体の 4 形を、実行時と型検査のための展開で Python の文に落として比べた(使い捨ての script・2026-10-12)。型検査の展開は全体が同じ、
  実行時の展開は module の段と関数の中(先頭から 290 行)が同じで、class の本体だけが変わった。§6 で記録に残す。
- 直す前の macro に対し、R1・R2・R3 の検 12 件が赤(NameError と、module の段の同じ名を節が読む `'str' object is not callable`)、
  既存の 33 件は緑。直した後は 45 passed。
- 限界 L1: §4 の R1 の行の限界のとおり、defhandler の `imports` の並びの外(例えば文の並びに直に足した import)や handle の prelude の
  外に新しい名の import を置くと、class の本体ではその名が class の属性になり節から見えない。それを捉えるのは、その名を使う節を
  class の本体で走らせる検だけ。
- 限界 L2: 型検査のための展開(doeff-hy-check)の class の本体の handle・defhandler は変えていない(R4)。pyright が class の本体の
  節の関数の中の `Pass`・`WithHandler` をどう読むかは確かめていない。
- 限界 L3: module の段の handle・defhandler の import は、書き手の module の同じ名(Transfer など)を上書きする(前からの形・R4 で
  変えない)。
