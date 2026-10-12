# 設計検証 — worker の沈黙の長さを、壁の時計の差と単調な時計の差の長い方で計る(依頼 lt-ZFAZXE88D928C47X6NZKRY3AQ2・card ki-069f4a5c07f7)

## 1. 完了の範囲と時系列

- 範囲は実装完了まで。最初の直しは盲検の前に main へ着地した(doeff L2298・a5384be88 = 赤の検・0d3c814fc = 直し)。この設計検証は
  終わりの検査が求めたので実装の後に行った(順が逆である事を記録する)。基準版は a1f95601a(origin/main・最初の直しを含む)。
- 盲検の前の主張と強制方法は design-pre-blind.md(sha256 ec89e305…・0d4c5e9f7 で固定)。盲検の入力は blind-input.md(300906b9…)。
  起動の記録は evidence/blind-meta.md、返答は evidence/blind-a-reply.md・blind-b-reply.md(加工していない)。
- 盲検 A・B はどちらも成立する失敗例を返した。再現し、境界と検査を直して測り直した(この文書の 4 節)。直しは 3 つの commit:
  0d4c5e9f7(起点の規則)・1d772f3e9(周の間の待ちの直し・時計の effect の規則)・d25e8a25e(拍の頭の刻の直し)。
- 予定のまま残る物は無い。測っていない物は 8 節。

## 2. 要件(固定)

機体の壁の時計が戻っても、同じ job(ターン)が 2 か所で走らない。worker が coordinator に届かない沈黙を数えて自分の job を止める
判断(途絶の柵)と heartbeat を送る間隔が、機体の壁の時計の step と機体の suspend で、実際に過ぎた時間を下回る沈黙を読まない。

作りの決め(配り役・固定): 判断に渡す沈黙の型は整数 ms の silent-ms のまま(新しい型は足さない)。silent-ms を作る関数は 1 つだけ
置き、本番の口と模擬の宿はそこから受ける。長い方を採る式(max)はその関数の中だけに書く。

## 3. 責務と公開の契約(検証の後の形)

| id | 責務 | 公開の形 | 盲検の前との差 |
| --- | --- | --- | --- |
| silence-measure | 沈黙の長さ(起点から今までの ms)を 2 つの時計の読みから計る(worker/core/heartbeat_rules.hy の silent-ms) | `(silent-ms now-ms now-mono-ms since-ms since-mono-ms) -> int` | 註に時計の前提(GetTime = 眠りの間も進む生の壁・GetMonotonic = 戻らない)と、周の間の待ちも使う事を足した |
| silence-judgments | 沈黙の長さから判断する(desired-after-silence・desired-when-unreachable・heartbeat-due・kept-when-cut-off?) | 引数 `silent-ms`(int) | 変えていない |
| link-anchors | 本番の口が沈黙の起点を持ち、期限を今の壁の時計の枠へ写して出す(worker/protocol/coordinator_link.hy) | handler coordinator-link・`(link-silence state now-ms) -> int` | 変えていない |
| worker-due | 時刻だけで判断が変わる刻を出す(worker/core/worker_due.hy) | `beat-due`・`fence-due` | 変えていない |
| tick-program | 調整ループの 1 周。周の頭の刻の対を、起きる期限の問いと周の間の待ちへ運ぶ(worker/core/program.hy・worker_model.hy) | effect `WorkerWakes [began began-mono-ms]`・`AwaitNextTick [policy changed wakes began began-mono-ms stopping]` | 周の頭で壁の時計と単調な時計を続けて読み、判断の時の枠へ silent-ms で写す。AwaitNextTick に対を足した(盲検 A) |
| tick-pauses | 周の間の待ち。期限(壁の枠の刻)を、周の頭の刻の対からの経過で待ちの長さにする(worker/protocol/tick_pauses.hy) | handler tick-pauses(AwaitNextTick に答える) | **盲検の前の表に無かった**。「期限 − 今の壁の時計」で待っていた(盲検 A)。silent-ms の 3 人目の呼び手になった |
| worker-entry | 本番の組み立て(worker/entry/main.hy)。時計の答え手を引数なしで並べる | `main` | 変えていない(並べ方を規則が守る — 盲検 B) |
| sim-host | 模擬の worker の機体の側(sim/local.hy の HostTruth・宿の節・host-wakes) | sim-host の handler | 変えていない |
| sim-machine-wall | 模擬の機体ごとの壁の時計(sim/local.hy の steps-wall・worker-wall・StepWorkerWall・WallBackOf) | 検の effect `StepWorkerWall` | 変えていない |
| timing-contract | 時間の不変条件 C4 と値(shared/core/timing_rules.hy・ClusterTiming) | `timing-checked` | 変えていない |
| anchor-rule | 起点の読み書きの形を止める規則(.semgrep.yaml の doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure) | commit の hook・`make lint-semgrep` | 今の刻の引数に起点を渡す形・起点を読み直してずらす書きを外さないよう締めた(盲検 B の副次の観測) |
| clock-rule | 時計の effect に答える handler と、時計の答え手へ源を渡す形を止める規則(doeff-cluster-clock-effects-are-answered-by-the-default-time-handlers) | commit の hook・`make lint-semgrep` | **新設**(盲検 B) |
| silence-contract-tests | 沈黙の計りの契約の検(packages/doeff-cluster/tests) | pytest の検 | 周の間の待ち(3 本)・待ちの直前の壁の戻り(2 本)・返事の処理の途中の眠り(1 本)を足した |

