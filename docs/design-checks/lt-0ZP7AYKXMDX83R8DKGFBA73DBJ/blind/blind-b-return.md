## 結論
検査を通るのに責務を侵す実装を 1 つ見つけ、検査の通過と責務の違反の両方を実測で確かめました。
新しい例 ex10 が「for/do を評価した値の class 名」を印字します。展開を文字列で写していないので、展開の形の印(隠す名・`__import__`)を持ちません。そのため例の検は全部緑で、code-quality も通ります。ところが答えを変えない macro の変更を入れると、ex10 だけが赤になります。

## 1. 機能要求とコード
**要求**: ex04 の頭の註「Program is DATA」「run() is the only place where execution happens」を、走る例で見せる。for/do が値を組み立てるだけである事を見せ、run の後の答えを印字する。

**差分**: 2 file を足しただけです(作業木に intent-to-add で残してあり、patch は `/tmp/blindb-ki-fe7a/ex10.patch`)。
- `packages/doeff-traverse/examples/ex10_built_then_run.hy`
  - 書き方は既存の例と同じ: `(require doeff-hy.macros [<- for/do])`、ex09 と同じ import。
  - From が 1 つの `single` と、From を重ねた `pairs` を for/do で作り、run して `valid_values` を印字する。
  - 問題の 1 行:
    ```hy
    (print "built:" (. (type single) __name__) "/" (. (type pairs) __name__))
    ```
    値をそのまま印字すると `Traverse(<function <lambda> at 0x355ef99b880>, ..., label='double')` のように番地が入り、毎回変わります(実測)。だから書き手は自然に class 名へ逃げる、という筋です。
- `examples/expected/ex10_built_then_run.txt`(実際に走らせた出力):
  ```
  built: Traverse / Call
  single: [10, 20, 30]
  pairs: [20, 30, 30, 40]
  ```

## 2. 本来の持ち主と、侵す契約
- **持ち主**: for/do を評価した値が何になるかを決めるのは macros です。`macros.hy` の `_place-traverse`(1461〜1467 行、docstring 1441〜1457 行)が次のように選んでいます。
  - From が 1 つで一番外: Traverse の effect そのまま。
  - From を重ねた一番外: Program(`doeff_vm.doeff_vm.Call`)。

  これは展開の一番外の「形」で、責務の表では macros が隠す物です。
- **移る知識**: その選択が `examples/expected/ex10_built_then_run.txt` の 1 行目へ写ります。`Call` は doeff-vm の class 名でもあります(doeff-vm の契約は材料に無いので、ここは推測です)。
- **侵す契約**:
  - R2「答えを変えない macro の変更で例は変わらない」。
  - S1 の予想範囲「macros だけ」。
  - 検の docstring 30〜31 行の目的「展開の形も doeff-hy の隠す中身で、例が印字すると答えを変えない macro の変更で例が赤になる」。
- **C1 の字面は侵していません**。それがこの穴の形です。C1 は展開の形を「文字列の印」でしか定義していません。展開が作った値を実行時に覗く道は、印を持たないまま形を運びます。

## 3. 提示された各検査が拒まない理由
`test_examples_run.py` 全体は 65 passed / 1 skipped(実測)です。skip は BROKEN が空の時の parametrize です。

| 検 | 拒まない理由 |
|---|---|
| `test_example_runs[ex10]` | 0 で終わり、標準出力が期待と字のまま合う |
| `test_case_failure_shows_in_compared_output[ex10]` | 失敗させる子は `case-calls: 7` で、出力が `single: [20, 30]`・`pairs: [40]` に変わり期待と合わない |
| `test_example_runs_a_program[ex10]` | `program-runs: 2` |
| `test_example_uses_only_doeff_hy_macros[ex10]` | defmacro なし、require は doeff-hy だけ、macroexpand 系の呼び出しなし(`type` で値を見るだけ) |
| `test_example_expansions_show_their_marks[ex10]` | 緑(実測)。for/do の展開は `_doeff_traverse_Traverse`・`_doeff_do` を持つ(code を読んだ範囲)。中の `<-` の使い所も緑だが、どの印で通ったかは個別に見ていない |
| `test_expected_output_places_nothing_unplaceable` | 期待の出力に `_` で始まる語も `__import__` も path も File の行も無い |
| `test_expected_output_places_only_known_marks` | `…` を置いていない |
| `test_tables_name_examples_as_they_are` | 期待の出力が例と 1 対 1 で、NO_CASES は変えていない |
| その他(`test_printed_expansion_copy_is_named` など) | ex10 に依らず緑 |

