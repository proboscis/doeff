# 設計検証 lt-EDXAAEFYPM5TD5HTTXD3Q4V4XD — 盲検の前の主張(card ki-f8a58fb49dd9)

この文書の目的: doeff-traverse の例の検(packages/doeff-traverse/tests/test_examples_run.py と子の入口
run_example_failing_a_case.py)について、将来の変更がどこまで波及するか・何を検が拒むかの主張を、失敗例探しの前に固定する。
読み手はこの文書と、§0 の版の実コードだけで、主張を反証する変更を組み立てられる。

## 0. 完了の範囲と版

- 範囲: 実装完了まで。実装は doeff main の b62dd0533(着地の列 L2148)で入っている。この設計検証は実装の後に行う
  (依頼の種類が調べ物 investigate で、設計検証の記録は着地の後に求められた)。
- 調べる版: doeff main 86ecd1344(b62dd0533 を含む)。
- 対象の file:
  - `packages/doeff-traverse/tests/test_examples_run.py`(例の検)
  - `packages/doeff-traverse/tests/run_example_failing_a_case.py`(件の中で失敗させる子の入口)
  - `packages/doeff-traverse/examples/*.hy` と `packages/doeff-traverse/examples/expected/*.txt`(例と期待の出力)
- 検の走らせ方: `uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q`(この版で 48 passed・1 skipped —
  skip は空の表 BROKEN の parametrize)。変えた所の検(pyproject.toml の `[[tool.doeff.contract-tests]]` の
  prefix `packages/doeff-traverse/`)と日次の全体検証がこの file を走らせる。

## 1. 要件(card ki-f8a58fb49dd9 の所見と受入)

- R1 伏せすぎの行: 期待の出力の印 `…` は、件の結果を載せる行を伏せられない。伏せたら、その行を変える変異が無くても検が赤。
- R2 件・Program を走らせない例: 例が件や Program を走らせる事を、名ではなく実行の印で確かめる。
- R3 置かない物: 書いた木では毎回同じに出て、別の木・別の機体で外れる物を、期待の出力に置けば赤。
- R4 例の source の文字列に頼らない: 件の中の失敗を起こす仕掛けが例の source の文字列を持たず、出力を変えない例の書き換えで
  検の file を直す事が無い。
- 受入: 限界それぞれについて、強制する検が doeff の main に在り変異で赤になる事を確かめたか、強制しない訳が card に在る。

## 2. 責務(module)

| id | 持ち主の file | 持つ知識・判断 | 隠す物 | 公開の形 |
|---|---|---|---|---|
| examples | `examples/*.hy`・`examples/expected/*.txt` | 例ごとの data・handler の組・印字。期待の出力のどこに印を置くか | なし(読み物) | `python -m hy examples/<名>.hy` が 0 で終わり、標準出力が期待の出力に合う |
| example-check | `tests/test_examples_run.py` | 例の母集団(glob)・印の種類(行だけの `…` = frame の行・表 MARKS)・frame の行の見分け方・表 BROKEN・NO_CASES・UNPLACEABLE・macro の持ち主の断り・合否 | 下請けの関数(_compare・_frame_violations・_line_pattern・_run_marks など) | pytest の id(§4 の表) |
| failing-child | `tests/run_example_failing_a_case.py` | 件の中の失敗の起こし方(Traverse の走りごとに最初の件の Program が例外を上げる)・実行の印の数え方(件の関数を呼んだ数・doeff の run の実行の数)・印の書き方(標準エラーの 1 行 `case-calls: N program-runs: M`) | Traverse の作り方(`Traverse.__init__(self, f, items, label=None)`)・doeff の run の関数の在りか・sys.monitoring の道具の番号 | `python run_example_failing_a_case.py <例の path>` — 例を `hy <例>` と同じに走らせ、終わる時に印の行を標準エラーへ書く |
| traverse-lib | `doeff_traverse/handlers.py`・`effects.py` | 件の失敗の方針(件の中の例外を件の失敗として記録して先へ進む)・件の関数 f を件ごとに呼ぶ事 | handler の中の frame | effect の class・handler |
| doeff-run | `doeff/run.py`(`doeff.run`) | Program を走らせる入口 | VM | `run(program)` |
| macros | doeff-hy の macro | 展開の規則 | 展開の中身(gensym・無名関数の番号) | `(require doeff-hy.macros [...])` |

effects と寿命:
- example-check: 子 process を起こす(1 回の pytest の session で、選ばれた検の子だけ・4 本ずつ並べる)。例と期待の出力を読む。
  書くのは test_marks_count_only_what_ran の tmp_path の 1 file だけ。子は終わるか締切(50 秒 × 負荷の倍率)で終わる。
- failing-child: 自分の process の中だけで Traverse.__init__ を包み、sys.monitoring の profiler の番号を取る。
  doeff の名前空間は書き換えない。process の終わりに印を書く。
- examples: 標準出力・標準エラーへの印字(ex05 は実時間の sleep)。

## 3. 公開契約と不変条件

1. 期待の出力の印:
   - 行が `…` だけの行は、traceback の frame の行を 1 行以上呑む。呑んだ行の並びは File の行
     (`File "<path>", line N, in <name>`)で始まり、続く行は File の行・直前の File の行の file の source の行
     (前後の空白を除いて一致)・frame の中の印の行(`^~` の並び・`...<N lines>...`・`[Previous line repeated N more times]`)。
     それ以外を呑むと test_example_runs が呑んだ行を名指して赤。
   - 行の中の印は表 MARKS の綴りでしか置けない(今は `…s` = `\d+(?:\.\d+)?s` — 実時間の秒)。行を越えない。
     表の綴りでない `…` は test_expected_output_places_only_known_marks が行番号つきで名指す。