共同の不変条件: J1(起点は対で書く)・J2(模擬の宿は本番の口と同じ計りを呼ぶ)・J3(1 周の effect の数は使い手の歩数の宣言の入力)・
J4(入口の単調な読みは同じ源)に、J5 を足した: **GetTime は機体の眠りの間も進む生の壁の時計・GetMonotonic は壁の step に動かされない
戻らない時計**(silent-ms の前提 — clock-rule が守る)。

## 4. 盲検の失敗例と、再現・修正

### 4.1 盲検 A(主): 期限を組み立てた後・待ちが時計を読む前に壁の時計が戻る

- 主張: 起きる期限(DueAt)は口が読んだ今の壁の時計の枠の刻。周の間の待ち(tick-pauses — 表に無かった module)は自分で壁の時計を
  読み直して「期限 − 今」で待つ。2 つの読みの間に壁が戻ると、待ちが戻った分だけ延び、柵の判断がその分遅れる。
- 再現(evidence/repro-a-before.log・9d603fa5c): 3 failed・1 passed。沈黙 19.5 秒の待ちの直前に 9 秒戻すと自己停止の判断が沈黙
  29,000 ms(余白 8 秒を越える)、途絶の最初の待ちの直前に 45 秒戻すと 48,000 ms、沈黙 19.5 秒の待ちの直前に 45 秒戻すと
  **65,000 ms(移し替えの 60,000 ms の後)**。対照は 20,001 ms。成立。
- 分類: 隠すはずの知識の漏洩(「DueAt がどの今の枠の刻か」を口と待ちが暗黙に共有していた)と、設計者の module の表の漏れ。
- 修正(1d772f3e9): 待ちの長さ = 期限 −(周の頭の刻 + そこからの経過)。経過は周の頭の刻の対から silent-ms で数える。調整ループが
  対を AwaitNextTick で渡す。口と模擬の宿は変えていない(A の試作は口と effect 2 つを変えたが、待ちの側だけで閉じる形にした)。
  性質: 待ちは「期限 − 今の壁の時計」より長くならない(壁が進んだ・機体が眠った時は今までどおり早く起きる)。壁が判断の後・口の読みの
  前に戻った時は、戻った分だけ早く起きる(余分な 1 周だけで、判断は変わらない)。
- 測り直し: A の検が 4 本とも 20,001 ms(evidence/repro-a-after.log・d25e8a25e)。待ちを元の式へ戻す変異で足した検が 3 本赤
  (evidence/a-fix-mutation.log)。

### 4.2 盲検 A(副次): 拍の途中(返事の処理の間)に機体が眠る

- 主張: 調整ループは拍の頭の壁の刻を「判断の now − 単調な差」で導く。口が返事の刻を読んだ後・調整ループが壁の時計を読む前に
  機体が眠ると、拍の頭が眠りの後へずれ、口は眠りの間に過ぎた期限を「もう判じた」として捨てる。
- 再現(evidence/a-secondary-before.log・1d772f3e9): 拍の外の送りが無い口(beat-beside 偽)で、自己停止の判断が **215,001 ms**
  (対照 5,000 ms)。本番の既定(beat-beside 真)では 5,000 ms。本番でも背景の送りの task が失敗して止まった後は同じ形になる
  (coordinator_link.hy の beating.failure — 読んだだけで、実機では測っていない)ので、直した。
- 分類: 設計の原則(単調な差だけを判断に渡さない)の破りが、起点の欄の外(周の頭の刻の導き方)に残っていた。規則は欄の名で見るので
  拾わない。
- 修正(d25e8a25e): 拍の頭で壁の時計と単調な時計を続けて読み、判断の now から、頭からの長さ(silent-ms)を引いて写す。壁の時計の
  読みは拍 1 つに 2 回になる(test_tick_lag.hy の数を 1 → 2)。
- 測り直し: 4 本とも 5,000 ms(evidence/a-secondary-fixed.log)。導き方を戻す変異で足した検が赤(215,001 ms —
  evidence/a-secondary-mutation.log)。

