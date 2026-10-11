# 設計検証 lt-4Z5WD2EKQ4ARF9CPCHPTQG00TF — 盲検の前に固めた主張

この file は盲検 A・B を起こす前に書いて commit した。盲検の後に足す事は design.md に書き、この file は変えない。

## 0. 完了の範囲

実装完了まで。実装は doeff の main に在る(c9e1622b589dcde007b42baaa5817ea9332b61e7・L2120・card ki-2d5b129f2ff2)。
設計検証は実装の後に行う(依頼 lt-4Z5WD2EKQ4ARF9CPCHPTQG00TF の class は investigate で、終わりの検査が設計検証を求めた)。
この順は記録に残し、主張は今の実装に対して固める。

## 1. 固定した要件

- R1(依頼の所見): 例の検は走る例の標準出力を `examples/expected/<stem>.txt` と比べるが、表 OUTPUT_NOT_COMPARED の 4 本(ex04_debug・ex04_debug2・ex05_parallel_timing・ex07_proboscis_ema_rewrite)は出力を比べず、件の中の壊れ(件を failed と印字して終了コード 0)が見えない。
- R2(card ki-2d5b129f2ff2 の受入 c0b82e474): OUTPUT_NOT_COMPARED の 4 本でも、件の中の失敗(failed の印字)があれば例の検が赤になり、それを確かめる失敗ケースが doeff の main に在る。
- 前提: Traverse の handler(sequential・parallel)は件の中の例外を件の失敗として記録して先へ進むので、件の中の壊れは終了コードに出ない。

## 2. 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 口 |
| --- | --- | --- | --- | --- |
| macros | doeff-hy の macro(`packages/doeff-hy/src/doeff_hy/macros.hy`) | 在る macro の名・展開の規則 | 展開の中身(gensym の名・Hy の無名関数の番号) | `(require doeff-hy.macros [名 …])` |
| traverse-lib | doeff-traverse の effect と handler・doeff-core-effects の handler | 件の失敗の方針・履歴の形 | handler の中の frame・行番号 | effect の class・handler の関数 |
| examples | `packages/doeff-traverse/examples/*.hy` と `examples/expected/<stem>.txt` | 例ごとの data・handler の組・印字。**期待の出力のどこが走るたびに変わるか(印 `…` を置く所)** | なし(読み物) | `python -m hy <file>` が 0 で終わり、標準出力が期待の出力に合う |
| example-check | `packages/doeff-traverse/tests/test_examples_run.py` | 例の母集団・表 BROKEN・OUTPUT_NOT_COMPARED・CASE_FAILURE_MUTANTS・印 `ANY = "…"` の意味(どの文字列〔改行を含む・空も〕にも合う)・走らせ方・合否 | 下請けの関数(`_run_hy`・`_matches`・`_program_runners`・`_forms` …) | pytest の id(下の §4) |
| daily-gate | 日次の全体検証の段 packages(`make test-packages`) | いつ・どの母集団を走らせるか | 段の組み立て | `pytest packages/<p>/tests` |

隠す知識の要: example-check は「例のどこが変わるか」も「traverse-lib の失敗の印字の形(failed・FAILED・skipped の語)」も知らない。変わる所は examples が期待の出力の中の `…` で示し、example-check は `…` の意味だけを知る。

## 3. 変更シナリオと主張

