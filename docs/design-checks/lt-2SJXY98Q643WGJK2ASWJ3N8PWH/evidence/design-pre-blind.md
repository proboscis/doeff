# handle・defhandler の展開が読む名を書き手の名前空間から分ける — 設計検証(盲検の前の版)

依頼 lt-2SJXY98Q643WGJK2ASWJ3N8PWH(card ki-16180480129e)。対象は doeff の `packages/doeff-hy`。
基準版(直す前)= doeff main `dc3385b9a4bb105937d9dad6671545f7b34c80f2`。検証する版(直した版)= doeff main `1410ce508e3baec632ea994184c884ba28ca0ee9`。
完了の範囲 = **実装完了まで**(直しは main に着地済み)。この設計検証は、着地した設計が将来の変更に対して責務を保つか、検査をすり抜ける責務違反が無いかを、盲検の失敗例で試す。

## 1. 要件(固定)

doeff-hy の macro `handle`・`defhandler` は、書いた場所(module の段・defk の本体・素の defn の本体・class の本体)の Python の名前空間へ、import と名の読みを含む code を展開する。展開が読む doeff の名 `Resume`・`Transfer`・`Pass`・`WithHandler`(session var があれば `Get`・`Put`・`Some`、型検査のための展開では `typed_resume`・`typed_transfer`)を素の名で読むと、書き手が自分の effect を同じ名にした時(振込の `Transfer`・KV の `Put`)、1 つの名を書き手の物と展開の物が分け合い、節の頭の `isinstance` が VM の node を指して effect が黙って外の handler へ抜けた。module の段では展開の import が書き手の名を VM の node で上書きして `(Transfer 5)` が TypeError になった。

| 記号 | 要件 |
| --- | --- |
| R1 | 書き手が上の名のどれかを自分の effect にした module で、module の段・defk の本体・素の defn の本体・class の本体の handle・defhandler の節が、その effect を受けて答える(節の操作 resume・transfer・reperform・:when の外れ・session var の := を通す) |
| R2 | module の段の handle・defhandler の後も、書き手の module の名は書き手の物のまま。展開は、書き手が書いていない名を module の globals に置かない |
| R3 | 実行時の展開と型検査のための展開は、これらの名を素の名で読みも束ねもしない(読むのは `_doeff_` の頭の別名だけ) |
| R4 | 展開が読む名の綴りは 1 か所の表から引き、節の操作の表 CLAUSE-OPS(card ki-b4c015d8ba1f)の行の追加・退役に追従する |

## 2. 責務(module)

| id | 実体 | 責務 | 所有する知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- | --- |
| expansion-names | `packages/doeff-hy/src/doeff_hy/expansion_names.py`(新・純粋な Python) | 展開が読む doeff の名の別名の綴りと import の唯一の定義元 | 別名の頭 `_doeff_`・名ごとの置き場(`WithHandler`=doeff_vm、`Get`・`Put`=doeff_core_effects.effects、他は doeff) | Hy の import の model の組み方、置き場ごとのまとめ方 | `alias(name)`・`alias_symbol(name)`(呼ぶたびに新しい Symbol)・`home(name)`・`import_form(names)`(Hy の `(do (import …))`)・`import_source(names)`(Python の import の文) |
| clause-ops-table | `clause_ops.hy` | 節の操作(resume・transfer・finish・reperform・pass・raise)の表 CLAUSE-OPS と、各行の書き換え | 操作の名・終わり方・引数の数・返るか・末尾で替える先・必要な node の名(nodes・static-nodes)・VM へ渡す form の作り方 | handle.hy に操作の名を写さない | `clause-op`・`named-op`・`node-names`・`ops-ending`・`linter-table-json` |
| handler-expansion | `handle.hy` | handle・defhandler の展開: 節の dispatcher・prelude の import・`WithHandler` の被せ・session の lazy 初期化(Get/Put/Some)・置き場(module/関数/class)ごとの組み立て | 展開の形、置き場ごとの import の置き方 | 節の操作の node の名の一覧(表から引く) | macro `handle`・`defhandler`・`with-handler` |
| session-write | `binding_forms.py` の `_assign`、`macros.hy` の `set!` | session var の書き換え `(do (setv x v) (yield (Put key (Some x))))` を作る | 書き換えの形 | 保存先の key の作り方(handle.hy の初期化と同じ key) | 展開の中の `(:= x v)`・`(set! x v)` |
| static-head-imports | `static_view.py` の `SESSION_STATIC_IMPORTS`・`STATIC_HELPER_IMPORTS` | 型検査のための展開の module の頭に 1 度だけ置く補助の import | 補助の名の一覧 | — | 文字列定数(doeff-hy-check が module の頭へ置く) |
| writer-namespace | 利用者の module・class・関数(doeff-hy の外) | 書き手の名の束縛 | 書き手の名 | — | — |
| name-guards | `tests/test_handle_writer_names.py`・`tests/test_expansion_names.py` | 展開の綴りの衝突を機械で止める検 | 監視する名の一覧、検の対象の展開の標本 | — | pytest の検 |

