# 設計検証 lt-H07C2RG4T2BM59EENGQWQHSJKH — 件ごとの結末の契約 ItemOutcome(card ki-e3a936788353)

## この文書の目的

読み終えた人が次の 3 つを言えるようにする。

1. 件ごとの結末の契約(`ItemOutcome`)で、どの将来の変更がどの責務に閉じ、何がそれを機械で守るか。
2. 盲検 A・B が何を破り、どう直し、直した後に何で確かめたか。
3. 何が今も守られていないか(限界)。

盲検の前の主張は `design-before-blind.md`(commit 9d1e02a36 で固定・以後変えていない)。この文書はその後の結果と直し。

## 範囲と版

| 項目 | 値 |
|---|---|
| 範囲 | 実装完了まで |
| 基準版(変更の前) | `a2254d66cfc52f9fa9c3b4829bff2d76191bccb3` |
| 実装の版(着地済み・L2136) | `3293bc4cc357ec9f6ed02e07d4facf75de0ae882` |
| 盲検の前の主張と入力 | commit 9d1e02a36(branch `wt/design-check-item-outcome`) |
| 盲検の後の直し | commit 22d78239f(受け手は渡す・契約の誤り)・3f82ca1b5(入れ子の契約の誤りを件の失敗にしない)— 証拠 C1〜C7 はこの版(branch `wt/design-check-item-outcome`)で取った |
| 載せ直した版 | origin/main 2bf226a96 の上へ載せ直し、直しは 006b9099a・b13d825cb(branch `wt/design-check-item-outcome-r2`・この記録と同じ branch で着地する)。載せ直しの後に関わる検を走らせ直した(C8) |
| 順 | 実装と着地が先。設計検証は完了の検査に求められて後から行った |

## 要件(固定)

| id | 受入の条 |
|---|---|
| R1 | c481163fe: sequential・parallel・parallel_fail_fast の 3 つが件ごとの結末を公開の契約で出し、その契約が doeff の main に在る |
| R2 | c6eeb01ac: 失敗する件のある pipeline で tqdm-progress の bar が n == total で閉じる事を確かめるテストが doeff の main に在る |

## 責務(module)— 直した後

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
|---|---|---|---|---|
| M1 利用者の pipeline | 何を計算するか | 段の並び・件の値 | 実行の順・並列・進捗・結末の知らせ | `Traverse(f, items, label)` を出す Program |
| M2 結末の消費者 | 件の結末をどう数え・見せるか(`progress_bars`・ex08 の `tqdm-progress`・利用者の handler) | bar の開閉・数え方・表示 | なし(M3 の内部の規則を持たない) | `ItemOutcome` を注記で名指し、**見て外へ渡す** handler |
| M3 Traverse の handler | 件をどう走らせ、結末をどう決めて知らせるか。**知らせに答えるのは M3 だけ** | 結末の分類・持ち越し・Skip・例外・知らせる時と場所・名指す handler の判定・**受け手が渡したかの確かめ** | 件の Program の組み立て・内側の handler の付け直し・並列の task | `Collection` と `ItemOutcome`・`ItemOutcomeNotPassedError` |
| M4 公開の語彙 | 結末の型と受け手の決まり | `ItemOutcome`・`ItemOutcomeKind`・`ItemOutcomeNotPassedError`・`ProgressBar`・`BarFactory` | なし | `effects.py`・`.pyi`・`handlers.pyi`・README |
| M5 静的な閉じの検査 | 答えの無い効果が残らないか | handler の節が出す効果の読み | なし | effect-analyzer・foundation closure |
| M6 検 | 契約の検 | 期待する結末の列 | なし | `tests/test_item_outcomes.py` ほか |

## 変更シナリオ — 事前の予測と実測

| シナリオ | 事前の主張(design-before-blind.md) | 実測 | 判定 |
|---|---|---|---|
| S1 concurrency: 方針を sequential・parallel(N)・parallel_fail_fast の間で替える | 変わるのは M2 の stack の 1 要素だけ。数える件・total・bar の閉じ方は同じ(fail_fast が止めた Traverse を除く) | 盲検 B: 知らせに例外を投げ返す受け手(`failure_budget`)を置くと、方針ごとに走る件(4 と 8)・bar(3/8 と 7/8)が分かれ、提示した検は全部通った(`evidence/b/B-repro-before.txt`)。直した後は、その受け手はどの方針でも `ItemOutcomeNotPassedError` で断られ(`evidence/b/B-after-fix.txt`)、渡す受け手では方針を替えても数えと bar の閉じ方は同じ(C1) | 3293bc4cc では不成立(受け手が M3 の走り方の判断を握れた)。直した後は成立 |
| S2 effects: 結末の消費者を足す | 変わるのは M2 の handler 1 つと stack の 1 要素だけ。M3 の規則を写さない | 盲検 A: 答えて止める `progress_bars` の隣に記録係を足すと、並び次第で bar か記録係のどちらかが黙って何も受けない(`evidence/a/A-repro-before.txt`)。「受け手は渡す」が契約に無かった。直した後は渡す受け手どうしがどの並びでも全部を受け、答える受け手は断られる(`evidence/a/A-after-fix.txt`・C3・C4) | 3293bc4cc では不成立(契約に無い共同の不変条件)。直した後は成立 |
| S3 simulation: bar を記録係に替える・結末を記録して比べる | 変わるのは `make_bar` の差し替え(か記録用の handler)だけ | 記録係の bar(tqdm を使わない `RecBar`・tqdm の子の `_RecordedBar`)と記録用の handler で、結末を決定的に比べられた(C5)。名指さない記録係には何も届かない(C6・限界として文書化) | 成立 |
| S4 storage | 対象外: 保存の層が無い(保存するなら S2 の消費者 1 つ) | — | — |
| S5 hardware | 対象外: 機器に依らない | — | — |
| S6 distribution | 対象外: cluster へ件を配る Traverse の handler は無い | — | — |

