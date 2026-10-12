提示された検査をすべて通るのに、`defk` の展開が決める「作った値の class」を期待の出力に写し込む例を、1 つ実際に作って確かめました。作業木は最後に元へ戻してあります(最後の節)。

## 1. 機能要求と、提案するコード

**機能要求(現実的な形):** ex04 の頭の註は「Program is DATA — `(pipeline items)` は計算を組み立てるだけで走らせない」と言葉で書いているだけです。これを目に見せる短い例 ex10 を足したい、という要求です。作った値を印字してから run します。

**差分(新しい file 2 つだけ。検の file・表・基底は触らない):**

`packages/doeff-traverse/examples/ex10_program_is_data.hy`
```hy
(require doeff-hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff [run])
(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_traverse.collection [Collection])
(import doeff_traverse.effects [Traverse])
(import doeff_traverse.handlers [sequential fail-handler :as fail_handler])

(defk double [x]
  "Per-item step: the work each item of the pipeline does."
  {:pre [(: x int)] :post [(: % int)]}
  (* x 2))

(defk pipeline [items]
  "The whole pipeline over items — building it must not run any item."
  {:pre [(: items list)] :post [(: % Collection)]}
  (<- results (Traverse double items :label "double"))
  results)

(setv program (pipeline [1 2 3]))
(print "built:" (type program))          ; ← macro が作った値の class を印字
(setv result (run (scheduled ((sequential) (fail_handler (try_handler program))))))
(print "valid:" (. result valid_values))
```

`packages/doeff-traverse/examples/expected/ex10_program_is_data.txt`
```
built: <class 'doeff_vm.doeff_vm.Call'>
valid: [2, 4, 6]
```

## 2. 本来の持ち主と、侵す契約

- **持ち主:** `(pipeline [1 2 3])` が返す値の class は、`defk` の展開が決めます(`_build-fn-with-contracts` が出す `(defn [_doeff_do] …)` の decorator)。これは責務表で macros が隠す「作る値の class」です。
- **何がどこへ移るか:** その class(`Call`)という doeff-hy の中身の知識が、examples/expected の期待の出力へ写ります。
- **侵す契約:**
  - **R1:** 値の class を実行時に印字する例を足したのに、例の検は赤になりません。
  - **R2 と S1:** doeff-hy が答えを変えずに `defk` の展開を変えると、例と期待の出力を書き直させます(4 の手順 3 で実測)。
  - **C3 の主張:** 「値の class 名・型・属性を印字する例は変異の下で出力が変わる」は、traverse と for/do が作る値にしか成り立ちません。
- **抜けの元:** `UNMUTATED_MACROS["defk"]` の訳は「関数を定義する文 — 包める値が無い」です。しかし、定義した関数を呼んだ値(Program)は実行時の値で、包めます。表が認める「残る限界」は「定義した関数の型・署名」だけで、呼んだ値は含みません。どの macro が実行時の値を作るかは doeff-hy の知識なのに、検の側が手書きの表と訳で持っていて、その訳の正しさを確かめる物がありません。

## 3. 提示された各検査が拒まない理由

- **`test_example_runs`:** 出力は毎回同じです。class の repr には path も番地も入りません。
- **`test_case_failure_shows_in_compared_output`:** Traverse の効果を直に使うので件を 3 回呼びます(`case-calls: 3`)。最初の件を失敗させると `valid: [4, 6]` に変わり、期待の出力に合いません。
- **`test_example_runs_a_program`:** `program-runs: 1` です。
- **`test_example_output_survives_expansion_mutation`:** 例は traverse も for/do も使わないので、変異は何も包みません。`program` は defk を呼んだ値で、`MUTATED_MACROS` の外です。変異の子の出力は期待の出力と同じでした。
- **`test_example_uses_only_doeff_hy_macros`:** defmacro・doeff-hy の外からの require・macroexpand のどれも使いません。
- **`test_expected_output_places_nothing_unplaceable`:** `_UNDERSCORED_WORD` は語頭が `_` の語だけを拾います。`doeff_vm` の `_vm` は前が語の字なので拾いません。隠す名・`__import__`・path・frame の行もありません。
- **`test_expected_output_places_only_known_marks`:** 期待の出力に `…` がありません。
- **`test_example_expansions_show_their_marks`:** `defk` と `<-` の展開は印を持ちます(ex02 と同じ使い方です)。
- **`test_example_macros_are_mutated_or_named`:** 使う macro は `defk` と `<-` だけで、どちらも `UNMUTATED_MACROS` にあります。
- **表と置き場の検(`test_tables_name_examples_as_they_are`・`test_unmarked_table_names_unmarked_macros`・`test_examples_dir_holds_only_examples_and_their_expected_outputs`):** 置いたのは直下の `.hy` と `expected/` の `.txt` だけで、表の行は増えも減りもしません。
- **module 契約(code-quality):** 例は module `doeff-traverse-examples` に属し、import は allow の内です。traverse を使わないので、private-symbol は出ません。`doeff-traverse-tests.json` は検の file を変えないので当たりません。

