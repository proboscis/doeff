あなたは静的検査を通過する責務違反の失敗例を生成してください。
合理的な機能追加・修正として実装されそうで、提示した検査をそのまま実行しても通るのに、
知識・判断・状態・effectsの所有者や公開契約を侵す実装を1つ具体化してください。
一般的な設計レビューや承認判定は求めていません。

検査の無効化、baselineの改変、対象除外、as any、ignoreやlint抑制、根拠のないcast、
動的importによる回避は使わないでください。小さい既存例に対する抜け道だけでなく、
通常の実装者が機能を実現しようとして書きそうなコードで示してください。

返答には次を含めてください。
1. 現実的な機能要求と、提案する具体的なコードまたは最小差分。
2. 本来の責務所有者と、違反する契約。何の判断・知識がどこへ移るか。
3. 提示された各検査がそのコードを拒否しない理由。未提示の検査は推測しないこと。
4. 同じ設定で検査通過と責務違反の両方を確かめる最小の入力・手順・期待結果。
5. 未確認の前提、不足する情報。推測と実測を明確に区別すること。

共有ソースを変更せず、全数検査や他会話への連絡を行わず、日本語で返してください。
実行していない検査を通ったと断定しないでください。失敗例が見つからない場合は探索した範囲と
不足を述べ、合格・安全とは結論しないでください。
(この作業木の中の変更は許しますが、終わりに元へ戻し、git status --short を返答の最後に書いてください。)


---

## 作業木

あなたの作業木は /home/ubuntu/.worktrees/doeff-wt-blind-b-ki-b7c4 です(commit 91b9b8bbc を detached で切って同期済み)。この作業木の git の履歴(git log・git show・blame・reflog)は読みません — 評価するのは、この commit の file の今の中身です。\n

# 失敗例探しの共通の材料

対象: doeff repo の commit 91b9b8bbc66d082d8bd357558e312690d1fef7aa(作業木は `/home/ubuntu/.worktrees/doeff-wt-blind-b-ki-b7c4` — この commit を detached で切り、`make sync` 済み)。

## 1. 対象と使われ方

`packages/doeff-traverse/examples/ex*.hy` は、利用者に doeff-traverse の使い方を見せる読み物の例で、`python -m hy <例>` で走る。例は
doeff-hy の macro(`defk`・`defhandler`・`fnk`・`<-`・`traverse`・`for/do` など)を `(require doeff-hy.macros [名 …])` で使い、
`expected/<例の stem>.txt` が期待の標準出力。macro の持ち主(doeff-hy の作者)は、macro の展開を、**run の後の答えを変えずに**作り替える
ことがある(展開の形・隠す名・展開が作る値の class など)。そのたびに例や例の検を書き換えさせてはならない。
例の検は `packages/doeff-traverse/tests/test_examples_run.py`(pytest)。マージの前に、変えた file に対応する検として選ばれて走る。

## 2. 固定した要件

- R1: 展開を文字列で写さず、doeff-hy の macro が作った値の形(class 名・型・属性)を実行時に印字する例を examples/ に足すと、
  例の検が、その例を名指して赤になる。
- R2: 答えを変えない doeff-hy の macro の変更では、今の examples/ の例も、例の検(test_examples_run.py の検の全部)も、変わらず緑のまま。
- R3: 今の例 9 本と期待の出力を書き換えずに、例の検は緑のまま。
- R4: 例の検が例を走らせる時に macro の展開を変える仕組み(「変異」)は、例が import する Hy の lib の展開と、機体で共有する bytecode の保存先
  (環境変数 DOEFF_HY_CODE_STORE)の中身を変えない。
- R5: 例の検の file が新しい macro や新しい種類の例に出会った時は、検が名指して表への行を求める(黙って緑にならない)。

## 3. 責務

