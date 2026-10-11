# 設計検証 lt-C53PGBQD7WWPYAA1WWSW41BHDP — 盲検の前の主張(card ki-3497c5fd38df)

この file は盲検 A・B を起こす前に書き、commit で版を固定する。盲検の後に結論を書き換えない(後の結論は design.md)。

## 0. 範囲

- 依頼(class=investigate): doeff-traverse の debug 用の 2 本(ex04_debug・ex04_debug2)を examples/ に残すか外すかを決める。外す前に doeff の docs/adr と 4 つの repo(agora・agora-controllers・doeff・dotfiles)の origin/main で名を引き、使い手と当たる法が無い事を確かめる。
- 決め: 外す。doeff main 7edd471c6(land L2133・基 cefb92a17)で 2 本と、2 本だけのために在った表 OUTPUT_NOT_COMPARED とその付属の検を外した。
- この設計検証の範囲: **実装完了まで**。7edd471c6 は実装済み。この検証で足す強制(§3 の E1)は、この設計検証の branch で実装し、同じ land で main に入れる。
- 基の版: a2254d66c(7edd471c6 と、その後の d37342bcd を含む origin/main)。

## 1. 要件(固定)

- R1: 2 本を残すか外すかを決め、ADR と 4 つの repo で名を引いた結果を card に書く。
- R2: 外すなら doeff の main で 2 本と比べない表の行が無い。
- R3(この設計検証の主張): examples/ の file は、どれも Program を走らせて結果を印字する読み物で、標準出力を期待の出力と比べられる。macro の展開や関数の番地のような doeff-hy・Hy の中身だけを印字する debug 用の file は examples/ に置けない。

## 2. 責務(module)

