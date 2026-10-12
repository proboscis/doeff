# 盲検の前の主張 — worker の沈黙の長さを、壁の時計の差と単調な時計の差の長い方で計る(依頼 lt-ZFAZXE88D928C47X6NZKRY3AQ2・card ki-069f4a5c07f7)

## この文書の目的

盲検 A・B を起こす前に、設計者の主張・前提・予想する波及の範囲・強制方法を固定する。盲検の後に結論を書き換えない
(結論と実測は design.md に書く)。

## 完了の範囲と時系列

- 範囲は実装完了まで。直しは盲検の前に main へ着地している(doeff L2298・a5384be88 = 赤の検・0d3c814fc = 直し。使い手の
  agora-controllers は L6631・1a198d15 で doeff の最小 commit と歩数の宣言 10 本を動かした)。
- この設計検証は、終わりの検査が求めたので実装の後に行う(順が逆である事を記録する)。基準版は a1f95601a(origin/main・直しを含む)。
  その上に、この検証で足した強制(semgrep の規則と検体と検)を載せた版を盲検に渡す。
- 実装の前に盲検でないレビューは行っていない。

## 要件(固定)

依頼の文(逐語): 「最初に、壁の時計を戻した時に 2 か所で走る時間帯ができる事を、模擬の失敗ケースで再現する。再現したら、案(max)を
C4 の余白と suspend の扱いを含めて設計し、last-ok の持ち方・壁の DueAt への写し・sim・検・消費者の歩数の宣言を同じ変更で動かす。」

利用者に見える結果: 機体の壁の時計が戻っても、同じ job(ターン)が 2 か所で走らない。

配り役の作りの決め(card の発言): (1) 型は既存の silent-ms(整数 ms)。新しい型は足さない。(2) 実例は coordinator_link.hy の
呼び出し側。(3) silent-ms を作る関数を 1 つだけ置き、呼び出しと sim の宿がそこから受ける。max はその関数の中だけに書く。

再現(直す前の版 2641decc8 の上・本番の口の台本): 途絶の最中に壁の時計を 9 秒戻すと自己停止の判断が沈黙 29,001 ms(戻さない時
20,001 ms)、45 秒戻すと 65,001 ms(移し替えの 60 秒の後)、途絶なしで 70 秒戻すと heartbeat の間が 72,500 ms。

## 利用の状況

- worker(機体ごとの process)は coordinator へ heartbeat を送り、返事で宣言(動かす job)を受ける。coordinator は自分の時計で
  worker の沈黙を数え、移し替え(ClusterTiming.reassign-after-ms = 60 秒)を越えたら job を他の worker へ置く。
- worker は coordinator に届かない沈黙が fence(20 秒)を越えたら、自分の job を自分で止める(途絶の柵)。条 C4
  timing-outlasts-the-self-stop: 移し替え 60 秒 ≥ fence 20 + 返事の上限 15 + 接続の上限 2 + 停止の猶予 15(余白 8 秒)。条 C2
  one-place-per-job: 同じ job は 1 か所でだけ走る。
- 時計の読みは effect(doeff-time の GetTime = 壁の時計・GetMonotonic = 単調な時計・Delay)。本番は入口が並べる handler が機体の
  時計で答え、検と模擬は仮想の時計(SimClock・sim-time-handler)が答える。
- 模擬(sim/local.hy の sim-cluster)は本物の coordinator と worker の調整ループを仮想の時計で回す。worker の機体の側(宿
  sim-host)は、本番の coordinator への口と同じ判断を呼ぶ別の実装。

