# 設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP — doeff-traverse の examples/ から debug 用の 2 本を外す(card ki-3497c5fd38df)

## この文書の目的

読み終えた人が次の 3 つを言えるようにする。

1. ex04_debug・ex04_debug2 を外した決めと、その根拠(名を引いた範囲)。
2. 「examples/ の file は Program を走らせる読み物で、macro の展開の形を持たない」を、どの検がどう守るか。
3. 盲検 A・B が崩した主張と、その直しと、直した後もまだ守れていない所。

## 全体の図(今 ⇒ 目標)

```
今(基 cefb92a17)                                   目標(この設計検証の後)
examples/                                            examples/
  ex01〜ex09 の読み物 10 本 ── 出力を比べる            ex01〜ex09 の読み物 10 本 ── 出力を比べる
  ex04_debug(展開を印字)─┐                           (debug 用の file は置けない)
  ex04_debug2(展開を手写し)┴─ 表 OUTPUT_NOT_COMPARED
                              で比べずに通す
tests/test_examples_run.py(example-check)          tests/test_examples_run.py(example-check)
  ・比べない表・「件を走らせない」の確かめ              ・test_example_runs_a_program: doeff の口を呼ぶ
                                                       ・test_example_uses_only_doeff_hy_macros:
                                                         macro の定義・外からの require・展開の引き出しを断る
doeff-hy の macros.hy・outcome_forms.hy(macros)    macros — 展開の形を変えても examples・検は変わらない
```

## 0. 範囲

- 依頼 lt-C53PGBQD7WWPYAA1WWSW41BHDP(class=investigate)。範囲は**実装完了まで**。
- 実装済み: doeff main 7edd471c6(land L2133・基 cefb92a17)で 2 本・表 OUTPUT_NOT_COMPARED・その付属の検を外した。
- この設計検証で足した物(branch wt/ki-3497-design-check・基 a2254d66c):
  - cf27cee98: E1 `test_example_runs_a_program`
  - b47af0f0f: 盲検への直し F1・F2
  - ddbd4cd66: 断りの文
- 盲検の前の主張は design-before-blind.md(commit dd5eddeeb で固定)。この文書は盲検の後の結論で、前の主張は書き換えていない。

## 1. 要件

- R1: 2 本を残すか外すかを決め、ADR と 4 つの repo で名を引いた結果を card に書く。
- R2: 外すなら doeff の main で 2 本と、比べない表の行が無い。
- R3: examples/ の file は Program を走らせて結果を印字する読み物で、標準出力を期待の出力と比べられる。macro の展開や関数の番地のような doeff-hy・Hy の中身を印字する debug 用の file は置けない。

決め(R1): 外す。名を引いた結果(2026-10-12):

| 引いた所 | 当たり |
|---|---|
| doeff origin/main cefb92a17 の docs/adr(defadr 95 件)・specs の ADR | 0 件 |
| doeff の test の表 | packages/doeff-traverse/tests/test_examples_run.py の 2 行 |
| doeff の過去の設計検証の記録 | docs/design-checks の 4 件 |
| doeff の README | 例の一覧(ex01〜ex09)に載っていない |
| agora origin/main 3efd9ab69 | 0 件 |
| agora-controllers origin/main 802a13959 | 0 件 |
| dotfiles origin/master ae0bd00ef | 0 件 |

外す訳: 2 本は doeff-traverse の最初の commit(47a1f4528・2026-04-01)からある macro づくりの debug の残り物。ex04_debug2 が「macro が作る形」として手で写した形は、今の展開と既に違っていた。

## 2. 責務(module)