### 4.3 盲検 B(主): 入口で壁の時計を「起動の刻 + 単調な時計の経過」に替える

- 主張: 偽の自己停止を無くす修正として、入口の handler の並びに壁の時計の代役を 1 つ足す。計り・起点・口・宿・規則は 1 文字も
  変わらず、壁の差が単調な差と同じになり、suspend を拾わない。提示した検査は全部通る。
- 再現(evidence/repro-b-before.log・0d4c5e9f7 + B の差分): semgrep rc 0・規則の検 2 passed・契約の検 8 + 5 + 12 passed・
  条の検 5 passed。その上で、届いている worker が 30 秒眠っても自己停止を **1 度も決めない**(cut-off-ms None・対照 5,000 ms)。
  届かなくなった時に 70 秒眠る台本は、最後の heartbeat からの実際の沈黙が 90,001 ms。成立。
- 分類: silent-ms の前提(壁の時計が何を数えるか)が、持ち主の外(入口の handler の並び)で変えられる。共同の不変条件が単調な
  時計(J4)にしか無かった。検は自分で handler の組を並べるので、入口の組を通らない。
- 修正(1d772f3e9): J5 を silent-ms の註に書き、clock-rule を足した — doeff_cluster の中(模擬の機体の時計 sim/local.hy を除く)で
  時計の effect に答える handler の節と、async-time-handler・sync-time-handler へ引数を渡す形を止める。
- 測り直し: B の steady_wall.hy に規則を当てて rc 1(evidence/repro-b-after.log)。検体 4 行で発火・実の木 0 件
  (evidence/final-semgrep-rules.log)。

### 4.4 盲検 B(副次): 規則の形の抜け

- 今の刻の引数に起点を渡す `(silent-ms now-ms s.last-ok-mono-ms s.last-ok-ms s.last-ok-mono-ms)` と、起点を読み直して先へずらす
  書きが発火しなかった → 規則の外す形を締め、検体に 2 行足した(38・41 行目で発火)。
- heartbeat の受け入れ以外の出来事で対を書く形(例: 断りの返事も届いたと数える)は、形の規則では止められない。「いつ書くか」は
  link-anchors の知識で、起点を書く所は beat の 1 か所と __init__。この変更は書く条件を変えていない(限界として 6 節に残す)。

## 5. 盲検の前の主張と実測

| id | 軸 | 予想した範囲 | 実測した範囲 | 差の訳 |
| --- | --- | --- | --- | --- |
| S1 | hardware | worker-entry だけ | worker-entry(時計の答え手へ源を渡す 2 行)と clock-rule(許す形) | 眠りを数える単調な時計の代役を 1 つ並べ、src を変えずに沈黙の台本 3 本が緑(s1-positive.log)— 計り・口・待ちは源を知らない。盲検 B の後、入口の時計の並びは規則が守る契約になったので、源を替える変更は規則の許す形を同じ変更で足す(s1-entry-guard.log: 2 行で発火)。意図した契約の変更で、知識の漏れではない |
| S2 | storage | 不適用 | — | 起点は process の記憶だけ |
| S3 | effects | silence-measure・link-anchors・anchor-rule | 同じ(.semgrep.yaml 1 行・heartbeat_rules.hy 15 行・coordinator_link.hy 5 行) | 予想どおり。口が起点を直に引く形は規則が 2 行で止め、持ち主に関数を足しただけでも規則が止める(許す読み手の名を足すまで)(s3-effects.log) |
| S4 | distribution | 不適用 | — | 機体をまたぐ時計の比べを持たない |
| S5 | concurrency | 新しい読み手だけ | 同じ(src の変更なし) | 別の task が 0.7 秒ごとに link-silence を呼ぶと、壁が 45 秒戻った後も 157 回の読みが全部 実際の沈黙と同じ。起点を直に引く読み手は −45,000 ms(s5-concurrency.log) |
| S6 | simulation | sim-machine-wall だけ | 変更なし(worker から見える形は今の StepWorkerWall で書ける) | **前提が誤り**: StallWorker は heartbeat を止めるだけで調整ループは回る(sim/local.hy の頭の註 — 盲検 A の指摘)。眠りの間に世界だけが進む筋書きには worker の task を止める仕組みが要り、それは世界の worker の一生の係の変更で sim-machine-wall に収まらない(実装していない・要件に要らない)。worker が眠りの後に見る形(単調は進まず壁だけ進む)は StepWorkerWall の負の秒で書け、既に検が在る(final-contract.log)。設計者の予想の誤りで、知識の漏れではない |
| SA | concurrency | (予想していない — 盲検 A) | tick-pauses・tick-program(AwaitNextTick の欄・拍の頭の読み)・silence-contract-tests | 4.1・4.2。期限の枠の知識が口と待ちに暗黙に分かれていた(漏洩)。待ちが周の頭の刻の対から数える形にして閉じた |