## 責務と公開の契約

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- |
| silence-measure | 沈黙の長さを 2 つの時計の読みから計る(worker/core/heartbeat_rules.hy の silent-ms) | 壁の差と単調な差の長い方を採る事・どの時計がどの乱れ(壁の戻り・suspend・壁の進み)を守るか | 式(max) | `(silent-ms now-ms now-mono-ms since-ms since-mono-ms) -> int`(ms・どれも int) |
| silence-judgments | 沈黙の長さから判断する(heartbeat_rules.hy の desired-after-silence・desired-when-unreachable、beat_policy.hy の heartbeat-due、policy.hy の kept-when-cut-off?) | 閾(fence・keep-fence・間隔)との比べ方 | 比べの向きと境 | 引数 `silent-ms`(int)を受ける純粋な判断 |
| link-anchors | 本番の coordinator への口が沈黙の起点を持つ(worker/protocol/coordinator_link.hy の LinkState・BeatCell・link-silence・beat・sent-beat・beat-beside-loop・polled・WorkerWakes の節) | 起点の欄(last-ok-ms と last-ok-mono-ms・started-at と started-mono-ms)・いつ書くか(heartbeat の返事が届いた時・送りを始めた時)・時計を読む所・起点を今の壁の時計の枠へ写す事(今の壁 − silent-ms の答え) | 欄の名と書く順・HTTP の送り方 | effect ReadDesired・Heartbeat・WorkerWakes の handler(coordinator-link)・`(link-silence state now-ms) -> int` |
| worker-due | 時刻だけで判断が変わる刻を出す(worker/core/worker_due.hy の beat-due・fence-due) | 起点 + 閾(+1 ms の境)= 期限 | 境の作り方 | `(beat-due now last-ok-ms interval-ms)`・`(fence-due now last-ok-ms fence-ms keep-fence-ms)` — last-ok-ms は今の壁の時計の枠へ写した刻 |
| tick-program | 調整ループの 1 周と、周の頭の刻を起きる期限の問いへ運ぶ(worker/core/program.hy・worker/intent/worker_model.hy の effect WorkerWakes) | 周の形・周の頭の刻の対(壁と単調) | 周の中の順 | effect `WorkerWakes [began began-mono-ms]` |
| worker-entry | 本番の組み立て(worker/entry/main.hy) | どの時計の handler を並べるか・起動の刻(最初の起点)の読み | handler の並び | `main` |
| sim-host | 模擬の worker の機体の側(sim/local.hy の HostTruth・heartbeat の節・ReadDesired の節・host-wakes・fresh-truth) | 模擬の起点の欄(last-ok-ms・last-ok-mono-ms)・本番の口と同じ判断をどこで呼ぶか | 欄と宿の節の形 | sim-host の handler(本番と同じ effect に答える) |
| sim-machine-wall | 模擬の機体ごとの壁の時計(sim/local.hy の SimWorker.steps-wall・handler worker-wall・effect StepWorkerWall・WallBackOf・世界の wall-backs) | 機体ごとのずれの持ち方と、GetTime の答えへの当て方 | ずれの表・並べる位置 | 検の effect `StepWorkerWall [name seconds]`・`SimWorker :steps-wall` |
| timing-contract | 時間の不変条件 C4 と値(shared/core/timing_rules.hy・shared/intent/protocol.hy の ClusterTiming) | 移し替え ≥ fence + 止め切りの内訳・前提「worker の数える沈黙は coordinator の数える沈黙を下回らない」 | 内訳の和 | `timing-checked`・ClusterTiming の欄 |
| anchor-rule | 起点の読み書きの形を止める静的な規則(.semgrep.yaml の doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure・検体・tests/semgrep の検 2 本) | 許す形(silent-ms の 3・4 番目に同じ持ち主の対・対を隣り合う 2 組で書く・在るかどうかの問い) | 正規表現 | commit の hook(semgrep-hy)と `make lint-semgrep` |
| silence-contract-tests | 沈黙の計りの契約の検(packages/doeff-cluster/tests の test_silence_wall_clock_step.hy 8 本・test_wall_clock_step_keeps_one_place.hy 5 本・test_worker_wakes.hy・test_sim_host_wakes.hy) | 台本(壁を戻す・進める・suspend・途絶)と読む値 | 台本の道具 | pytest の検 |

共同の不変条件(一緒に守る — 分けて変えられない):