| id | 持ち分 | 隠す物 | 口 |
|---|---|---|---|
| macros | doeff-hy の macro(macros.hy・outcome_forms.hy)— 在る macro の名と展開の規則 | 展開の形・gensym の名・Hy の無名関数の番号 | `(require doeff-hy.macros [名 …])` と、展開が使い手の module から引く名(`_doeff_do`・`_doeff_traverse_Traverse`・`_doeff_traverse_Skip` — macro の docstring の Requires)※盲検 A の付記で口に書き足した |
| traverse-lib | doeff-traverse の effect と handler(件の失敗の方針・件の履歴) | handler の frame・行番号 | effect の class・handler の関数 |
| examples | examples/*.hy と expected/<stem>.txt | なし(読み物) | `python -m hy <examples>/<file>` が 0 で終わり、標準出力が期待に合う |
| example-check | tests/test_examples_run.py — 例の母集団・表 BROKEN と CASE_FAILURE_MUTANTS・印 ANY・件を走らせる口の名(`_RUNNER_MODULE`・`_PROGRAM_RUNNERS`)・例が呼ばない展開の関数(`_MACRO_EXPANDERS`)・合否 | 下請けの関数 | pytest の id |
| premerge-selection | pyproject.toml の `[[tool.doeff.contract-tests]]` の表と、その表の検 tests/test_contract_table_follows_packages.py | 選び方の組み立て | 表の行(path の頭 → 検の file) |

## 3. 強制方法と実装の在りか

| 守る事 | 強制(実装) | 経路 | 限界 |
|---|---|---|---|
| E1: examples/ の file は doeff の Program を走らせる | `test_example_runs_a_program[<例>]` — `_program_calls` は、doeff から import した run・async_run(別名も)の呼び出しか、`(doeff.run …)` を数える。入れ子の form は見て、quote・quasiquote の中は見ない。拾い方は inline の検 `test_runner_call_check_sees_nested_forms` が確かめる | マージの前の選び(表の行 `packages/doeff-traverse/`・`packages/doeff-hy/src/doeff_hy/`・`doeff_core_effects/outcomes.py` → この file)と日次の段 packages | doeff の口を 1 回呼んだうえで別の物を印字する file は通る(その印字は E2・E3・F1 が受け持つ)。`(import doeff [run])` の後に同じ名の `defn run` を定義し直す形は見分けない |
| E2: 走る例は全部、標準出力を期待の出力と比べる | `test_example_runs[<例>]`・`test_tables_name_examples_as_they_are`(比べない表は無い) | 同上 | 比べるのは印 … の外の文字だけ |
| E3: 期待の出力に Hy の付ける名を置かず、… を置いた例には件の中で失敗する変異がある | `test_tables_name_examples_as_they_are`・`test_case_failure_shows_in_compared_output` | 同上 | 変異が変える行の外の伏せすぎは見えない |
| F1(盲検の後): 例は macro の展開を引き出さない | `test_example_uses_only_doeff_hy_macros[<例>]` の `_form_macro_violations` が、`hy.macroexpand`・`macroexpand-1`・`macroexpand-all` の呼び出しを違反に数える(macro の定義・doeff-hy の外からの require と同じ検)。inline の検 `test_macro_check_sees_nested_forms` が拾い方を確かめる | 同上 | 展開を引き出さずに展開の形を手で書いて印字する file(文字列の写し)は見分けない |
| E4: 消した 2 本の名を使い手が引かない | 4 つの repo の main と doeff の ADR で名を引いた(§1) | 1 回の確かめ | 静的な強制ではない |

型で表せる所: 無い(例は Hy の script で、検は file の glob と子 process で例を見る)。import の向き: example-check は examples を import せず、子 process で走らせる。

## 4. 変更シナリオ — 盲検の前の主張と実測

### S1(axis effects): macros の展開の中身が変わる(例の答えは変わらない)

- 盲検の前の主張: 変えるのは macros だけ。examples・期待の出力・example-check は 1 行も変えず、例の検は緑のまま。展開を印字したり手で写したりする file を examples/ に戻すと、E1・E3 が断る。
- 前提: 例の答えが変わらない。展開が使い手の module から引く名(Requires)は変えない。Requires の名を変えるのは、使い手全部の公開の契約の変更で、S1 の外。
- 盲検 A・B の実測(再現 r1・r2): 主張は成り立たなかった。Program を走らせる例に `hy.repr (hy.macroexpand …)` の印字を足し、期待の出力も更新すると、E1・E3 を含む検を全部通った(r1: 39 passed)。
  - 盲検 B は ex04_traverse_macro に節を足した。
  - 盲検 A は ex10 を新しく足した。
  - そこへ答えを変えない macro の変更(実行時の traverse の展開から余分な `(do …)` を外す)を当てると、その 2 本だけが赤になり、他の 10 本は緑のままだった(r2: 2 failed・37 passed)。
  - つまり、隠すはずの展開の形が examples の期待の出力に漏れた。主張の 2 文目(E1・E3 が断る)は、Program を走らせない file にしか当たらなかった。
- 直し F1: 例は macro の展開を引き出さない。`hy.macroexpand` などの呼び出しを、macro の持ち主の検で断る。
- 直した後(c1・c2):
  - c1: 同じ macro の変更と gensym の名の変更を当てても、examples と検を変えずに緑(36 passed)。
  - c2: 盲検 A・B の 2 本と消した ex04_debug は赤になる。
    - ex04_traverse_macro・ex10 は `macroexpand の呼び出し` で断られる。
    - ex04_debug は口を呼ばない事・期待の出力が無い事・展開の引き出しで断られる。
- 盲検 A の付記(r4): ex09 から `Skip :as _doeff_traverse_Skip` の import を消すと、件が `name '_doeff_traverse_Skip' is not defined` で失敗として印字され、終了コードは 0 だった。これは期待の出力と合わないので、例の検 E2 が赤にする。
  - 展開が使い手の module から引く名は macros の公開の契約(docstring の Requires)で、隠す中身の漏れではない。
  - そのため §2 の macros の口に書き足し、S1 の前提に「Requires の名は変えない」を書いた。直す code は無い。

### S2(axis simulation): 例を 1 本足す

- 盲検の前の主張: 変えるのは examples(例と期待の出力)だけ。… を置く例は CASE_FAILURE_MUTANTS の 1 行も足す。Program を走らせない debug 用の file は、期待の出力の中身に依らず E1 が赤にする。
- 盲検 B の実測(再現 r3): 主張の後半は成り立たなかった。E1 の拾い方は名だけで口を数えたので、`(subprocess.run …)`・`(asyncio.run …)`・例の中で定義した `(defn run …) (run)` も口に見えた。
- 直し F2: 口は、doeff から import した run・async_run(別名も)の呼び出しか、`(doeff.run …)` に限る。
- 直した後(c3・c4・c5):
  - c3: 新しい読み物の例(ex04_simple の写し)は、検の表を変えずに緑(39 passed)。
  - c4: asyncio.run と例の中で定義した run だけを呼ぶ debug 用の file は、標準出力どおりの期待の出力を置いても E1 で赤(1 failed)。盲検 B の probe の 3 つの形はどれも数えない。
  - c5: 検の file の写しで F1・F2 をそれぞれ外すと、対応する inline の検が赤になる。

### 適用しない軸

| 軸 | 適用しない訳 |
|---|---|
| hardware | 例は機体の資源に依らない script で、この変更は機体の扱いに触れない |
| storage | 例と期待の出力は repo の file で、保存の方法を変える要求が無い |
| distribution | 例の検は 1 つの機体の pytest の子 process で閉じる。別の木(日次の作業木)で走る事は既存の path の検が受け持ち、この変更では変えない |
| concurrency | 例は 1 本ずつ子 process で走る。ex05 の並列は例の中身で、この変更では変えない |

## 5. 確かめの一覧(版 ddbd4cd66・evidence/measure.sh.txt で取る)

| id | シナリオ | 種類 | 期待 | 観測 | 証拠 |
|---|---|---|---|---|---|
| c0 | S1・S2 | 正常 | 例の検が全部緑 | 36 passed・1 skipped(空の BROKEN) | evidence/checks/c0-all-green.txt |
| c1 | S1 | 正常 | 答えを変えない macro の変更 2 つで、examples・検を変えずに緑 | 36 passed・1 skipped | evidence/checks/c1-macro-change-examples-green.txt |
| c2 | S1 | 失敗 | 展開を印字する例と ex04_debug を戻すと、狙った理由で赤 | 5 failed(macroexpand の呼び出し ×3・口を呼ばない・期待の出力が無い) | evidence/checks/c2-expansion-printing-examples-red.txt |
| c3 | S2 | 正常 | 新しい読み物の例は、検の表を変えずに緑 | 39 passed・1 skipped | evidence/checks/c3-new-example-green.txt |
| c4 | S2 | 失敗 | doeff の外の run だけを呼ぶ debug 用の file は E1 で赤 | 1 failed(test_example_runs_a_program)・probe 3 形とも [] | evidence/checks/c4-debug-file-other-runs-red.txt |
| c5 | S1・S2 | 失敗 | F1・F2 を外した写しで、inline の検が赤 | f1・f2 とも 1 failed | evidence/checks/c5-inline-checks-guard-the-fix-red.txt |

盲検の失敗例の再現(直す前の版 cf27cee98): evidence/repro/r1〜r4。

## 6. 残る限界(強制していない事)

- doeff の口を 1 回呼んだうえで、関数の repr や Hy の中身を印字する file は E1 を通る。
  - 番地のように走るたびに変わる印字は、E2 で期待の出力と比べられる。そのため … と変異(E3)が要る。
  - `_hy_anon_`・`_hy_gensym_` の名は、E3 が期待の出力に置かせない。
  - 番地や名を含まない Hy の中身の印字は見分けない。
- 展開を引き出さずに、展開の形を文字列や quote した form で写して印字する file は見分けない。macros が変わっても赤にならず、写しが古びる。
- 例が `hy.repr` で quote した source を印字すると、その出力は Hy の書式に依る。Hy の版を上げる変更(uv.lock)は例の検を選ばないので、日次で初めて赤になる(盲検 B の推測・未確認)。
- 盲検 B が挙げた、ex04 の差分に表が選ぶ他の検(test_traverse_answer_types.py・tests/test_daily_test_population.py)は、この設計検証では走らせていない。
- 盲検の model は代用した(blind-meta.md)。effort xhigh が実際に効いたかは観測していない。

## 7. 未解決

無い。
