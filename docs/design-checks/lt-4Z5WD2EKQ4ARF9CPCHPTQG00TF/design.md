# 設計検証 lt-4Z5WD2EKQ4ARF9CPCHPTQG00TF — doeff-traverse の例の検が、出力を比べない例でも件の中の失敗を見る

## この文書の目的

読み終えた人が、次の 4 つを言えるようにする。

1. doeff-traverse の例の検が、件の中の失敗(終了コード 0 のまま failed を印字する壊れ)をどの検で赤にするか。
2. 変わる印字(実時間・path・traceback)を `…` で伏せても、件の結果が伏せられていない事を何が確かめるか。
3. 盲検 A・B が見つけた 2 つの抜けと、その直し。
4. まだ強制していない事。

## 0. 完了の範囲と版

| 何 | 版 | 状態 |
| --- | --- | --- |
| 実装(card ki-2d5b129f2ff2) | c9e1622b589dcde007b42baaa5817ea9332b61e7(L2120 で main に着地) | 実施済み |
| 盲検の前の主張と盲検の入力 | 3769c3e85 | 実施済み(実装の後に固めた — 下の注) |
| 盲検 A・B の直し | 7d56f9307b(branch `wt/design-check-lt-4z5w`) | 実施済み・この記録と一緒にマージの列へ載せる |
| 測り直し(c0〜c9) | 7d56f9307b | 実施済み |

注: 依頼 lt-4Z5WD2EKQ4ARF9CPCHPTQG00TF の class は investigate で、実装と着地を先に行い、終わりの検査が設計検証を求めた。主張は実装の後で、盲検の前に固めた([design-before-blind.md](design-before-blind.md)・sha256 3f2b0c4e…)。§1〜§3 の主張はその file のとおりで、この文書では書き換えない。

## 1. 要件

- R1: 例の検は走る例の標準出力を `examples/expected/<stem>.txt` と比べるが、表 OUTPUT_NOT_COMPARED の 4 本(ex04_debug・ex04_debug2・ex05_parallel_timing・ex07_proboscis_ema_rewrite)は比べず、件の中の壊れが見えない。
- R2(card の受入 c0b82e474): 4 本でも件の中の失敗があれば例の検が赤になり、それを確かめる失敗ケースが doeff の main に在る。

## 2. 責務

design-before-blind.md §2 の 5 つ(macros・traverse-lib・examples・example-check・daily-gate)のまま。盲検の後に、2 つの責務の持ち分を言い直した。

| 責務 | 言い直した持ち分 | 理由 |
| --- | --- | --- |
| example-check | **例を走らせる場所の形**: 例は examples/ の dir の中で、兄弟の file と並んで走る。変異の写しも examples/ の dir ごと作る | 盲検 A |
| example-check | **期待の出力に置かない物**: checkout の path(既存)と、Hy が compile の時に付ける名(`_hy_anon_`・`_hy_gensym_`) | 盲検 B |

## 3. 変更シナリオ(盲検の前の主張)

| id | 軸 | 主張(design-before-blind.md §3) | 実際 |
| --- | --- | --- | --- |
| S1 | effects | 件の中で例外が起きる変更は、example-check を変えずに `test_example_runs[<例>]` を赤にする | 成立(c1) |
| S2 | storage | checkout の置き場や lib の frame・行番号が変わっても、ex07 は期待の出力を変えずに緑 | 成立(c0・実装の時の別の path の木) |
| S3 | simulation | 走るたびに印字が変わる新しい例は、例 + 期待の出力 + 変異の表の 1 行で足せ、判じの code は変えない | 直す前は不成立(盲検 A — 兄弟の helper を使う例)。直した後は成立(c5・c6) |
| — | hardware・distribution・concurrency | 除外(理由は design-before-blind.md §3) | — |

盲検 B は S1 の前提「件の結果の行は `…` で伏せない」の外側を突いた: 件の結果ではない行に、macros の隠す中身(Hy の無名関数の番号)が固まる。