| id | 軸 | 変える要求 | 主張 | 予想の波及 |
| --- | --- | --- | --- | --- |
| S1 | effects | macros・traverse-lib・例の backend のどれかが変わり、ex05 か ex07 の件の中で例外が起きる(盲検 A の形 — 例は終了コード 0 のまま件を失敗として印字する) | example-check を 1 行も変えずに、`test_example_runs[<例>]` が赤になる。期待の出力も変えない | 壊した側の module だけ。example-check・examples の期待の出力は変えない |
| S2 | storage | 例を走らせる checkout の置き場が変わる(日次の作業木・別の path の木)か、doeff-core-effects・traverse-lib の中の frame・行番号が変わる | ex07 は期待の出力を変えずに緑のまま(traceback の枠は `…`)。example-check も変えない | 何も変えない |
| S3 | simulation | 走るたびに印字が変わる(実時間・番地・path)新しい例を足す | 変えるのは examples(例 1 file + 期待の出力 1 file — 変わる所は `…`)と、example-check の表 CASE_FAILURE_MUTANTS の 1 行(件の中だけで失敗する変異)だけ。件を走らせない debug 用の例なら、期待の出力の代わりに OUTPUT_NOT_COMPARED の 1 行。example-check の判じの code は変えない | examples + example-check の表の 1 行 |
| — | hardware | 除外 | 例は CPU だけを使う。機体の速さは ex05 の秒の印字を変えるだけで、`…` が覆う(S2・S3 と同じ形)。子の締切は既存の `deadline_scale_factor` の持ち分で、この変更は触らない | — |
| — | distribution | 除外 | 検は同じ機体の上で子 process を起こすだけ。機体をまたがない | — |
| — | concurrency | 除外 | 日次は直列。変異の写しは pytest の検ごとの `tmp_path` にだけ書くので、`-n` で並べても互いに触らない。ex05・ex07 の parallel の handler の結果は件の番号の順に印字される | — |

前提: 例の印字のうち、件の結果(合計・成功の数・件ごとの OK/FAILED・履歴)が載る行は `…` で伏せない。伏せると S1 の主張は崩れる — これを §4 の変異の検が止める、と主張する。

## 4. 強制方法

| 守る責務 | 強制方法と理由 | 在り処 | 実行の経路 | 限界 |
| --- | --- | --- | --- | --- |
| 件を走らせる例は標準出力を比べる | `test_example_runs[<例>]`: 終了コード 0 と、`_matches(期待, 標準出力)`(`…` を `.*?` に、他を `re.escape` にした fullmatch・DOTALL) | test_examples_run.py | 日次の段 packages → `pytest packages/doeff-traverse/tests` | マージの前には走らない |
| `…` が件の結果まで伏せていない | `test_case_failure_shows_in_compared_output[<例>]`: 表 CASE_FAILURE_MUTANTS の変異(元の文 → 先の文)を例の写し(`tmp_path`)に当て、写しが終了コード 0 で終わり、出力が期待に合わない事 | 同上 | 同上 | 変異 1 つが見る件の結果だけを確かめる(他の行の伏せすぎは、その行を変える変異が無いと見えない) |
| `…` を置いた例には変異が在る | `test_tables_name_examples_as_they_are`: 期待の出力に `…` が在る比べる例の集合 = CASE_FAILURE_MUTANTS の鍵の集合 | 同上 | 同上 | 変異が件の中で起きている事は、写しの終了コード 0 と出力の不一致でしか見ない |
| 比べない例は件を走らせない | `test_not_compared_example_runs_no_program[<例>]`: Hy の reader で読んだ form のどこにも `run`・`async_run` の Symbol が無い。拾う事は inline の `test_runner_check_sees_nested_forms` で確かめる | 同上 | 同上 | 名だけを見る静的な確かめ。別の名で program を走らせる口は見えない |
| 期待の出力に checkout の path を置かない | `test_tables_name_examples_as_they_are`(既存・文言だけ変更) | 同上 | 同上 | 置いた木の path だけを探す |

## 5. 検証の計画

- S1 正: 変えていない木で `test_examples_run.py` が全部緑。負: ex05・ex07 に件の中だけの失敗を入れた木で `test_example_runs[ex05]`・`[ex07]` が赤。
- S2 正: 別の path の木で同じ検が緑。負: `…` を件の結果まで広げると、`test_case_failure_shows_in_compared_output` が赤。
- S3 正: 変わる印字の新しい例を、例 + 期待の出力 + 変異の 1 行で足すと緑。負: 変異の行が無いと表の検が赤。件を走らせる例を OUTPUT_NOT_COMPARED に置くと `test_not_compared_example_runs_no_program` が赤。
- 盲検 A・B の失敗例を、同じ検で再現する。