## 4. 確かめる手順と結果(どれも実測)

1. **例の検:** 2 file を置いて `uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q --tb=short -rA` を走らせると、**84 passed, 1 skipped** でした。ex10 の 6 本と表の検はすべて PASSED です。skip は空の BROKEN の parametrize です。
2. **品質の検:** `git add -N` の後に code-quality を `--scope changed` で走らせると、`--base HEAD` と `--base $(merge-base HEAD origin/main)=dc3385b9a` のどちらも **passed** でした。ただし fast の段なので型検査は含みません。また `--base HEAD` の 1 回目は 60 秒の上限を越えて「未実行」になり、走らせ直して通りました。
3. **責務違反の確認:** `macros.hy` の defk の decorator に、呼んだ値を「何もしない handler の下で走らせる Program」で包む関数を足しました。検の変異と同じ、答えを変えない形です。
   ```hy
   ;; defk: ['_doeff_do] → [`(. (__import__ "doeff_hy.macros" :fromlist #("_wrap_call_values")) _wrap_call_values) '_doeff_do]
   (defn _wrap-call-values [kleisli] … (WithHandlerType pass-everything (kleisli #* args #** kwargs)) …)
   ```
   自分用の空の `DOEFF_HY_CODE_STORE` で同じ pytest を走らせると、**2 failed, 82 passed** でした。
   - 赤は `test_example_runs[ex10…]` と `test_example_output_survives_expansion_mutation[ex10…]` の 2 本だけです。
   - 差は `-built: <class 'doeff_vm.doeff_vm.Call'>` / `+built: <class 'doeff_vm.doeff_vm.WithHandler'>` の 1 行で、`valid: [2, 4, 6]` は同じでした。
   - 既存の 9 本は全部緑です。

   つまり、例を足した時は通り、答えを変えない macro の変更の時に、macro を直した人の手で初めて赤になります。
4. **戻した後:** 78 passed, 1 skipped です。`macros.hy` の sha256 は元と一致しました。

## 5. 未確認の前提・不足する情報

- **推測(走らせていない物):**
  - 3 の defk の包み替えは、§6 の前提「macro が作る値を 1 つの Program で包む」を defk に当てた作りの変更です。doeff-hy が実際にこう変える予定かは知りません。doeff-hy 自身の検もこの変更では走らせていません。答えが変わらない事は、ex10 と既存 9 本の出力でだけ確かめました。
  - fnk を呼んだ値と、defhandler を body に当てた値にも同じ抜けがあると推測しています。
  - traverse と for/do でも、変異は一番外の値しか包みません(変異の入口の docstring に書いてある)。そのため、handler が受け取る Traverse の効果の中身(`f` の型など)を印字する例も、変異では見分けないと推測しています。
- **副次の実測(主題の外):** 最初は traverse の macro を使って ex10 を書きました。その版は code-quality の `private-symbol` で赤になりました。既存の traverse と for/do の例 7 本には、同じ規則の行(subject `doeff_hy/static_types.py`)が基底に登録されています。ex10 の行の subject がそれと同じかは推測です。この通りなら、traverse を使う新しい例は、S2 の予測(変わるのは examples と expected だけ)のままでは入りません。
- **bytecode の置き場:** 3 の実験は、私用の `DOEFF_HY_CODE_STORE` とは別に、作業木の `__pycache__`(git の無視の対象)に pyc を書きました。書いたのは変えた macro で compile した `ex04_simple`・`ex10`・`macros` の 3 つです。消さずに `/tmp/ki-b7c4-blindb/moved-out/pycache/` へ移しました。私用の置き場の指示は、普通の `__pycache__` までは覆いません。
- **不足:** 型検査(full の段)は走らせていません。git の履歴は読んでいません。defk の「残る限界」が呼んだ値まで含むつもりで書かれたのかは、註の文から読んだだけで分かりません。
- commit も push もしていません。作業の材料とログは `/tmp/ki-b7c4-blindb/` にあります。

`git -C /home/ubuntu/.worktrees/doeff-wt-blind-b-ki-b7c4 status --short` の出力:
```
(出力なし — 変更なし)
```