## 4. 強制方法(最後の版 7d56f9307b)

| 守る責務 | 強制方法と理由 | 在り処(`packages/doeff-traverse/tests/test_examples_run.py`) | 実行の経路 | 限界 |
| --- | --- | --- | --- | --- |
| 件を走らせる例は標準出力を比べる | 終了コード 0 と `_matches(期待, 標準出力)`。`…` は `.*?`(DOTALL)、他は `re.escape` の fullmatch | `test_example_runs[<例>]`・`_matches`・`ANY` | 日次の段 packages → `pytest packages/doeff-traverse/tests` | マージの前には走らない |
| `…` が件の結果まで伏せていない | 表 CASE_FAILURE_MUTANTS の変異を **examples/ の dir ごとの写し**に当て、写しが 0 で終わり、出力が期待に合わない事 | `test_case_failure_shows_in_compared_output[<例>]` | 同上 | 変異 1 つが変える行だけを見る。他の行の伏せすぎは、その行を変える変異が無いと見えない |
| `…` を置いた例には変異が在る | `…` の在る期待の出力の例の集合 = CASE_FAILURE_MUTANTS の鍵 | `test_tables_name_examples_as_they_are` | 同上 | 変異が件の中で起きている事は、写しの終了コード 0 と出力の不一致でしか見ない |
| 比べない例は件を走らせない | Hy の reader で読んだ form のどこにも `run`・`async_run` の Symbol が無い | `test_not_compared_example_runs_no_program[<例>]`・inline の `test_runner_check_sees_nested_forms` | 同上 | 名だけを見る。別の名で program を走らせる口は見えない |
| 期待の出力に checkout の path を置かない | 置いた木の path を含む期待の出力を断る | `test_tables_name_examples_as_they_are` | 同上 | 置いた木の path だけを探す |
| 期待の出力に Hy の付ける名を置かない(盲検 B で足した) | `_hy_anon_`・`_hy_gensym_` を含む期待の出力を断る。拾う事は inline の文で確かめる | `_HY_GENERATED_NAME`・`test_tables_name_examples_as_they_are`・`test_hy_name_check_sees_generated_names` | 同上 | Hy の名の形だけを見る。他の隠す中身(repr の番地・lib の内部の class の名 …)は見えない |

## 5. 盲検の結果・再現・直し

起動の記録は [blind/blind-meta.md](blind/blind-meta.md)(第 1 候補の gpt-6-astra は起こせず、第 2 候補の opus・effort xhigh で代用)。入力は [blind/blind-common.md](blind/blind-common.md)・[blind/blind-a-input.md](blind/blind-a-input.md)・[blind/blind-b-input.md](blind/blind-b-input.md)。

### 5.1 盲検 A — 例が兄弟の helper を使うと、変異の検が誤った案内で赤になる

- 返答: [blind/blind-a-return.md](blind/blind-a-return.md)
- 失敗例: 7 本の例に写された `with-stack` を `examples/common/stack.hy` に寄せ、ex05・ex06・ex07 で `(import common.stack [with-stack])` にする。例の出力は変わらない。
- 再現(c9e1622b5 の写しの木・[evidence/repro/a0-baseline.txt](evidence/repro/a0-baseline.txt)・[evidence/repro/a1-refactor-red.txt](evidence/repro/a1-refactor-red.txt)): 直す前は 31 passed。変更の後は 2 failed, 29 passed — `test_case_failure_shows_in_compared_output[ex05]`・`[ex07]` が、写しの import の行の `ModuleNotFoundError: No module named 'common'` で赤。案内は「件の中だけで失敗する変異を選ぶ」で、原因と合わない。`test_example_runs` は全部緑。**成立。**
- 原因: example-check の中で、例を走らせる場所の形が 2 通りあった(元の例は examples/ の中、変異の写しは `tmp_path` に 1 file だけ)。「例は 1 file で完結する」という書かれていない規則が、`…` を置いた例にだけ効いていた。
- 直し(7d56f9307b): 写しを `shutil.copytree(EXAMPLES, tmp_path / "examples")` で dir ごと作り、変異はその中の例に当てる。例は元と同じ形の場所で走る。失敗の案内を「変異を当てた写しが 0 以外で終わった」に改め、stderr を添える。
  - もう 1 つの直し方(examples の口に「1 file で完結する」を足し、兄弟の import を静的に断る)は採らなかった。共有の helper は例の持ち主の合理的な整理で、それを断る理由が example-check の都合しか無いため。