## 盲検 A・B と設計者の再現

記録: `blind/blind-meta.md`(起動)・`blind/blind-common.md`・`blind/blind-a-input.md`・`blind/blind-b-input.md`(入力)・`blind/blind-a-return.md`・`blind/blind-b-return.md`(未加工の返答)・`blind/a-files/`・`blind/b-files/`(実験の file)。

### 盲検 A — 2 つ目の受け手を足す

- 返答の要点: 受け手が知らせに `Resume(k, None)` で答えると、外の受け手には届かない。同梱の `progress_bars` がそう書かれていたので、記録係や 2 本目の bar を足すと、並び次第で片方が黙る。契約(docstring・README)に「受け手は渡す」が無く、検の補助 `_Reports` の docstring にしか無かった。
- 設計者の再現(`evidence/a/A-repro-before.txt`・版 3293bc4cc): `[ledger(resume), progress_bars]` で bars が `[]`、`[progress_bars, ledger(resume)]` で記録が空、`[progress_bars, progress_bars]` で 2 本目が空。sequential・parallel(2) とも同じ。**成立**。

### 盲検 B — 受け手が Traverse を止める

- 返答の要点: `failure_budget(max_failed)` が `ItemOutcome` を名指し、失敗が上限を超えたら知らせに `ResumeThrow` で例外を投げ返す。検 4 file と B の実装者の検で 42 passed・semgrep と doeff-linter の基点比較も通る。一方で「残りの件を走らせるか・Collection を答えるか」の判断が M2 へ移り、方針ごとに振る舞いが分かれ、M3 が隠す知識(どこで知らせるか)が無いと正しく止められない。
- 設計者の再現(`evidence/b/B-repro-before.txt`): 42 passed(rc 0)。budget=1 で sequential は 4 件だけ走って `FailureBudgetExceeded`・bar 3/8、parallel は 8 件全部走って 7/8。入れ子では内の例外が外の件の failed に変わった。**成立**。

## 直し

| id | 直し | 受けた失敗例 | 再検証 |
|---|---|---|---|
| D1 | 契約: 受け手は知らせを見て外へ渡す(`Pass(effect, k)`・defhandler の `(reperform effect)`)。答えるのは M3 だけ(`ItemOutcome` の docstring・README・CHANGELOG・`.pyi`) | A・B | 文書の変更。下の D2・D3 が機械で守る |
| D2 | M3: 自分の知らせに目印 `_PASSED_ON` で答え、出した所(`_report`)で目印が返ったかを確かめる。目印でない答え・例外なら `ItemOutcomeNotPassedError`(`cause` = 投げた物)。取り消しの `TaskCancelledError` はそのまま上げる | A(答える受け手)・B(投げる受け手) | A-after-fix: 答える受け手の組は断られ、渡す受け手はどの並びでも全部を受ける。B-after-fix: B の実装者の検 2 件が赤(`ItemOutcomeNotPassedError` caused by `FailureBudgetExceeded`)・違反の実験はどの方針でも断られる |
| D3 | M2: `progress_bars` は数えた後に渡す | A | `test_consumers_compose_in_any_order`(bar 2 本と記録係・2 通りの並び × 2 方針) |
| D4 | M3: 入れ子の Traverse で上がった `ItemOutcomeNotPassedError` は件の失敗にせず上げる(parallel_fail_fast は外の件を failed として知らせ直さない) | B の再検証で見えた穴(内の契約の誤りが外の件の failed に飲まれる — for/do の From を 2 つ重ねると必ず入れ子) | `test_a_broken_report_contract_in_a_nested_traverse_is_no_item_failure`(3 方針) |

予想の範囲は広げていない。S1・S2 の主張は、直す前には成り立たなかった(盲検の失敗例と再現を残す)。