## 3. 変更シナリオと事前の主張(盲検の前に固定)

6 軸のうち適用するのは effects・storage・distribution。hardware・concurrency・simulation は不適用(§3.4)。

### 3.1 S1(effects)— 新しい節の操作を足す

- 変更: 節の操作 `(throw 例外)` を足す(続きへ例外を投げて handler を外す・doeff の node `TransferThrow`、非末尾では続きの後に節へ戻る `ResumeThrow`)。
- 主張: 変えるのは **clause-ops-table だけ**(CLAUSE-OPS に行を足し、rewrite が `alias-symbol` で node を引く)。handler-expansion・expansion-names・session-write・static-head-imports・writer-namespace は変えない。足した node は `_doeff_TransferThrow` の別名で import され、書き手の module に `TransferThrow` があっても隠さない。
- 前提: 足す node は doeff から import できる。書き手の名ではなく展開が読む名だけを別名にする。
- 予想の範囲: clause_ops.hy(+ 検)。

### 3.2 S2(storage)— session の値の保存先の効果を替える

- 変更: session val / session var の保存先を、状態の効果 `Get`・`Put`(+ 値の包み `Some`)から、別の効果の組(例 `ReadSession`・`WriteSession`)へ替える。
- 主張: 変えるのは expansion-names(置き場)と、その名を読む 3 つの綴り(handler-expansion の lazy 初期化、session-write の `_assign` と `set!`)と static-head-imports の名の一覧。clause-ops-table・置き場ごとの組み立て・writer-namespace は変えない。書き手は `Get`・`Put` の名を自由に使え続ける。
- 前提: 保存先の key の作り方(`doeff_hy.session.session_key`)は変えない。
- 予想の範囲: expansion_names.py・handle.hy の lazy 初期化の関数・binding_forms.py の `_assign`・macros.hy の `set!`・static_view.py(+ 検の監視の名)。

### 3.3 S3(distribution)— 版の違う process が共有の code store を使う

- 変更: 単一機体から、版の違う doeff-hy を持つ複数の process(worker・会話の Pod・作業木)が、共有の code store(`DOEFF_HY_CODE_STORE`)にある compile 済みの module を読む形へ。
- 主張: 古い展開(素の名を import した code)で作った cache は、新しい macro の process では使われない — macro の依存の記録(`doeff_hy_bytecode_guard`)の digest は macro の関数の code の閉包で、`expansion_names` の関数と、それが読む表の値(`_HOMES`・`PREFIX`)を覆う。表を後で書き換えても cache は冷える。変えるのは expansion-names の表の値だけで、bytecode guard は変えない。
- 前提: digest の作り方は `macro_use.py` の記述のとおり(素の値は値で、dict・list は束縛する文の source で覆う)。
- 予想の範囲: expansion_names.py の表の値(bytecode guard は変えない)。

### 3.4 不適用の軸

- hardware: 展開は compile 時の文字列・model の変換で、機器に依らない。
- concurrency: 展開は入力の model から出力の model を作る純粋な関数で、表は module の定数(書き換えない)。`alias_symbol` は呼ぶたびに新しい Symbol を作り、共有の可変な状態を持たない。
- simulation: 時計・IO の差し替えを要する形は展開に無い。展開の出力の決定性は既存の gensym の正準化(`test_gensym_canonical.py`)が持ち、この変更は gensym を足さない。

