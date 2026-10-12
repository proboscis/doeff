## 失敗例: 関数の中の handle の検めを「書いた場所ごとに 1 回」にする高速化

提示された検はすべて通るのに、`handle` を評価した時に出るはずの ClauseEndingError を黙って飛ばす実装を 1 つ作り、実際に走らせて確かめました。作業木は元の版(2bf226a96)に戻してあり、差分は `/tmp/blind-b-d4d7/candidate.diff` に残しています。

### 1. 機能要求と差分

**要求(想定):** defk の本体に書いた `handle` は、外の関数を呼ぶたびに節の終わり方の検め(`resumption-of` と `match`)をやり直しています。defhandler は `check-clause-endings-once` で 1 回しか検めないので、handle も同じ考え方で「書いた場所ごとに 1 回」にしたい、という依頼です。材料の hardware の軸が評価ごとの手間に触れており、このコードの註にも同じ種類の高速化(#1931・#2871 など)が多く見られます。

**差分:** 実装は 2 file だけで、検の file は変えていません。

`clause_endings.hy` に足したもの:
```hy
(import inspect)
(import weakref)
(setv _CHECKED-HANDLE-CODES (weakref.WeakSet))
(deff check-clause-endings-per-site [data handler specs]
  {:pre [(: data Callable) (: handler str) (: specs Callable)] :post [(: % None)] :tags {…}}
  "関数の中の handle の照合を、書いた場所ごとに 1 回だけ行うため(印を data の関数の code に置く)"
  (let [code (. (inspect.unwrap data) __code__)]
    (when (not-in code _CHECKED-HANDLE-CODES)
      (check-clause-endings handler (specs))
      (.add _CHECKED-HANDLE-CODES code)))
  None)
```

`handle.hy` は関数の中の枝だけを変えました。module の段の枝と型検査のための展開は、前と 1 字も変わりません。
```hy
`(((fn [] ~(_do-import) ~(_vm-imports)
          ~(_clause-endings-import 'check-clause-endings-per-site '_doeff-check-clause-endings-per-site)
          (setv __doeff-handler-data__ ~h-expr)
          (_doeff-check-clause-endings-per-site __doeff-handler-data__ "handle" (fn [] [~@specs]))
          (fn [__doeff-body__] (WithHandler __doeff-handler-data__ __doeff-body__))))
  ~body)
```

### 2. 本来の持ち主と、侵している契約

- **状態の持ち主が移る:** defhandler の「検め済み」の印は handler の値(data の属性)が持ち、値と寿命を共にします。handle は評価のたびに検める作りで、状態を持ちません。この差分は、印を clause-endings の module に置いた process 全体の WeakSet へ移します。材料の「check-clause-endings は状態を持たない」「足した共有の可変の状態は無い」(concurrency の軸)に反します。
- **判断が移る:** effect の型は評価のたびに閉包から読まれます(S1 の前提「effect の型は閉包で読める」・`_effect-parameter` の註「外の関数の型も解ける」)。この「評価ごとに型が変わり得る」という知識は handle の展開が持つものです。差分は clause-endings に「code が同じなら同じ handler」と決めつけさせます。しかも WeakSet は code を値の等しさで比べるので、同じ source を同じ行に持つ別の module の code まで同じものとみなします。
- **公開契約が破れる:** 失敗の形のうち「handle を評価した時の ClauseEndingError(型での検め)」が、同じ場所(等しい code)の最初の評価でしか出なくなります。その結果、再開しない effect(Resumption.NEVER)を resume する handler が黙って作られます。これは R15 が止めるはずの「失敗したのに成功したかのように続く」状態です。

### 3. 提示された各検査が拒否しない理由

候補を当てた上で、提示された 6 file を名指して走らせました。結果は **124 passed, 1 skipped** です(実測)。skip は `test_defhandler_effect_type_filter.py:127` の Python 3.14 の版による skip で、この変更とは関係ありません。

- **`test_handle_inside_function_runs`(8 形):** どの形も節の型は `Bump` に固定されています。最初の評価で検めが走るので、以後の省略は結果に出ません。
- **`test_outer_function_binds_no_handle_import` と `test_host_function_gets_only_its_own_names`:** 足した import は、すぐ呼ぶ関数の中にあります。外の関数の局所の名は `_hy_` の頭の名だけです。
- **`test_handle_inside_function_leaves_module_globals_unchanged`:** 新しい状態は clause_endings の module に置かれ、利用者の module の globals は変わりません。
- **`test_handle_checks_clause_endings_before_the_body`:** 他のどの検にも無い source を、新しい module で 1 回だけ評価する検です。検めは走り、本文より前に落ちます。
- **`test_module_and_class_level_definitions_are_not_wrapped` と `test_static_check_defhandler.py`:** module の段の枝と型検査のための展開は変えていません。
- **scope の判断の 1 点:** 元から規則がありません。この候補は `is-function-scope` を呼ばないので、そもそも関係しません。
- **欠けている検:** 強制方法の表には、「評価のたびに型で検める」ことと「clause-endings が状態を持たない」ことを見る検がありません。

### 4. 確かめ方(同じ設定で通過と違反の両方)

1. `cd /home/ubuntu/.worktrees/doeff-wt-blind-b-d4d7 && git apply /tmp/blind-b-d4d7/candidate.diff`
2. `uv run pytest -q tests/test_handle_scope.py tests/test_handle_macro.py tests/test_nested_definition_scope.py tests/test_fnk_do_bang_scope.py packages/doeff-hy/tests/test_defhandler_effect_type_filter.py packages/doeff-hy/tests/test_static_check_defhandler.py` → 124 passed, 1 skipped(実測)
3. `uv run python /tmp/blind-b-d4d7/repro.py` を走らせます。入力は `(defn answer-with [etype value prog] (handle prog (etype [] (resume value))))` と、NEVER の `Halt` です。同じ module で、先に `Ping` で評価し、次に `Halt` で評価します。

| 段 | 元の版(2bf226a96)(実測) | 候補(実測) |
|---|---|---|
| A1: 最初の評価が `Halt` | ClauseEndingError | ClauseEndingError |
| B2: `Ping` の後に `Halt` で評価 | ClauseEndingError | エラーにならない |
| B3: B2 の Program を走らせる | ClauseEndingError | `1` が返る(`(<- x (Halt))` が 0 を受けて続く) |

外の関数の局所の名は、どちらの版でも `('etype','value','prog','_hy_anon_4')` だけでした。

4. `uv run python /tmp/blind-b-d4d7/repro_cross_module.py` は、同じ source を別の module として 2 回読み込みます。2 つの code は別のものですが、値としては等しい(`==` が真)ことを確かめました。module C で `Ping` を評価した後、module D の最初の `Halt` の評価が、候補では `1` を返し、元の版では ClauseEndingError になりました(実測)。

再現の出力は `/tmp/blind-b-d4d7/repro-*.log` にあります。

### 5. 未確認の前提・不足する情報

- **走らせていないもの:** pyright・doeff-hy-check・semgrep、pytest の全体、docs/adr の実行できる ADR(収集されないという警告が出ていました)。これらが拒否するかどうかは分かりません。関連する 5 file は、規則に従って元の版では走らせていません。元の版で検を走らせたのは `test_handle_scope.py` だけで、結果は 29 passed です。
- **free-threading の競合:** 共有の WeakSet が free-threading の build で競合を起こすかは推測で、測っていません。
- **再現の経路:** 再現は `hy.eval` で行いました。file から bytecode guard を通して import する経路は試していません。
- **入力の現実味:** 「節の頭が引数の変数」という形を、この repo の中で探しても見つけられませんでした(`(handle` を使う `.hy` は数件だけ)。現実にどのくらい書かれるかは分かりません。材料は閉包から型を読むことを支えるとは書いていますが、型が評価のたびに変わる使い方を契約として認めるかどうかは明記していません。
- **探したが確かめていない候補:**
  - S2 で節の操作を足すと、`handle.hy` の `_terminates` と clause_endings の `ENDING-OPS` も直す必要が出そうです。S2 の「変えるのは handler-imports と handler-expr だけ」という範囲から漏れている可能性があります。
  - module の段の handle・defhandler は `Transfer`・`Pass` などを利用者の module の globals に import します。利用者が同じ名の effect を持っていると上書きしてしまう恐れがあります。これは変更の前からある話です。