| id | 持ち分 | 隠す物 | 口 |
|---|---|---|---|
| macros | doeff-hy の macro(macros.hy・outcome_forms.hy)— 在る macro の名と展開の規則 | 展開の中身(gensym の名・展開の形・Hy の無名関数の番号) | `(require doeff-hy.macros [名 …])` |
| traverse-lib | doeff-traverse の effect と handler(件の失敗の方針・件の履歴) | handler の frame・行番号 | effect の class・handler の関数 |
| examples | packages/doeff-traverse/examples/*.hy と expected/<stem>.txt — 例ごとの data・handler の組・印字、期待の出力の中で走るたびに変わる所(印 …) | なし(読み物) | `python -m hy <examples>/<file>` が 0 で終わり、標準出力が期待に合う |
| example-check | packages/doeff-traverse/tests/test_examples_run.py — 例の母集団(glob)・表 BROKEN と CASE_FAILURE_MUTANTS・印 ANY の意味・期待の出力に置かない物・件を走らせる口の名・合否 | 下請けの関数 | pytest の id |
| premerge-selection | 変更から走らせる検を選ぶ表(pyproject.toml の選びの表)と、その表の検(tests/test_contract_table_follows_packages.py) | 選び方の組み立て | 表の行(path の頭 → 検の file) |

## 3. 強制方法

| 守る事 | 強制 | 置き場 | 経路 | 限界 |
|---|---|---|---|---|
| E1: examples/ の file は Program を走らせる(R3) | 新しい検 `test_example_runs_a_program[<例>]`: 例の form(入れ子も)に、頭が件を走らせる口(`run`・`async_run`・`doeff.run` の形)の呼び出しが 1 つ以上在る。口の名は表 `_PROGRAM_RUNNERS` 1 か所。拾い方の正しさは inline の検 `test_runner_call_check_sees_nested_forms`(入れ子・`doeff.run`・import だけ・呼ばずに名を書くだけ)で確かめる | test_examples_run.py | マージの前の選び(examples/・macros・outcomes の変更 → この file)と日次の段 packages | 口を別名で呼ぶ例(`run :as r`)は断る(本名で呼べと名指す)。口を呼んだ上で中身も印字する file は通る — その時は出力を比べる側(E2・E3)が受け持つ |
| E2: 走る例は全部、標準出力を期待と比べる(比べない表は無い) | `test_example_runs[<例>]` が期待の出力の在りかと一致を見る。期待の出力は走る例ちょうどに在る(`test_tables_name_examples_as_they_are`) | 同上(既存) | 同上 | 一致の判定は印 … の外の文字だけ |
| E3: 期待の出力に Hy の付ける名を置かず、… を置いた例には件の中で失敗する変異がある | `test_tables_name_examples_as_they_are`・`test_case_failure_shows_in_compared_output` | 同上(既存) | 同上 | 変異が変える行の外の伏せすぎは見えない |
| E4: 消した 2 本の名を使い手が引かない | 4 つの repo の origin/main と doeff の ADR で名を引いた(card の発言) | — | 1 回の確かめ | 静的な強制ではない(消した名を新しく書く使い手は、その時の検が落ちる) |

型で表せる所: 無い(例は Hy の script で、口は file の glob と子 process)。import の向き: example-check は examples を import せず子 process で走らせる。

## 4. 変更シナリオと事前の主張

### S1(axis effects・適用): macros の展開の中身が変わる(答えは変わらない)

- 変える要求: traverse・`<-` の展開が使う gensym の名や展開の形を変える(例: outcome_forms.hy の `(hy.gensym "bound")` の名を変える)。
- 主張: 変えるのは macros だけ。examples・期待の出力・example-check は 1 行も変えず、例の検は緑のまま。
- 理由: 7edd471c6 で、展開の中身を印字する ex04_debug と、展開の形を手で写した ex04_debug2 が消えた。ex04_debug2 の写しは既に今の展開と違っていた(展開の知識が examples に漏れ、比べられないまま古びていた)。今の examples に展開の知識を持つ file は無く、そういう file を戻すと E1・E3 が断る。
- 前提: 例の答え(件の結果)が変わらない変更である事。答えが変わる変更は、期待の出力を書き直す正当な契約の変更。

### S2(axis simulation・適用): 例を 1 本足す

- 変える要求: 新しい読み物の例(Program を走らせ、件の結果を印字する)を examples/ に足す。走るたびに変わる所が在れば … と変異を置く。
- 主張: 変えるのは examples(例と期待の出力)だけ(… を置く例は CASE_FAILURE_MUTANTS の 1 行も)。example-check の比べない表のような除外の行は要らないし、作れない。
- 主張(反対側): Program を走らせず、展開や関数の repr を印字するだけの debug 用の file を examples/ に足すと、期待の出力の中身に依らず E1 が赤にする。
- 前提: 例は README の一覧に載る読み物である事(README の一覧は強制しない)。

### 適用しない軸

- hardware: 例は機体の資源に依らない script で、この変更は機体の扱いに触れない。
- storage: 例と期待の出力は repo の file で、保存の方法を変える要求が無い。
- distribution: 例の検は 1 つの機体の pytest の子 process で閉じる。別の木(日次の作業木)で走る事は既存の path の検が受け持ち、この変更では変えない。
- concurrency: 例は 1 本ずつ子 process で走る。ex05 の並列は例の中身で、この変更では変えない。

## 5. 予定の確かめ(正常例・失敗例)

- c0(S1・S2 正常): 実装後の基の版で例の検が全部緑。
- c1(S1 正常): outcome_forms.hy の gensym の名を変えた木で、examples・期待・検を変えずに例の検が緑。
- c2(S1 失敗): ex04_debug を戻し、展開を写した期待の出力(Hy の名を … にし、変異も足す)を置くと赤 — E1 が名指す。
- c3(S2 正常): 新しい読み物の例を 1 本(期待の出力つき)足すと、検の表を変えずに緑。
- c4(S2 失敗): 関数の repr を印字する debug 用の file に、どの出力にも合う期待の出力を置いても赤 — E1 が名指す。
- c5(E1 の拾い方): inline の検が入れ子・`doeff.run` を拾い、import だけ・呼ばない名を拾わない。