## 4. 強制方法(実装済み・main に着地)と検査の実体

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 検出範囲 |
| --- | --- | --- | --- | --- |
| 展開が書き手の名を隠さない(R1・R2) | 書き手が 7 つの名を自分の effect にした module を、4 つの置き場 × (handle の 2 形・defhandler の session var)で受けて答える挙動の検 + module の段の後の名が書き手の物のままの検 | `tests/test_handle_writer_names.py::test_clause_answers_the_writers_effect_named_like_an_expansion_name`(84 件)・`::test_module_level_handle_and_defhandler_leave_the_writers_names_alone`・`::test_module_level_handle_and_defhandler_do_not_put_the_bare_names_in_the_globals` | 通常の pytest(`make test-changed`・日次) | 標本の source の置き場 4 × 形 3 × 名 7 |
| 展開が素の名を読みも束ねもしない(R3) | 標本の source 8 本を実行時と型検査のための展開で compile した AST で、`ast.Name`(Load)と import の別名に素の 9 つの名が無い検 | `tests/test_handle_writer_names.py::test_expansion_neither_reads_nor_binds_a_bare_expansion_name`(16 件) | 同上 | 標本 8 本(module/defk/defn/class の handle・defhandler・legacy `set!`) × 2 モード |
| 綴りは 1 か所の表から(R4) | doeff_hy の `.hy` で、`import`/`require` の形の外に素の Symbol として書かれた 9 つの名があれば赤 + `.py` の `Symbol("名")` の呼びがあれば赤 | `tests/test_expansion_names.py::test_no_source_spells_an_expansion_name_bare` | 同上 | doeff_hy の `*.hy`・`*.py`(`expansion_names.py` を除く)の Symbol model と `Symbol("…")` の呼び |
| 表の正しさ | 表の名が置き場から import できる検、`import_form`/`import_source` の形の検 | `tests/test_expansion_names.py` | 同上 | 9 つの名 |
| 節の操作の表への追従(R4) | CLAUSE-OPS に足した行の node が、実行時・型検査のための展開で別名で import され読まれる検 | `tests/test_handle_clause_ops.py`(`TransferThrow`・`ResumeThrow` の行をテストの中で足す) | 同上 | 足した行 |
| 型検査のための展開 | pyright strict で、書き手の `Transfer`・`Pass`・`Put` の effect を持つ module が赤 0 | `packages/doeff-hy/tests/test_static_check_defhandler.py::test_writer_effects_named_like_the_expansion_names_are_not_an_error` | 同上(pyright が PATH に在る時) | 検体 1 本 |
| 依存の方向 | 品質検査の module 契約: `doeff-hy-expansion-names`(純粋・依存先なし)を `doeff-hy-binding-forms`・`doeff-hy-macros`・`doeff-hy-static-check` だけが使える | `.agents/code-quality.d/doeff-hy-expansion-names.json` | `code-quality --scope changed`(land の門) | 契約に載せた module だけ(handle.hy・clause_ops.hy は未登録) |

検査の実行コマンド(作業木の根で):

```
uv run pytest tests/test_handle_writer_names.py tests/test_expansion_names.py tests/test_handle_clause_ops.py tests/test_handle_scope.py \
  packages/doeff-hy/tests/test_static_check_defhandler.py -q -W ignore
```

## 5. 設計者が既に知っている限界(盲検へは渡さない)

- 素の Symbol の検は、監視する 9 つの名だけを見る。CLAUSE-OPS の行が足す新しい node の名(例 `TransferThrow`)を素の Symbol で書いても、この検は拾わない(実行すれば別名の import と食い違って NameError になるので、黙りはしない)。
- 検の標本に無い展開の経路(新しい macro の経路・文字列から `hy.read` する綴り)は、展開の構造の検の外。
- 展開の局所名 `effect`・`k`(節の関数の引数)と書き手の欄の名の衝突は、この設計の外(欄の名が `k` の節は TypeError で落ちる — 別の根)。`doeff_hy.declarations` の import が束ねる `doeff_hy`・Hy の compiler が置く `hy` も素の名。
- 品質検査(changed)は macros.hy の既存の指摘 20 件で failed のまま(基底と同数)。handle.hy・clause_ops.hy は module 契約に未登録。
- 実行時の展開の「基準版との比較」は、展開の AST を直接比べる検ではなく、挙動と構造の検の組。