- 再検証: c6(A の変更の上で全部緑)・c7(A の変更の上で `…` を広げると、dir ごとの写しでも変異の検が赤)。

### 5.2 盲検 B — 失敗の場所を印字すると、Hy の無名関数の番号が期待の出力に固まる

- 返答: [blind/blind-b-return.md](blind/blind-b-return.md)
- 失敗例: ex07 に、失敗した件が例のどこで起きたかを 1 行で印字する(`raised in: _hy_anon_17:129 -> fetch_price:95 -> _hy_anon_28:174`)。例の file の frame だけを `__file__` で絞るので、path は入らない。
- 再現(c9e1622b5 の写しの木・[evidence/repro/b1-feature-green.txt](evidence/repro/b1-feature-green.txt)・[evidence/repro/b2-feature-macro-red.txt](evidence/repro/b2-feature-macro-red.txt)): 機能の差分だけで 31 passed。そこへ答えを変えない macro の変更(traverse の展開を 072dce983 の前の形に戻す)を足すと、`_hy_anon_17` → `_hy_anon_19`・`_hy_anon_28` → `_hy_anon_31` だけが変わって `test_example_runs[ex07]` が赤。**成立。**
- 原因: example-check は「どの印字が誰の隠す中身か」を知らない。checkout の path は断っていたが、Hy が compile の時に付ける名(番号は macro の展開と compile の順で決まる)は断っていなかった。macros の隠す知識の 2 つ目の置き場が、examples の期待の出力にできた。ex04_debug2 を比べない表に置いた理由と同じ種類の印字。
- 直し(7d56f9307b): 期待の出力に `_hy_anon_`・`_hy_gensym_` が在れば `test_tables_name_examples_as_they_are` を赤にし、「その所を `…` にする」と案内する。拾う事は inline の `test_hy_name_check_sees_generated_names` で確かめる(今の期待の出力には名が無く、本物の data だけでは中身の無い緑と区別できないため)。
- 再検証: c8(B の差分 → 表の検が名指して赤)・c9(名の所を `…` にすると緑。同じ macro の変更を足しても緑)。
- B の所見のうち直していない物: 例の frame の並び(どの generator の frame が traceback に載るか)は traverse-lib・VM の投げ方で決まる、という推測。B も測っておらず、私も測っていない。今の ex07 の期待の出力では traceback の枠は `…` の中にある。

## 6. 予測と実際の波及

| シナリオ | 予測(盲検の前) | 実際 |
| --- | --- | --- |
| S1 | 壊した側だけ。example-check・期待の出力は変えない | 予測どおり(c1: 例 2 本に変異を当てると、検を変えずに 2 本が赤) |
| S2 | 何も変えない | 予測どおり(c0 は別の path の写しの木・実装の時の 2 つ目の木でも緑) |
| S3 | 例 + 期待の出力 + 変異の表の 1 行 | 直す前は、兄弟の helper を使う例で判じの code(写しの作り方)まで変わった(盲検 A)。これは example-check の中の「走らせる場所」の知識が 2 通りあった漏れで、境界の直しで 1 通りにした。直した後は予測どおり(c5: 検の file の差分は表の 1 行だけ・c6) |
| 盲検 B | (主張の外 — 件の結果ではない行の伏せ方) | 期待の出力に macros の隠す中身が固まり、macros の内部の変更が examples へ波及した。example-check に「Hy の付ける名を置かない」を足し、波及を置いた時点で止める |