- J1: 起点は、壁の時計と単調な時計を同じ所で続けて読んだ対で書く(片方だけ進めない)。
- J2: 模擬の宿(sim-host)は本番の口(link-anchors)と同じ計り(silent-ms)を呼ぶ。本番の口が沈黙の使い方を変えたら、宿も同じ変更で動かす。
- J3: worker の 1 周の effect の数は、使い手(agora-controllers)の歩数の宣言の入力。周の中の effect の数を変える変更は、使い手の宣言を
  同じ着地の列で測り直す。
- J4: 入口の中で、起動の刻の単調な読みと調整ループの単調な読みは同じ源の時計。

範囲の外(この設計は持たない): coordinator の側が数える worker の沈黙(last-seen — coordinator/core の別の持ち主)・子 process の
止めの猶予などの、coordinator との沈黙でない時間の比べ。

## 変更シナリオと主張(盲検の前)

### S1(hardware)単調な時計の源を、機体の眠りの間も進む時計に替える

- 要求: suspend する機体(laptop・一時停止する VM)に worker を置く運用で、単調な時計を Linux の CLOCK_BOOTTIME 相当に替える。
- 変える: worker-entry だけ(GetMonotonic に答える handler と、起動の刻の読み — J4 で同じ源にする)。
- 変えない: silence-measure(max のまま)・silence-judgments・link-anchors・worker-due・tick-program・sim-host・sim-machine-wall・
  timing-contract。
- 収まる理由: 起点の持ち主は時計を effect で読み、源を知らない。silent-ms は 2 つの読みの差を取るだけで、単調な時計が眠りを数えるか
  どうかに依らない(数えるなら 2 つの差が揃う)。
- 前提: 替えた時計は戻らず、worker の process の間で比べられる。

### S2(storage)不適用

起点は worker の process の記憶だけに在り(LinkState・HostTruth)、保存しない。worker が起き直すと新しい世代(boot)で名乗り、起動の
刻を起点に数え直す。単調な時計の読みは機体の再起動をまたいで意味を保たないので、保存の方法を替える要求の相手が無い。

### S3(effects)自己停止の記録に、どちらの時計が沈黙を決めたかを載せる

- 要求: 壁の時計が進むと偽の自己停止が起きる(設計の代償)。運用が訳を読めるよう、途絶の絞り(CutOff)を返す時の log に、壁の差と
  単調な差の両方を載せる。
- 変える: silence-measure(2 つの差を返す関数を足す — max は silent-ms の中のまま)・link-anchors(log を出す)・anchor-rule(許す
  読み手の名に足した関数を加える)。sim-host は log を写さないなら変えない。
- 変えない: silence-judgments・worker-due・tick-program・worker-entry・sim-machine-wall・timing-contract。silent-ms の署名と答え。
- 収まる理由: 起点を直に引く読みは規則が止めるので、差を作る所は silence-measure に集まる。判断は int の silent-ms だけを受ける。
- 前提: 型は足さない(配り役の決め)。log は判断に使わない。

### S4(distribution)不適用

沈黙の計りは worker 1 台の中の 2 つの時計の読みだけを比べ、機体をまたぐ時計の比べを持たない(coordinator は自分の時計で last-seen
を数える)。worker や coordinator の数を増やす変更は、この計りの入力(自分の機体の時計の読みと自分の起点)を変えない。coordinator の
側の沈黙の計りは別の持ち主で、この依頼の範囲の外。

### S5(concurrency)今の沈黙の長さを、別の task が定期に読む

- 要求: worker の計器(metrics)に今の沈黙の長さを出す。調整ループの周とは別の task が定期に読む。
- 変える: 新しい読み手(計器の task)だけ — link-anchors の `link-silence` を呼ぶ。
- 変えない: silence-measure・silence-judgments・link-anchors・worker-due・tick-program・sim-host・timing-contract。
- 収まる理由: 起点は 1 つの setv で対のまま書くので(J1)、別の task がいつ読んでも対は揃っている。読み手は欄を知らず link-silence の
  答え(int)だけを受ける。欄を直に読む読み手は規則が止める。
- 前提: doeff の task の切り替えは effect の所でだけ起きる(setv の途中では起きない)。

