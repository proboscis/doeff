# 設計検証 lt-9E0W15ZW21H6R3SA26CNW1T4FP — 組の同時数の上限の値の持ち主を系の宣言 1 つにする

## この文書の目的

読み終えた人が次の 4 つを言えるようにする。

- doeff-cluster で、task の同時数の上限の値と範囲を、どの module が持ち、どの module が数え、送り手は何を名乗るか。
- その分け方が、送り手の増加・値の変更・保存先・並行の置き・模擬の 5 つの変更に、どこまで狭く収まるか(盲検の前の主張と実測)。
- 盲検 A・B が示した失敗例と、その再現・直し・確かめ。
- 残る強制の限界。

## §0 完了の範囲と commit

完了の範囲: **実装完了まで**(card ki-b0e8028c8c8d の doeff-cluster の側)。上に載る系の側(送り手の名乗りを組の名だけにし、系の宣言に組を書く)は別の card(子 B)で、この検証の範囲外。配備もこの検証の範囲外。

| 段 | doeff の commit | 着地 |
|---|---|---|
| 実装(系の宣言の GroupLimit・資源 GroupLimit・組の名だけの名乗り・宣言の値での数え・/state の groupLimits) | 74ebb50b6c3c5de9a652088d11e16371d954abb1 | L2139 |

## §1 固定した要件

| # | 要件 | 出所 |
|---|---|---|
| R1 | 組ごとの上限の値と数える範囲は、系の宣言(System の task-limits)1 つが持ち、宣言の保存と受け口で値を検める(1 以上の整数・範囲の名) | card の「何をするか」1 |
| R2 | task の上限の名乗り(TaskLimit)は組の名だけ。値と範囲は名乗らない。移行のための読み分けは書かない | card の「何をするか」2 |
| R3 | 置き方(task-misfit・hold-limits)は候補の task の申告ではなく宣言の値で数える。宣言に無い組を名乗る task は受け口で断る | card の「何をするか」3・受入の条 cb0a9c75f |
| R4 | coordinator の /state に、組ごとの宣言の値と置いている数を出す | card の「何をするか」4・受入の条 c7e6872fe |
| R5 | 呼び手ごとの予算(組の名が呼び手ごとに生まれる)も、送り手が値を送らずに持てる | 親の決め R7 と R2・R3 の両立(依頼元へ lt-7DGRE0V0SNF2JVXPY1ATZTARC2 で知らせ、可を得た) |

失敗ケース(直す前): 送り手が task ごとに (組・値・範囲) を名乗り、coordinator は置く task 自身の申告と升の数を比べた。2 本目の送り手が値を上げると 1 本目の上限を越えて置かれ、同じ 2 本でも置いた順で同時数が変わった。

## §2 責務(module)

| id | 責務 | 持つ知識 | 隠す物 | 公開の口 |
|---|---|---|---|---|
| group-declaration | 系が組の上限を宣言する(System の task-limits・defsystem の頭の `:task-limits`・構成子 system-of) | 組の名 → 範囲・上限の値・区分ごとか(perPart) | 宣言の行の形(資源への書きは declare-writer) | `GroupLimit`・`System.task-limits`・`system-of :task-limits`・defsystem の `:task-limits {"<組>" {"scope" … "limit" … "perPart" …}}` |
| limit-rules | 組の宣言の形と task の名乗りの形の規則と JSON の綴り(純粋)。系の構成子・coordinator の受け口・保存の読み直しが同じ規則を読む | 範囲の名(TASK-LIMIT-SCOPES)・1 以上・名乗りの欄の閉じた集合(group・part) | JSON の欄の綴り | `group-limit-refusal`・`group-limit-of`・`group-limit-spec`・`task-limit-refusal`・`task-limits-refusal`・`task-limits-of`・`task-limits-json` |
| declare-writer | 宣言を coordinator の資源の口へ差分だけ書く(本番の declare と sim-cluster) | 資源 GroupLimit の作り・版つきの書き直しの本文 | HTTP の要求 | `apply-declaration`・`group-limit-write` |
| coordinator-resource | coordinator が宣言の値を資源 GroupLimit として持つ(作る・版つきの書き直し・消す・一覧・保存と読み直し) | 組の名で引く宣言の表(ClusterState.group-limits — 書き換えない表 Table) | 保存の鍵(grouplimit/<組>)・state file の欄 groupLimits | `/resources/GroupLimit`・`ClusterState.group-limits` |
| coordinator-placement | task の受け口で名乗りを宣言と照らし、置き方で宣言の値と範囲で数える | 升の鍵(組・区分・worker)・待ちの理由(TASK-LIMITED・TASK-UNDECLARED) | 升の数の数え方 | `limits-undeclared-refusal`・`limit-slot`・`limit-holdings`・`hold-limits`・`task-misfit`・受け口 POST /tasks・PUT /detached |
| state-view | 運用者に組ごとの宣言の値と置いている数を見せる | 画面の形(GroupLimitView・GroupHeld) | — | GET /state の `groupLimits` |
| sender | task を送る呼び手(doeff の側の構築関数 submit-detached-task と、上に載る系の送り手) | 自分がどの組の仕事か・区分(呼び手の id など) | — | `TaskLimit(group, part)` |