| id | 持ち分 | 隠す物 | 口 |
|---|---|---|---|
| macros | doeff-hy の macro の定義(packages/doeff-hy/src/doeff_hy/macros.hy ほか)— 名と展開の規則 | 展開の中身(隠す名・gensym・形・作る値の class) | `(require doeff-hy.macros [名 …])`。traverse・for/do は呼び手に別名の import(`_doeff_traverse_Traverse` など)を求める |
| hidden-names | `doeff_hy.static_stub.hidden_name` — 公開の面に出さない名の 1 か所の定義 | 接頭辞の表 | 関数 |
| examples | examples/*.hy と expected/<stem>.txt | なし(読み物) | `python -m hy <例>` が 0 で終わり、標準出力が期待の出力に合う |
| example-check | tests/test_examples_run.py — 母集団・表(BROKEN・NO_CASES・UNMARKED_EXPANSIONS・MUTATED_MACROS・UNMUTATED_MACROS・MARKS・UNPLACEABLE)・比べ方・macro の持ち主の検・展開の形の印の検・変異の検 | 子の走らせ方・比べ方・使い所の読み方 | pytest の検の名と断りの文 |
| mutation-entrance | tests/run_example_mutated_expansion.py — 子の入口: MUTATED_MACROS の macro の展開を、答えを変えない形(何もしない handler の下で走らせる Program)に変えてから例を走らせる | 変異の作り方・Hy の macro の呼び口の扱い | `python run_example_mutated_expansion.py <例の path> <macro の名>…` |
| failing-child | tests/run_example_failing_a_case.py — 件の中で失敗させて走らせる入口 | 失敗の起こし方 | 標準エラーの実行の印 |
| module-contract | .agents/code-quality.d/doeff-traverse-tests.json — 検の file の module 契約(許す依存) | なし | code-quality の dependency-not-allowed・dynamic-boundary |

## 4. 公開契約(主張)

- C1: 期待の出力に「展開の形の印」(Hy・doeff-hy の隠す名・展開の `__import__`)を置かない。
- C2: 例が使う doeff-hy の macro の使い所を 1 つずつ文脈の外で展開し、展開できた物はどれも印を持つ(印の無い macro は UNMARKED_EXPANSIONS に理由つき)。
- C3: 例は、`MUTATED_MACROS`(traverse・for/do)の展開を変異させた下でも、期待の出力のまま(`test_example_output_survives_expansion_mutation`)。
  変異した値は元の値の class に依らず必ず WithHandler になるので、値の class 名・型・属性を印字する例は変異の下で出力が変わる(または落ちる)。
- C4: 変異は、例の file を compile する時の展開だけに当たる。例が import する lib の展開は変えない。
- C5: 例が使う macro は、MUTATED_MACROS か UNMUTATED_MACROS(理由つき)に置く(`test_example_macros_are_mutated_or_named`)。
- C6: examples/ の下の file は、直下の例(.hy)と expected/ の期待の出力(.txt)だけ(`test_examples_dir_holds_only_examples_and_their_expected_outputs`)。
- C7: 変異は Hy の macro の呼び口(第 1 引数が `_hy_compiler` の macro にだけ compiler を渡す)を保つ。

## 5. 強制方法と実行経路

| 守る事 | 強制方法 | 実装の在りか |
|---|---|---|
| C1・C2 | 契約の検(pytest) | test_examples_run.py の `UNPLACEABLE`・`_shows_expansion`・`test_expected_output_places_nothing_unplaceable`・`test_example_expansions_show_their_marks` |
| C3 | 契約の検(例ごとに変異の子を走らせて出力を比べる) | `test_example_output_survives_expansion_mutation`・入口 run_example_mutated_expansion.py |
| C4 | 契約の検(tmp の lib を使う source) | `test_expansion_mutation_spares_the_modules_an_example_imports` |
| C5 | 契約の検 | `test_example_macros_are_mutated_or_named` |
| C6 | 契約の検 | `test_examples_dir_holds_only_examples_and_their_expected_outputs` |
| 検の file の依存 | module 契約 | .agents/code-quality.d/doeff-traverse-tests.json |

実行経路: マージの前に `[[tool.doeff.contract-tests]]`(pyproject.toml)の prefix(`packages/doeff-traverse/`・`packages/doeff-hy/src/doeff_hy/` など)で、
この検が選ばれる。

## 6. 将来の変更シナリオと、事前の予測

| シナリオ | 変わる所(予測) | 変わらない所(予測) |
|---|---|---|
| S1 doeff-hy が答えを変えずに traverse・for/do の展開を作り替える(形・隠す名・import の仕方・macro の引数の宣言など) | macros だけ | examples・expected・example-check・mutation-entrance |
| S2 新しい例を 1 本足す(既存の macro だけを使う) | examples と expected | example-check の表・mutation-entrance |
| S3 例が doeff-hy の macro を初めて使う | examples・expected と example-check の表(MUTATED_MACROS か UNMUTATED_MACROS の行) | macros・mutation-entrance |
| S4 Hy / doeff-hy の macro の呼び口や表の置き場が変わる | mutation-entrance(と、表の置き場を読む箇所) | examples・expected・example-check の比べ方 |
| S5 隠す名の決まりが変わる(hidden-names) | hidden-names | example-check・examples |

主張の前提: 「答えを変えない」変更とは、例の `run` の後の答え(印字される値)が同じになる変更。macro が作る値を 1 つの Program で包む・
import の形を替える・gensym の名を替える等を含む。未知の将来の全部を予測するとは主張しない。

## 7. 読む物と走らせ方(`/home/ubuntu/.worktrees/doeff-wt-blind-b-ki-b7c4` で)

- 実コード: packages/doeff-traverse/tests/test_examples_run.py・run_example_mutated_expansion.py・run_example_failing_a_case.py、
  packages/doeff-traverse/examples/(例と expected/)、packages/doeff-hy/src/doeff_hy/macros.hy、
  .agents/code-quality.d/doeff-traverse-tests.json。
- 検の実行: `cd /home/ubuntu/.worktrees/doeff-wt-blind-b-ki-b7c4 && uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q --tb=short -rf`(約 30 秒。機体の検の枠が埋まっていると待つ。
  timeout は 600 秒まで)。
- 変更箇所の品質検査(module 契約・import の境界など。型検査は含まない): 変更を `git add -N <file>` などで名乗らせた上で
  `cd /home/ubuntu/.worktrees/doeff-wt-blind-b-ki-b7c4 && sh ~/dotfiles/agent/venv-run.sh ~/dotfiles/agentcli code-quality --repo "$PWD" --scope changed --base $(git merge-base HEAD origin/main)`
  (`origin/main` が無ければ HEAD~3 など基点を自分で選ぶ)。commit 時の semgrep の hook は、commit を作らないので走らせなくてよい。
- 機体の制約: 作業木の中の削除(`rm -r` など)と `git checkout`・`git reset` は hook に止められる。変更は file を書き戻して戻す(patch を作っておく)。
  作業木の外の `~/repos` など共有の物は変更しない。ネットワークに送らない。bytecode の保存先は、実験で macros.hy を書き換える時は
  自分用の空の dir(`DOEFF_HY_CODE_STORE=<自分の dir>`)を指す(共有の保存先を汚さない)。