**code-quality**:
- 実行した命令: `uv run --frozen --no-sync --project /home/ubuntu/repos/dotfiles/agentcli code-quality --repo <作業木> --scope changed --paths-file <ex10 の 2 file> --no-cache`。結果は `code-quality: passed`(fast の段)、candidate=0 でした。
- ex10 は module 契約 `doeff-traverse-examples` の paths に入り、既存の例と同じ module だけを import します。
- 1 回目は ex10 が追跡外のため検査の対象に入りませんでした。`git add -N` で名乗らせて取り直しています。
- dependency-not-allowed が fast の段の規則に入るかは、出力から直接は読めません。

## 4. 最小の再現手順と期待結果
作業木 `/home/ubuntu/.worktrees/doeff-wt-blind-b-ki-fe7a` で行います。ex10 は置いたままで、macros.hy は元に戻してあります(差分なし)。

1. **検査を通る事**: `uv run pytest packages/doeff-traverse/tests/test_examples_run.py`
   - 期待: 65 passed / 1 skipped(実測 29 秒)。
2. **責務の違反が出る事**: `git apply /tmp/blindb-ki-fe7a/macro_shape_change.patch`
   - 中身: 実行時の展開だけを変え、From 1 つの一番外も「同じ Traverse を実行する Program」で包む。型検査のための展開の枝には触れない。
   - `python -m hy ex10` は `built: Call / Call` を印字し、答えの 2 行は変わらない(実測)。
   - 検: `1 failed, 64 passed, 1 skipped`(実測)。落ちたのは `test_example_runs[ex10]` だけ(差分は `-built: Traverse / Call` / `+built: Call / Call`)。ex01〜ex09 と、展開の印の検は全部緑のまま。
   - 答えを変えない事の裏付けとして、for/do・traverse を使う検 6 file(`tests/test_for_do_runtime.py` など)も同じ変更の下で 138 passed(実測)。これは証明ではありません。
   - この変更は prefix `packages/doeff-hy/src/doeff_hy/` で例の検を選ぶので、macro の作者の変更で赤になります。直すには examples/expected を書き換えるしかなく、変更が macros の外へ波及します。
3. **戻す**: macros.hy を手で元に戻す(`git checkout --` は hook に止められます)。

## 5. 未確認の前提と、不足する情報
- **推測**: 「From 1 つの for/do が Traverse の effect に評価される事」が macros の公開契約かどうかは、材料に書かれていません。ex04 の註は traverse を「Traverse の effect に変える」と書いています。それが公開契約なら半分は正当で、`Call` の側だけが違反になります。
- **実測した範囲**: 答えを変えない事は、上の検の範囲でしか確かめていません。repo 全体、pyright、doeff-hy-check は走らせていません。
- **実行していない物**: code-quality の full の段(型検査)と日次の全体。land の列が同じ版の code-quality を使うかも確かめていません。
- **付随の所見 1(実測)**: ADR-DOE-HY-006 が module の直下に勧める `val` を使う例は、`test_example_expansions_show_their_marks` に `['10: val']` で断られます。`(val x v)` の 1 段の展開が、印の無い `(setv x v)` だからです。S2(a) の「表の変更は要らない」と食い違います。
- **付随の所見 2(code を読んだだけ、未実測)**: 例の source は、隠す別名 `_doeff_traverse_Traverse`・`_doeff-do` を自分で import しています。traverse・for/do の docstring の「Requires:」がそう求めているからです。そのため S1(a) の改名では、例も直す事になると読めます。
- **作業木の状態**: ex10 の 2 file が intent-to-add で残っています(`git reset` は hook が止めました)。log と patch は `/tmp/blindb-ki-fe7a/` に在ります。

WAIT: work — 頼み手(親の会話)の次の依頼