## §3 公開契約と所有

- 値と範囲の持ち主は group-declaration だけ。`TaskLimit` の欄は `group` と `part` だけで、送り手は値を持てない(型に欄が無い・本文の欄の集合は閉じている)。
- 組の宣言の形の規則は limit-rules の `group-limit-refusal` 1 つ。呼ぶのは system-of(系の値を作る時)・resource_policy の `checked-group-limit`(資源の口)・state_json の `group-limit-from-json`(state file と耐久の鍵の読み直し)。
- 受け口(cluster_policy の submit-task・detached_policy の submit-detached)は、形の規則(task-limits-refusal)の後に `limits-undeclared-refusal` で宣言と照らす: 宣言に無い組・perPart の組で part を名乗らない・perPart でない組で part を名乗る → 400。
- 置き方は升の鍵を `limit-slot` で作る: #(組 区分 worker)。区分は宣言が perPart の時だけ task の part、worker は宣言の範囲が worker の時だけ。比べる値は宣言の limit。升の数は拍ごとに置いている task から数え直し、同じ拍で置いた分を足す(hold-limits)。
- 受けた後に宣言から消えた組を名乗る task は、置いている物は止めず、待っている物は TASK-UNDECLARED で待つ。
- 資源 GroupLimit の版は spec が変わった時だけ進む(status は空)。置いている数は /state の groupLimits に出し、版に入れない。
- 保存の旧い形の task の行(値と範囲を名乗る limits)は、まだ終わっていない行を理由つきの failed にする(読み分けて値を捨てて数える道は作らない)。
- 停止・再開・解放: 資源 GroupLimit は宣言の道具が書き、DELETE で消える。coordinator の再起動では保存から読み直す。置いている task の止めは今までどおり(この変更は止めを持たない)。

## §4 変更シナリオと盲検の前の主張

| id | 軸 | 変更 | 主張 | 予想する変更範囲 | 変えない module |
|---|---|---|---|---|---|
| S1 | distribution | 同じ組を名乗る送り手が増える(別の Pod・別の会話・別の環境変数) | 送り手は値を名乗れないので、送り手の数や環境が違っても組の上限は宣言の値 1 つで数えられる。送り手を足すのに doeff の module は変わらない | sender(新しい送り手が組の名を名乗るだけ) | group-declaration・limit-rules・declare-writer・coordinator-resource・coordinator-placement・state-view |
| S2 | effects | 組の値を変える(例 6 → 8)・組を足す | 値の変更と組の追加は系の宣言の literal を変えて宣言し直すだけ。declare-writer が資源 GroupLimit を書き直し、置き方は次の置きから新しい値で数える。送り手と coordinator の code は変わらない | group-declaration(使い手の系の宣言の literal だけ) | limit-rules・declare-writer・coordinator-resource・coordinator-placement・state-view・sender |
| S3 | storage | coordinator の保存先で組の宣言を保存・読み直す(state file と耐久の鍵。保存先を足す時も同じ) | 保存の綴りと読みの検めは limit-rules の group-limit-spec と group-limit-refusal 1 つを通る。保存先を足す時に足すのはその保存先の鍵の組だけで、規則は写さない | coordinator-resource の保存の module だけ | group-declaration・limit-rules・declare-writer・coordinator-placement・state-view・sender |
| S4 | concurrency | 同じ拍に同じ組の task が複数待つ・宣言し直しと置きが重なる・送る順が変わる | 升の数は拍ごとに置いている task から数え直し、同じ拍で置いた分も足すので、同じ拍でも宣言の値を越えない。送る順は値に関わらない(値は宣言だけ)。値を下げても置いている task は止めない | なし(今の置き方のまま守られる) | 全部 |
| S5 | simulation | 宣言から置きまでを sim-cluster(本物の coordinator と worker)で確かめる | sim の宣言の書きは本番の declare と同じ判断 group-limit-write を通り、本物の coordinator の受け口と置き方を通るので、sim の検が本番の道を覆う。足すのは検の file だけ | 検の file だけ | 全部の実装の module |
| X-hw | hardware | 機体ごとに値を変える | 不適用: 親の決め R9 で先送り(long-command が置かれる機体は 1 台)。要る時は GroupLimit に機体の欄を足す公開契約の拡張になる(予想: group-declaration・limit-rules・coordinator-placement) | — | — |

