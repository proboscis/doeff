# 失敗例探しの材料: doeff-hy の handle・defhandler の展開が、関数の中で import の名をどこに束ねるか

対象の版: doeff 2bf226a96ed1a2abb3e66dbaf1c5119a4e61098c(この変更が入った main の版)。変更の前の版は a2254d66cfc52f9fa9c3b4829bff2d76191bccb3。
変更の差分は `git -C <あなたの作業木> show 2bf226a96 -- packages/doeff-hy/src/doeff_hy/handle.hy packages/doeff-hy/src/doeff_hy/macros.hy tests/test_handle_scope.py` で読める。

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
- tests/test_handle_scope.py(新)。

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

## 6. 走らせ方(あなたの作業木で)

- 環境: `cd <あなたの作業木> && uv sync --group dev`(数秒)。以後 `uv run …` で走らせる。Python は 3.14(free-threading の build)。
- 検: `uv run pytest -q tests/test_handle_scope.py`。関わる既存の検: `tests/test_handle_macro.py`・`tests/test_nested_definition_scope.py`・
  `tests/test_fnk_do_bang_scope.py`・`packages/doeff-hy/tests/test_defhandler_effect_type_filter.py`・`packages/doeff-hy/tests/test_static_check_defhandler.py`。
- Hy の source を 1 つの module として読み込んで走らせる形は tests/test_handle_scope.py の `_eval_module` と `_run` に在る。
- 展開を Python の source で見る: `hy.compiler.hy_compile(hy.models.Lazy(iter(hy.read_many(src))), <types.ModuleType の module>, filename="<x>")` を
  `ast.unparse` する(module は sys.modules に登録しておく)。型検査のための展開は `doeff_hy.static_view.static_view()` の with の中で同じ事をする。
- 作業木の中の file を変えて試すのは自由(この作業木は使い捨て)。`~/repos/doeff` と他の作業木は変えない。全数の検(pytest の全体)は走らせない。
