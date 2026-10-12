# 設計検証 lt-EDXAAEFYPM5TD5HTTXD3Q4V4XD — doeff-traverse の例の検の限界 3 つ(card ki-f8a58fb49dd9)

この文書の目的: doeff-traverse の例の検について、将来の変更がどこまで波及し、何を検が拒むかを、盲検の失敗例で確かめた結果として
示す。読み手はこの文書だけで、(1) 何が強制され何が強制されないか、(2) 盲検の失敗例がどう再現し、どう直したか、(3) 証拠の在りか
を辿れる。盲検の前の主張は design-before-blind.md(sha256 58c3c56a…)に固定してあり、この文書はそれを上書きしない。

## 0. 完了の範囲と版

| 項目 | 値 |
|---|---|
| 範囲 | 実装完了まで(設計・実装・盲検・直し・再検証) |
| 実装の版 | doeff main b62dd0533(着地の列 L2148 — 限界 1〜3 と所見 4 の強制) |
| 調べた版(base_revision) | doeff main 86ecd1344(b62dd0533 を含む) |
| 盲検の後の直し | branch `wt/ki-f8a5-design-check` の commit c8baa36a9(子に 0 の終了を求める・子の時間帯を固定)と 921e7b171(時間帯は `env TZ=UTC0 …` の命令で決める — os.environ を読まない・doeff-linter DOEFF004)。この記録と同じ branch で着地させる |
| 直しの後の証拠の版 | 921e7b171(evidence/checks・evidence/mutants の記録の頭に版と作業木の差分を書いた) |
| 対象の file | `packages/doeff-traverse/tests/test_examples_run.py`・`packages/doeff-traverse/tests/run_example_failing_a_case.py` |
| 検の経路 | 変えた所の検(pyproject.toml の `[[tool.doeff.contract-tests]]` の prefix `packages/doeff-traverse/` が file を名指す)と日次の全体検証 |

設計検証は実装の着地の後に行った(依頼の種類が調べ物で、設計検証の記録は着地の後の完了の検査が求めた)。そのため盲検は実装済みの
版を材料にし、盲検で見つかった穴は c8baa36a9・921e7b171 で直した。

## 1. 要件(card ki-f8a58fb49dd9)

- R1 伏せすぎの行: 期待の出力の印 `…` は件の結果を載せる行を伏せられず、伏せれば変異が無くても赤。
- R2 件・Program を走らせない例: 名ではなく実行の印で確かめる。
- R3 置かない物: 書いた木では毎回同じに出て、別の木・別の機体で外れる物を期待の出力に置けば赤。
- R4 例の source の文字列に頼らない: 件の中の失敗を起こす仕掛けが例の source を持たない。

## 2. 責務(盲検の後に 1 か所を改めた)

| id | 持ち主の file | 持つ知識・判断 | 隠す物 |
|---|---|---|---|
| examples | `examples/*.hy`・`examples/expected/*.txt` | 例の data・handler の組(例の中で定義した handler も含む)・印字。期待の出力のどこに印を置くか | なし(読み物) |
| example-check | `tests/test_examples_run.py` | 例の母集団・印の種類(行だけの `…` = frame・表 MARKS)・frame の見分け・表 BROKEN・NO_CASES・UNPLACEABLE・子の環境(時間帯 CHILD_TIME_ZONE)・子の終了コードの意味・合否 | 下請けの関数 |
| failing-child | `tests/run_example_failing_a_case.py` | 件の中の失敗の起こし方・実行の印の数え方。依る traverse-lib の約束 3 つ(Traverse の作り方・handler が件ごとに f を呼ぶ・件の Program の中の例外を件の失敗として記録する) | sys.monitoring の道具の番号・包み方 |
| traverse-lib | `doeff_traverse/` | traverse-lib の handler(sequential・parallel・parallel_fail_fast)の件の失敗の方針 | handler の中の frame |
| doeff-run | `doeff/run.py` | Program を走らせる入口 | VM |
| macros | doeff-hy | 展開の規則 | 展開の中身 |