### S6(simulation)模擬で機体の suspend を起こす

- 要求: 模擬の世界で、worker の機体が眠る(単調な時計が進まず、壁の時計だけ進む)筋書きを書けるようにする。今は壁の時計だけを
  動かせる(StepWorkerWall)。
- 変える: sim-machine-wall だけ(機体ごとの単調な時計のずれを世界が持ち、機体の時計の代役 worker-wall が GetMonotonic にも答える・
  検の effect を足す)。
- 変えない: sim-host(判断の写し)・silence-measure・link-anchors・worker-due・tick-program・timing-contract。
- 収まる理由: 宿は時計を effect で読み、機体の時計の代役は宿の外側に並ぶ。宿は「どの時計が動いたか」を知らない。
- 前提: 眠っている間に調整ループが回らない事は、既に在る検の effect(StallWorker)で作る。

## 強制方法(盲検の前に固定)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 沈黙の長さは silence-measure だけが作る(起点を直に引かない・生の起点を期限へ渡さない・2 つの引数を入れ替えない)。J1(対で書く) | semgrep の規則 doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure(generic・ERROR)。起点の欄の名の読み書きを全部拾い、許す形(silent-ms の 3・4 番目に同じ持ち主の対・隣り合う 2 組の書き・鍵の対・`(is-not x.started-at None)`)だけを外す。型を足さない決めの下で、int の引数の取り違えを止める道が規則しか無い | .semgrep.yaml・検体 tests/semgrep/fixtures/python/packages/doeff-cluster/src/doeff_cluster/worker/protocol/silence_anchor_read_outside_measure_forbidden.hy・検 tests/semgrep/test_vm_failfast_semgrep_rules.py の test_silence_anchor_rule_detects_reads_outside_the_one_measure と test_silence_anchors_are_read_only_by_the_one_measure_in_the_tree | commit の hook(.pre-commit-config.yaml の semgrep-hy — stage した .hy)・`make lint-semgrep`・日次の全体の回 | 欄の名で見るので、別の名の新しい起点(例: 新しい欄 last-heard-ms)を壁の差だけで計る実装は拾わない。周の頭の刻(began・began-mono-ms)は局所の名なので見ない。`(f a.last-ok-ms x a.last-ok-mono-ms y)` の形の読みは書きと見分けない |
| 計りの式(長い方)と、口・宿・期限がそれを使う事 | 契約の検(台本: 壁を 9 秒・45 秒・70 秒戻す・5 秒進める・suspend 30 秒・模擬の世界で 45 秒戻して移し替え先と重ならない)。式を壁だけ・単調だけに替えると赤になる | packages/doeff-cluster/tests/test_silence_wall_clock_step.hy・test_wall_clock_step_keeps_one_place.hy・test_worker_wakes.hy・test_sim_host_wakes.hy | `make test-changed`(変えた file を import する検を逆依存で選ぶ — doeff-linter が要る)・日次の全体の回 | 新しい判断が silent-ms を通らずに時間を比べる形は、その判断の検が無ければ拾わない |
| silent-ms の引数と答えは int・判断は int の silent-ms を受ける | defk の :pre / :post(実行時の契約)・LinkState と WorkerWakes と HostTruth の必須の欄(欠けると作る所で落ちる) | heartbeat_rules.hy・coordinator_link.hy・worker_model.hy(.pyi)・sim/local.hy(.pyi) | 検の実行・本番の実行 | 引数の意味(壁か単調か)は型で分けない — 規則で補う |
| C4 の値の関係 | 起動の検め timing-checked と条の検 tests/test_cluster_timing.hy | shared/core/timing_rules.hy | worker の起動・検 | 沈黙の計りの前提は註と契約の検が持つ |

検討して採らなかった物: 起点の型(壁と単調の対を 1 つの record にする)は配り役の決め(新しい型は足さない)で採らない。import の
向きの検査は、silent-ms が worker/core に在り、口(worker/protocol)と模擬(sim)が core を読む既存の向きのままなので足さない。