## 強制方法(直した後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 知らせは M3 の外へ漏れない(M5 の閉じを壊さない) | analyzer の静的な読みを検で固定 | `test_carried_builders.py::test_doeff_traverse_item_outcomes_stay_inside_the_handler` | pytest(日次の全体・手の focused) | 外へ出す変異(N5)を analyzer の検が捕まえるのは sequential・parallel。parallel_fail_fast は実行の検だけが捕まえる(`evidence/mutations-after-fix.txt`)。新しい Traverse の handler は検の表に足さないと外れる |
| 知らせに答えるのは M3 だけ(受け手は渡す) | 実行時の契約の確かめ(目印の答え・例外の捕まえ)と検 | `handlers.py` の `_report`・`_PASSED_ON`、`effects.py` の `ItemOutcomeNotPassedError`、検 `test_a_consumer_that_answers_the_report_fails_the_traverse`・`test_a_consumer_that_throws_into_the_report_fails_the_traverse`・`test_consumers_compose_in_any_order` | 受け手を足した stack を走らせた時(本番でも検でも)・pytest | 静的には止めない(受け手の節が Resume / ResumeThrow で終わる事を断る規則は無い)。誤りは知らせが出た時に上がる — 名指す受け手を置いた stack を一度走らせれば出る |
| 契約の誤りは件の失敗に飲まれない | 件の例外の捕まえ方で `ItemOutcomeNotPassedError` を上げ直す | `handlers.py` の 3 か所・検 `test_a_broken_report_contract_in_a_nested_traverse_is_no_item_failure` | pytest | — |
| 知らせの中身は Traverse の答えと同じ | 検 | `test_each_item_reports_the_outcome_the_traverse_answers` | pytest | parallel_fail_fast は失敗までの個別の検だけ |
| 名指す受け手が無い stack の動きは今のまま | 検 | `test_nothing_is_reported_unless_a_handler_inside_names_item_outcome`・`test_sequential_reads_handlers_once.py`・`test_traverse_effect_type_filter.py` | pytest | 費用は検で測らない(呼び出しの数の実測は card の発言) |
| M2 は M3 の規則を知らずに数える | 公開の型と検 | `effects.pyi`・`handlers.pyi`・`test_progress_bars_close_at_total_when_items_fail` | pyright(日次)・pytest | 消費者が Traverse を横取りして f を包む書き方を禁じる静的な規則は無い |

import の向き・万能の service・生の dict: 受け手の公開の形は `ItemOutcome` の欄(型のある値 `ItemResult`・Literal の `outcome`)だけで、M3 の内部(`_report`・`_PASSED_ON`・`_report_boundaries`)は module の private の名。状態の網羅: `outcome` は `ItemOutcomeKind` の Literal 4 語。

## 実装の証拠(checks)

| id | シナリオ | 種類 | 証拠 |
|---|---|---|---|
| C1 | S1 | 正常例 | `evidence/s1/C1-strategies-count-alike.txt`(8 passed) |
| C2 | S1 | 失敗例 | `evidence/b/B-after-fix.txt`(B の `failure_budget` がどの方針でも `ItemOutcomeNotPassedError`・B の実装者の検 2 件が赤) |
| C2b | S1 | 失敗例 | `evidence/s1/C2b-report-stays-inside-analyzer.txt`(10 passed)・`evidence/mutations-after-fix.txt` の N5 |
| C3 | S2 | 正常例 | `evidence/s2/C3-consumers-compose.txt`(6 passed)・`evidence/a/A-after-fix.txt` の 3・4・5 |
| C4 | S2 | 失敗例 | `evidence/s2/C4-answering-or-throwing-consumer-refused.txt`(11 passed)・`evidence/a/A-after-fix.txt` の 1・2 |
| C5 | S3 | 正常例 | `evidence/s3/C5-recording-consumers.txt`(6 passed) |
| C6 | S3 | 失敗例 | `evidence/s3/C6-unnamed-recorder-gets-nothing.txt`(12 passed) |
| C7 | S1・S2・S3 | 失敗例 | `evidence/mutations-after-fix.txt`(変異 6 つがどれも赤: N1 答えを確かめない・N2 投げを飲む・N3 progress_bars が答える・N4/N6 入れ子で飲む・N5 外へ出す) |
| C8 | S1・S2・S3 | 正常例 | `evidence/gate/C8-related-tests-after-rebase.txt`(載せ直した版 f72c5d4df で 171 passed・1 skipped・rc 0) |

関わる検の組(`packages/doeff-traverse/tests` 全部・`tests/test_for_do_runtime.py`・`tests/effects/test_traverse.py`・`test_carried_builders.py`)は直した後に 168 passed・1 skipped、載せ直した後に 171 passed・1 skipped(C8)。pyright は変えた 4 file(`effects.py`・`handlers.py`・`__init__.py`・`test_item_outcomes.py`)で 0 件(載せ直した版で測り直した)。変えた範囲の品質検査(code-quality・scope changed・基点 2bf226a96)は passed — 速い段で型検査を含まず、doeff-traverse の file は継続検査の対象に未導入(この変更の前から)。

## 未確認事項

- 盲検の model と effort の実の値(`blind/blind-meta.md`)。
- 本番(agora-controllers)の stack での受け手の数と並び(agora-controllers の doeff の pin は動かしていない)。
- 受け手の節が Resume / ResumeThrow で終わる事を静的に断る規則(実行時の確かめだけ)。