改めた所(盲検 B の判定 §6.2 による): 盲検の前の表は traverse-lib を「件の失敗の方針」の持ち主と書いたが、Traverse の公開の
約束は「Handler decides: sequential, parallel, error strategy per item」(`doeff_traverse/effects.py` の Traverse の docstring)で、
方針を決めるのは Traverse を受ける handler。traverse-lib が持つのは自分の handler の方針で、例が自分の handler を組めば、その方針は
例の持ち分になる。持ち主の言い換えであって、強制の範囲を広げたのではない(§6.2 の再現は検が通る事も確かめた)。

改めた所(盲検 A による): failing-child が依る物に「件の Program の中の例外を件の失敗として記録する」を足し、example-check が
子の終了コードの意味(0 = 起こした失敗が件の失敗として記録された)を持つ事を明記した。盲検の前の表はこの共同の不変条件を
書いていなかった。

## 3. 公開契約と不変条件(直しの後)

1. 印: 行だけの `…` は traceback の frame の行だけを 1 行以上呑む。行の中の印は表 MARKS の綴りだけ(今は `…s`)。
2. 置かない物(UNPLACEABLE): checkout の path・機体の置き場(venv・Python・home)・Hy の付ける名・traceback の frame の行。
3. 子の環境: 子(例・件の中で失敗させる子)は親の環境のまま、時間帯だけ `TZ=UTC0` で走る(直しで足した)。
4. 件の中の失敗: 件の中で失敗させた子は **0 で終わり**、その出力は期待の出力に合わない(直しで「0 以外なら見えたと数える」を外した)。
   件の関数を呼ばない例は NO_CASES に理由つきで在る。
5. 実行の印: 走る例は全部、子の中で doeff の run の実行が 1 度以上始まる。
6. macro の持ち主: 例は macro を定義せず、外から require せず、展開を引き出さない。

## 4. 変更シナリオ — 盲検の前の主張と実測

| id | 軸 | 盲検の前の主張(design-before-blind.md §4) | 実測した波及 |
|---|---|---|---|
| S1 | effects | traverse-lib・macros・backend が変わり件の中で例外が起きるか、例が件の結果を印字しなくなれば、example-check を変えずに赤 | 今の traverse-lib の方針の下では主張どおり(M1・M2・M3・M5 が赤)。盲検 A: traverse-lib の方針を「Fail を通った失敗だけを件の失敗にする」に変えると、件の結果を印字しない例も緑のまま(repro/a2)— 主張が崩れた。原因は example-check の「子が 0 以外で終われば失敗は見えた」の逃げ道と、failing-child が書き写した traverse-lib の方針(宣言されていない共同の不変条件)。直し(c8baa36a9)の後は方針の変化で走る例の全部が赤(checks/c1・c2) |
| S2 | storage | 例を足す・出力を変えずに書き換える・期待の出力を書き直すのは examples だけ。件の行を伏せる・件も run も無い file を足すと赤 | 主張どおり。出力を変えない ex05 の書き換えは examples だけで緑(M8)、伏せすぎ・debug の file は赤(M1・M3・M4・M6・M10)。盲検 B の retry の例(例の中に Traverse の handler を定義)は検を全部通る(repro/b1・checks/c4)— §6.2 の判定で契約の内側 |
| S3 | simulation | 失敗の起こし方・数え方が依る物が変われば、failing-child の 1 file に閉じ、黙って緑にならず走る例の全部が赤 | 依る物の表に traverse-lib の件の失敗の方針が欠けていて、方針が変わると黙って緑だった(盲検 A・repro/a1)。直しの後は赤(checks/c1)。包みを外す・数えを外すと赤(M9・M11) |
| S4 | distribution | 別の木・別の機体で期待の出力を変えずに緑。書いた木でしか合わない物を置けば書いた木で赤 | 機体の path・frame の行は赤(M7)。盲検 B の別の候補: 地方時の印字は機体の時間帯に依り、TZ=JST-9 の機体で外れた(repro/b4)— 主張が崩れた(UNPLACEABLE は列挙で時間帯を知らない)。直し(子の時間帯を UTC0 に固定)の後は親の時間帯に依らず緑(checks/c3・c0j)、固定を外すと赤(M12)。別の path の木(この記録の木)でも緑(checks/c0) |
| H1 | hardware | 対象外: 例は機器に触れない | — |
| C1 | concurrency | 対象外: 子は別 process で状態を共有しない。例の中の並行は traverse-lib の持ち分 | — |