前提: 送り手は doeff-cluster の受け口(POST /tasks・PUT /detached)を通して task を出す。系の宣言は宣言の道具(apply-declaration)で coordinator へ書く。coordinator は 1 つ(複数の coordinator が別々の宣言を持つ形は前提の外)。

## §4b 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 値と範囲の持ち主は宣言だけ(R1・R2) | 型: `TaskLimit` の欄は group と part だけ。受け口の規則: `task-limit-refusal` の欄の閉じた集合 TASK-LIMIT-KEYS(他の欄を 400) | `shared/intent/detached_model.hy` の TaskLimit・`.pyi`・`shared/core/detached_rules.hy` | pytest `tests/test_group_limits_declared.hy::test_a_task_naming_an_undeclared_group_or_sending_a_value_is_refused`(変えた所の検と 4 時間ごとの全体の回) | TaskLimit に欄を足す将来の変更を止める静的な規則は無い(検が値を送る本文を断る事で見る) |
| 組の宣言の規則は 1 つ(R1) | 規則の関数 1 つ(group-limit-refusal)を 3 か所が呼ぶ | `detached_rules.hy`・`service_build.hy` の system-of・`resource_policy.hy` の checked-group-limit・`state_json.hy` の group-limit-from-json | `test_a_malformed_group_declaration_is_refused_by_the_resource_intake`・`test_defsystem_form.hy` | 規則を写した 2 つ目の関数を書く事を止める静的な規則は無い |
| 宣言に無い組を断る(R3) | 受け口の 2 か所で `limits-undeclared-refusal` | `cluster_policy.hy` の submit-task・`detached_policy.hy` の submit-detached | 同上の検 | 3 つ目の受け口を足す時に呼び忘れを止める規則は無い |
| 宣言の値で数える(R3) | 升の鍵と比べる値を宣言から引く(`limit-slot`・`task-misfit` が GroupLimit を受ける) | `cluster_policy.hy` | `test_two_senders_naming_one_group_are_counted_by_the_one_declared_value`・`test_the_per_part_group_counts_each_caller_by_the_declared_value`・`tests/test_task_priority_limits.hy` | — |
| /state に出す(R4) | 画面の型 GroupLimitView・GroupHeld と綴り | `cluster_model.hy`・`cluster_policy.hy` の group-limit-views・`replies.hy` | `test_the_state_shows_each_group_declared_value_and_held_count_on_sim` | — |
| defsystem の形は静的 | 展開の時の SyntaxError(read-task-limits) | `doeff-hy/src/doeff_hy/system_form.hy` | `test_defsystem_form.hy` の 2 本 | — |
| module の依存の向き | code-quality の module の契約の allow | `.agents/code-quality.d/doeff-cluster-1541.json` | code-quality の fast の段(commit の前の手の実行・日次) | 取り込みの処理は走らせない |
| 業務の語を持たない | semgrep `doeff-packages-have-no-application-vocabulary` | `.semgrep.yaml` | commit の hook・日次 | — |

検査の実行の命令: `uv run pytest packages/doeff-cluster/tests/test_group_limits_declared.hy packages/doeff-cluster/tests/test_task_priority_limits.hy packages/doeff-cluster/tests/test_defsystem_form.hy`(doeff の根で)。