予想の範囲は後から広げていない。S3 の直す前の失敗例は [evidence/repro/a1-refactor-red.txt](evidence/repro/a1-refactor-red.txt) に残す。

## 7. 証拠

### 7.1 実装の前と実装の時(c9e1622b5 と、載せ直す前の同じ差分 39ec749c2)

| 記録 | 何を見たか | 結果 |
| --- | --- | --- |
| [evidence/before/mutants-at-cffba412b-green.txt](evidence/before/mutants-at-cffba412b-green.txt) | 直す前の main cffba412b で、ex05・ex07 に変異([evidence/before/mutants.patch.txt](evidence/before/mutants.patch.txt))を当てた木 | 26 passed, 1 skipped — 見逃しの再現 |
| [evidence/implementation/after-green.txt](evidence/implementation/after-green.txt)・[other-tree-green.txt](evidence/implementation/other-tree-green.txt) | 作った木と、別の path の木(c9e1622b5) | どちらも 31 passed, 1 skipped |
| [evidence/implementation/c1-mutants-red.txt](evidence/implementation/c1-mutants-red.txt)〜[c4-not-compared-runs-red.txt](evidence/implementation/c4-not-compared-runs-red.txt) | 変異・広すぎる `…`・変異の行の欠け・比べない表への戻し | どれも名指した検だけが赤 |

### 7.2 最後の版 7d56f9307b の測り直し

手順は [evidence/measure.sh.txt](evidence/measure.sh.txt)(写しの木 = `git archive 7d56f9307`、変える所を毎回戻し、検の file 1 本だけを走らせる)。記録は `evidence/checks/`。

| 検 | 正/負 | シナリオ | 結果 |
| --- | --- | --- | --- |
| c0 | 正 | S1・S2・S3 | 32 passed, 1 skipped(別の path の写しの木) |
| c1 | 負 | S1 | `test_example_runs[ex05]`・`[ex07]` が赤(2 failed) |
| c2 | 負 | S2 | `…` を件の結果まで広げると、`test_example_runs` は通り、変異の検が「… が件の結果まで伏せている」で赤(2 failed) |
| c3 | 負 | S3 | ex07 の変異の行を外すと表の検が赤 |
| c4 | 負 | S3 | ex05 を比べない表へ戻すと `test_not_compared_example_runs_no_program[ex05]` が赤 |
| c5 | 正 | S3 | 新しい例 ex10(ex05 の写し)を、例 + 期待の出力 + 変異の表の 1 行で足すと 3 passed。検の file の差分は表の 1 行だけ |
| c6 | 正 | S3(盲検 A) | A の変更(共有の helper)の上で 32 passed, 1 skipped |
| c7 | 負 | S3(盲検 A) | A の変更の上で `…` を合計まで広げると、変異の検が赤 |
| c8 | 負 | S1(盲検 B) | B の差分で、表の検が「Hy の付ける名が在る」で赤 |
| c9 | 正 | S1(盲検 B) | 名の所を `…` にすると、答えを変えない macro の変更を足しても 3 passed |

## 8. 残る限界(強制していない事)

- 検はマージの前には走らない(日次の段 packages だけ)。
- 変異の検は、表の変異 1 つが変える行だけを確かめる。件の結果を載せる別の行の伏せすぎは、その行を変える変異が無いと見えない。
- 比べない例(ex04_debug・ex04_debug2)が件を走らせない事は、`run`・`async_run` の名だけで見る。
- 期待の出力に置かない物として断るのは、checkout の path と Hy の付ける名だけ。他の隠す中身(番地・lib の内部の名)は見えない。
- 変異の写しは examples/ の dir ごと作る。examples/ の外の file を `__file__` からの相対 path で読む例は、写しで落ちる。
- 赤の時の差分は、`…` の行も違う行として出る。
- 範囲の外の所見(依頼者が別の card にする): ex07 の履歴は Zip が 2 つの履歴を繋ぐので `index`・`signal` が 2 度出る。ex04_debug・ex04_debug2 は件を走らせない debug の残り物で、examples/ から外す案がある。