予測と実測の差の原因: S1・S3 は「traverse-lib の件の失敗の方針」を failing-child と example-check が暗に前提にしていた(知識の漏洩
ではなく、宣言されず強制されていない共同の不変条件)。S4 は「機体で変わる物」を期待の出力の側の列挙(UNPLACEABLE)だけで止めようと
した事。どちらも予想の範囲を広げずに、検の側を直した。

## 5. 強制方法(直しの後)

| 守る事 | 強制 | 実装の場所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| 印は件の行を呑まない | 行だけの `…` が呑んだ行を frame の並びと照らす | `_compare`・`_frame_violations`・test_example_runs・test_frame_mark_swallows_only_frames | contract-tests の prefix・日次 | frame の見分けは「直前の File の file に同じ文の行が在る」まで |
| 行の中の印は種類の決まった形 | 表 MARKS の綴りで割る | `MARKS`・`_mark_violations`・test_expected_output_places_only_known_marks・test_mark_check_sees_unknown_marks | 同上 | 数 + s の形の件の結果は `…s` で伏せられる |
| 置かない物 | UNPLACEABLE を期待の出力に当てる | test_expected_output_places_nothing_unplaceable・test_unplaceable_table_catches_its_samples | 同上 | 走るたびに変わる物は表に無い(書き手の 2 度目の走りで外れる) |
| 機体の時間帯に依らない(直しで足した) | 子の命令を `env TZ=UTC0 …` で包む | `CHILD_TIME_ZONE`・`_in_child_time_zone`・test_children_run_in_a_fixed_time_zone | 同上 | 固定するのは時間帯だけ(locale 等は親のまま) |
| 件の中の失敗が出力に出る | 子が走りごとに最初の件を失敗させ、子は 0 で終わり出力が合わない(直しで 0 を求めた) | failing-child の `_first_case_fails`・test_case_failure_shows_in_compared_output | 同上 | 失敗させるのは走りごとに最初の件だけ |
| 件・Program を走らせる | 子が件の関数の呼び出しと run の code の実行の始まりを数える | failing-child の `_count_program_runs`・test_example_runs_a_program・NO_CASES・test_marks_count_only_what_ran | 同上 | doeff.run の関数の code だけを数える |
| macro の持ち主 | Hy の reader で form を読む | `_macro_violations`・test_example_uses_only_doeff_hy_macros | 同上 | 名で見る |

## 6. 盲検の再現と判定

### 6.1 盲検 A — 成立した(直した)

- 主張: traverse-lib の方針を変えると、件の結果を印字しない例を検が黙って通す。
- 再現(直す前の 86ecd1344): 方針の変更だけで 48 passed(repro/a1-policy-only-green)、変異 M(ex04_simple が `done` だけを
  印字)を足しても `-k ex04_simple` が 4 passed(repro/a2-policy-plus-silent-ex04-green)。主張どおり。
- 直し(c8baa36a9): 子の終了コードの逃げ道を外し、件の中で失敗させた子は 0 で終わる事を求める。failing-child の docstring に
  依る約束 3 つを書く。
- 再検証: 方針の変更だけで 9 failed(checks/c1 — 全部「件の中で失敗させた子が 0 以外で終わった」)、変異 M を足して赤
  (checks/c2)。今の方針では 49 passed(checks/c0)。