盲検 A が S6 について挙げた推測(世界が起き直した世代の最初の起点を世界の時計で書くので、壁と単調の両方がずれた機体では沈黙を短く
読むはず)は、単調な時計のずれを模擬に足した場合の話で、今の code には単調のずれが無い。測っていない(8 節)。

## 6. 強制方法(検証の後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 沈黙の長さは silence-measure だけが作る・J1 | semgrep の規則 doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure。起点の欄の読み書きを全部拾い、許す形だけを外す | .semgrep.yaml・検体 tests/semgrep/fixtures/python/packages/doeff-cluster/src/doeff_cluster/worker/protocol/silence_anchor_read_outside_measure_forbidden.hy(11 行で発火)・検 tests/semgrep/test_vm_failfast_semgrep_rules.py の test_silence_anchor_rule_detects_reads_outside_the_one_measure・test_silence_anchors_are_read_only_by_the_one_measure_in_the_tree | commit の hook(.pre-commit-config.yaml の semgrep-hy — stage した .hy)・`make lint-semgrep`・日次の全体の回 | 欄の名で見る: 別の名の新しい起点・局所の名(周の頭の刻の対)は見ない。いつ書くか(どの出来事を「届いた」と数えるか)は見ない |
| J5(時計の前提)・J4 | semgrep の規則 doeff-cluster-clock-effects-are-answered-by-the-default-time-handlers | .semgrep.yaml・検体 …/worker/protocol/clock_effect_answered_outside_time_handlers_forbidden.hy(4 行で発火)・検 test_clock_effect_rule_detects_answers_outside_the_time_handlers・test_clock_effects_are_answered_only_by_the_time_handlers_in_the_tree | 同上 | package の外で作った時計の答え手を入口に並べる形は拾わない(入口の差分は残る) |
| 計りの式・口と宿と待ちと調整ループがそれを使う事 | 契約の検。式を壁だけに替えると 10 本・単調だけに替えると 5 本が赤(mutation-wall-only.log・mutation-mono-only.log)。待ちの式・拍の頭の導き方を戻す変異でも赤 | packages/doeff-cluster/tests の test_silence_wall_clock_step.hy(11 本)・test_wall_clock_step_keeps_one_place.hy(5 本)・test_tick_pause_tasks.hy(4 本)・test_worker_wakes.hy・test_sim_host_wakes.hy・test_tick_lag.hy | `make test-changed`(変えた file を import する検を逆依存で選ぶ — doeff-linter が要る。会話の Pod には linter が無く、名指しで回した)・日次の全体の回 | 新しい判断が silent-ms を通らずに時間を比べる形は、その判断の検が無ければ拾わない |
| silent-ms の引数と答えは int・effect の必須の欄 | defk の :pre / :post・AwaitNextTick と WorkerWakes の必須の欄(欠けると作る所で落ちる)・.pyi | heartbeat_rules.hy・tick_pauses.hy・worker_model.hy(.pyi) | 検と本番の実行 | 引数の意味(壁か単調か)は型で分けない — 規則と検で補う(型を足さない決め) |
| C4 の値の関係 | 起動の検め timing-checked・条の検 tests/test_cluster_timing.hy | shared/core/timing_rules.hy | worker の起動・検 | — |

## 7. 使い手への波及(J3)

worker の 1 周あたりの歩数は、着地済みの直し(0d3c814fc)から +10.7(周の間の待ち +3.9・拍の頭の刻 +6.9・+1.35% — 周の数は同じ・
evidence/steps-per-tick.log)。agora-controllers(66fbc2e52)の歩数の宣言 85 本を両方の版の上で測ると、今の pin の上で既に超えている
宣言が 20 本(この変更の前から)、直した版で新しく超える宣言が 9 本(evidence/consumer-budgets.log)。9 本は doeff の最小 commit を
進める変更と同じ着地の列で測り直す。

## 8. 測っていない事・範囲の外

- repo 全体・package 全体の検と型検査、doeff-linter(会話の Pod に binary が無い)。日次の全体の回で測る。
- 本番の機体での壁の時計の step・suspend(検は仮想の時計の台本)。盲検 A の測りでは、口の読みから待ちの読みまでの窓は検の組で
  中央値 1.2 ms(本番の幅は測っていない)。
- 模擬の世界で単調な時計を機体ごとにずらす筋書き(S6 の全部の形)。
- 範囲の外(この設計は持たない): coordinator の側が数える worker の沈黙・子 process の止めの猶予(worker/core/policy.hy の止めの
  合図からの長さは壁の差だけ — 着地の報告に書いた所見)・同じ沈黙の間に suspend と壁の戻りの両方が起きる組(silent-ms の註の
  「拾えない組」)。