2. 期待の出力に置かない物(表 UNPLACEABLE): checkout の path・機体の置き場(sys.prefix・sys.base_prefix・home)・
   Hy が付ける名(`_hy_anon_`・`_hy_gensym_`)・traceback の frame の行。
3. 件の中の失敗: 走る例は全部、件の中で失敗させる子でも走らせ、その出力は期待の出力に合わない(子が 0 以外で終われば
   失敗は終了コードに出たと数える)。件の関数を 1 度も呼ばない例は表 NO_CASES に理由つきで在り、在る例は件を呼ばない。
4. 実行の印: 走る例は全部、子の中で doeff の run の実行が 1 度以上始まる。名だけ在って走らない run(import だけ・
   呼ばない関数の中・quote の中・doeff の外の同じ名)は数えない。
5. macro の持ち主: 例は macro を定義せず、doeff-hy の外から require せず、展開を引き出さない(hy.macroexpand など)。

## 4. 変更シナリオと主張(失敗例探しの前に固定)

| id | 軸 | 変える要求 | 主張 | 予測する波及 | 変えない物 |
|---|---|---|---|---|---|
| S1 | effects | traverse-lib・macros・例の backend のどれかが変わり、件の中で例外が起きる(例は 0 で終わる)か、例が件の結果を印字しなくなる | example-check を変えずに検が赤になる(印字された件の行は伏せられず、件の失敗は子の出力に出る)。件の結果を印字しない例は test_case_failure_shows_in_compared_output が赤 | 変えた側の module だけ | example-check・failing-child |
| S2 | storage | examples に例を足す・例を出力を変えずに書き換える・期待の出力を書き直す | 例の書き換えで出力が変わらなければ、examples の外は変わらない(検の file に例の source の文字列が無い)。期待の出力で件の行を伏せれば赤。件も run も走らせない file を足せば赤 | examples だけ | example-check・failing-child |
| S3 | simulation | 件の中の失敗の起こし方・実行の印の数え方が依る物(Traverse.__init__ の形・doeff の run の在りか)が変わる | 依る知識は failing-child の 1 file に閉じ、変わった時は黙って緑にならず、走る例の全部が赤になる | failing-child だけ | example-check・examples |
| S4 | distribution | 検を別の木(日次の作業木)・別の機体(別の home・別の Python の置き場)で走らせる | 期待の出力を変えずに緑。書いた木でしか合わない物を期待の出力に置けば、書いた木で赤 | なし | 全部 |
| H1 | hardware | — | 対象外: 例は機器に触れない | — | — |
| C1 | concurrency | — | 対象外: 子は別 process で状態を共有しない(期待の出力を読むだけ)。例の中の並行(parallel の handler)は traverse-lib の持ち分で、この変更は変えない | — | — |

前提: Python は 3.12 から(sys.monitoring)。repo の `.python-version` は 3.14t。例は `hy <例>` で走る script のまま。

## 5. 強制方法

| 守る事 | 強制 | 実装の場所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| 印は件の行を呑まない(§3-1) | 期待の出力を正規表現にし、行だけの `…` を名のある組で呑ませ、呑んだ行を frame の並びと照らす | `_expected_line_pattern`・`_compare`・`_frame_violations`・test_example_runs・失敗例 test_frame_mark_swallows_only_frames | 変えた所の検(contract-tests の prefix)・日次 | frame の行の見分けは「直前の File の file の source に同じ文の行が在る」まで。行番号までは照らさない |
| 行の中の印は種類の決まった形だけ(§3-1) | 表 MARKS の綴りで割り、形でない `…` を名指す | `MARKS`・`_line_pattern`・`_mark_violations`・test_expected_output_places_only_known_marks・失敗例 test_mark_check_sees_unknown_marks | 同上 | 秒の形(数 + s)の件の結果は `…s` で伏せられる(検は文の意味を知らない) |
| 置かない物(§3-2) | 表 UNPLACEABLE の正規表現を期待の出力に当てる | `UNPLACEABLE`・test_expected_output_places_nothing_unplaceable・失敗例 test_unplaceable_table_catches_its_samples | 同上 | 走るたびに変わる物(番地・一時 dir の名)は表に無い — 書き手の 2 度目の走りで外れる |
| 件の中の失敗が出力に出る(§3-3) | 子が Traverse の走りごとに最初の件を失敗させ、出力が期待に合えば赤 | failing-child の `_first_case_fails`・`_init`、test_case_failure_shows_in_compared_output | 同上 | 失敗させるのは走りごとに最初の件だけ |
| 件・Program を走らせる(§3-3・§3-4) | 子が件の関数の呼び出しと run の code の実行の始まりを数え、標準エラーに書く | failing-child の `_count_program_runs`・`_write_counts`、test_example_runs_a_program・test_case_failure_shows_in_compared_output・NO_CASES・失敗例 test_marks_count_only_what_ran | 同上 | doeff.run の関数の code だけを数える |
| macro の持ち主(§3-5) | Hy の reader で form を読み、定義・外の require・展開の引き出しを名指す | `_macro_violations`・test_example_uses_only_doeff_hy_macros・test_macro_check_sees_nested_forms | 同上 | 名で見る(展開の関数を別名で呼べば見えない) |