- 採らなかった直し: 注入を Fail にする案(返答の (b'))は、注入した失敗の扱いが例ごとの Fail の handler(examples の持ち分)で
  決まる(replace-with-zero の例では成功に変わった — 返答の実測)ので採らない。方針が変わった時に failing-child を新しい約束へ
  合わせるのは failing-child の持ち分(失敗の起こし方の持ち主)で、検はそれを全部の赤で知らせる。

### 6.2 盲検 B(主の例)— 検の通過は再現した。責務の違反としては成立しない(契約の内側)

- 主張: 例の中に Traverse を受ける retry の handler を書くと、検を全部通るのに件の失敗の判断と件の履歴が例へ移る。
- 再現: ex10_item_retry を置くと 52 passed(repro/b1)。probe で、例の数えた試行 3 に対し lib の履歴は attempt=1(repro/b2)。
- 判定: Traverse の公開の約束は「Handler decides: … error strategy per item」で、Traverse を受ける handler を利用者(例)が組むのは
  約束の内側(ex01 は Fail を、ex08 は ItemOutcome を例の中の handler で受けている)。ex10 の履歴の食い違い・入れ子の Traverse を
  retry しない事は、その例の作りの不足で、例の検(例が走り、期待どおり印字し、件の失敗が出力に出る)が判じる事ではない。
  盲検の前の §2 の「traverse-lib が件の失敗の方針を持つ」は言い過ぎだったので、§2 で「traverse-lib の handler の方針」に改めた。
- 直しの後も ex10 は通る(checks/c4 — 53 passed)。

### 6.3 盲検 B(別の候補)— 成立した(直した)

- 主張: 例が `datetime.fromtimestamp` の地方時を印字すると、期待の出力が機体の時間帯に依る。
- 再現(直す前): 親の TZ 無しで緑(repro/b3)、TZ=JST-9 で test_example_runs[ex07] が赤(repro/b4)。
- 直し(c8baa36a9・921e7b171): 子の命令を `env TZ=UTC0 …` で包み、時間帯を固定する(POSIX の綴り — この Pod には時間帯の data base が無い)。
- 再検証: TZ=JST-9 の親で緑(checks/c3)、file 全部も TZ=JST-9 で 49 passed(checks/c0j)、固定を外すと
  test_children_run_in_a_fixed_time_zone が赤(mutants/M12)。

## 7. 実装の証拠(変異)

変異は `evidence/mutants/mutate_dc.py.txt` の名の付いた変更を直しの後の木に当て、検を走らせて元へ戻した。

| 変異 | 当てた変更 | 結果 |
|---|---|---|
| M1 | ex07 の行だけの `…` に例外の行と件 [2] を呑ませる | test_example_runs[ex07] が赤(呑んだ行を名指す) |
| M2 | ex05 の合計 550 を `…` に | 2 本赤(形でない印・出力の差) |
| M3 | ex07 の `Success: 3` を `Success: …` に | 2 本赤 |
| M4 | 件も run も無い debug 例を足す | 2 本赤 |
| M5 | 件を走らせるが件の数しか印字しない例を足す | test_case_failure_shows_in_compared_output が赤 |
| M6 | NO_CASES から ex01 を外す | 赤 |
| M7 | 機体の Python の path と frame の行を期待に置く例 | test_expected_output_places_nothing_unplaceable が赤 |
| M8 | ex05 の handler の式を出力を変えずに書き換える | 49 passed(examples だけで済む) |
| M9 | 子が Traverse を包まない | 9 本赤 |
| M10 | run が呼ばない関数の中にだけ在る例 | 2 本赤 |
| M11 | 子が run を数えない | 11 本赤 |
| M12 | 子の時間帯の固定を外す | test_children_run_in_a_fixed_time_zone が赤 |

## 8. 未確認事項と限界(合格の条件ではなく、主張の外として残す)

- 盲検の model は要求値(Agent tool の opus・effort xhigh)で、観測していない(blind-meta.md)。
- 検は file 1 本と `-k` で絞った分だけを走らせた。日次の全体検証は日次の制限に従い、この記録では走らせていない。
- 行の中の `…s` は数 + s の形の件の結果を伏せうる。走るたびに変わる値と件の結果を見分けるには文の意味が要る。
- 子の環境で固定するのは時間帯だけ。locale に依る印字(setlocale の後の書式)は今の例に無く、固定していない。
