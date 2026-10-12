# 盲検の入力 — doeff-cluster の worker が、coordinator との沈黙の長さを 2 つの時計で計る形

## 対象

- repo: doeff。対象版: この作業木の HEAD。git の歴史と commit の文、docs/design-checks/ の下は読まないでください。
- 対象の code(packages/doeff-cluster/src/doeff_cluster/ の下):
  - worker/core/heartbeat_rules.hy(silent-ms・desired-after-silence・desired-when-unreachable)
  - worker/core/beat_policy.hy(heartbeat-due)・worker/core/policy.hy(kept-when-cut-off?)・worker/core/worker_due.hy(beat-due・fence-due)
  - worker/protocol/coordinator_link.hy(LinkState・BeatCell・link-silence・beat・sent-beat・beat-beside-loop・polled・WorkerWakes の節)
  - worker/core/program.hy(worker-tick・run-worker)・worker/intent/worker_model.hy(effect WorkerWakes)
  - worker/entry/main.hy(起動の刻の読みと handler の組)
  - sim/local.hy(HostTruth・fresh-truth・heartbeat の節・ReadDesired の節・host-wakes・SimWorker.steps-wall・worker-wall・
    StepWorkerWall・WallBackOf)
  - shared/core/timing_rules.hy・shared/intent/protocol.hy(ClusterTiming・条 C4)・shared/core/clock.hy(now-epoch-ms・monotonic-ms-of)
- 検査の実体: .semgrep.yaml の規則 doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure、検体
  tests/semgrep/fixtures/python/packages/doeff-cluster/src/doeff_cluster/worker/protocol/silence_anchor_read_outside_measure_forbidden.hy、
  規則の検 tests/semgrep/test_vm_failfast_semgrep_rules.py、契約の検 packages/doeff-cluster/tests/ の
  test_silence_wall_clock_step.hy・test_wall_clock_step_keeps_one_place.hy・test_worker_wakes.hy・test_sim_host_wakes.hy。

## 要件(固定)

機体の壁の時計が戻っても、同じ job が 2 か所で走らない。worker が coordinator に届かない沈黙を数えて自分の job を止める判断
(途絶の柵)と、heartbeat を送る間隔が、機体の壁の時計の step(NTP の修正・手での変更)と機体の suspend で、実際に過ぎた時間を
下回る沈黙を読まない。

作りの決め(固定): 判断に渡す沈黙の型は整数 ms の silent-ms のまま(新しい型は足さない)。silent-ms を作る関数は 1 つだけ置き、
本番の口と模擬の宿はそこから受ける。長い方を採る式(max)はその関数の中だけに書く。

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
- 沈黙の長さ = max(壁の時計の差, 単調な時計の差)。壁が戻る → 単調な差が守る。機体の suspend(単調な時計 CLOCK_MONOTONIC は
  眠りを数えない)→ 壁の差が拾う。壁が進む → suspend と見分けられないので早く止まる(代償は偽の自己停止)。
- 時刻だけで判断が変わる刻(期限 DueAt)は壁の時計の枠の刻で出す。起点は「今の壁の時計 − silent-ms の答え」で今の壁の枠へ写して
  から、期限の関数(beat-due・fence-due)へ渡す。

## 責務と公開の契約

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- |
| silence-measure | 沈黙の長さを 2 つの時計の読みから計る(worker/core/heartbeat_rules.hy の silent-ms) | 壁の差と単調な差の長い方を採る事・どの時計がどの乱れを守るか | 式(max) | `(silent-ms now-ms now-mono-ms since-ms since-mono-ms) -> int`(ms・どれも int) |
| silence-judgments | 沈黙の長さから判断する(heartbeat_rules.hy の desired-after-silence・desired-when-unreachable、beat_policy.hy の heartbeat-due、policy.hy の kept-when-cut-off?) | 閾(fence・keep-fence・間隔)との比べ方 | 比べの向きと境 | 引数 `silent-ms`(int)を受ける純粋な判断 |
| link-anchors | 本番の coordinator への口が沈黙の起点を持つ(worker/protocol/coordinator_link.hy) | 起点の欄(last-ok-ms と last-ok-mono-ms・started-at と started-mono-ms)・いつ書くか(heartbeat の返事が届いた時・送りを始めた時)・時計を読む所・起点を今の壁の時計の枠へ写す事 | 欄の名と書く順・HTTP の送り方 | effect ReadDesired・Heartbeat・WorkerWakes の handler(coordinator-link)・`(link-silence state now-ms) -> int` |
| worker-due | 時刻だけで判断が変わる刻を出す(worker/core/worker_due.hy の beat-due・fence-due) | 起点 + 閾(+1 ms の境)= 期限 | 境の作り方 | `(beat-due now last-ok-ms interval-ms)`・`(fence-due now last-ok-ms fence-ms keep-fence-ms)` — last-ok-ms は今の壁の時計の枠へ写した刻 |
| tick-program | 調整ループの 1 周と、周の頭の刻を起きる期限の問いへ運ぶ(worker/core/program.hy・worker_model.hy の effect WorkerWakes) | 周の形・周の頭の刻の対(壁と単調) | 周の中の順 | effect `WorkerWakes [began began-mono-ms]` |
| worker-entry | 本番の組み立て(worker/entry/main.hy) | どの時計の handler を並べるか・起動の刻(最初の起点)の読み | handler の並び | `main` |
| sim-host | 模擬の worker の機体の側(sim/local.hy の HostTruth・heartbeat の節・ReadDesired の節・host-wakes・fresh-truth) | 模擬の起点の欄・本番の口と同じ判断をどこで呼ぶか | 欄と宿の節の形 | sim-host の handler(本番と同じ effect に答える) |
| sim-machine-wall | 模擬の機体ごとの壁の時計(sim/local.hy の SimWorker.steps-wall・handler worker-wall・effect StepWorkerWall・WallBackOf・世界の wall-backs) | 機体ごとのずれの持ち方と、GetTime の答えへの当て方 | ずれの表・並べる位置 | 検の effect `StepWorkerWall [name seconds]`・`SimWorker :steps-wall` |
| timing-contract | 時間の不変条件 C4 と値(shared/core/timing_rules.hy・ClusterTiming) | 移し替え ≥ fence + 止め切りの内訳・前提「worker の数える沈黙は coordinator の数える沈黙を下回らない」 | 内訳の和 | `timing-checked`・ClusterTiming の欄 |
| anchor-rule | 起点の読み書きの形を止める静的な規則(.semgrep.yaml・検体・tests/semgrep の検 2 本) | 許す形 | 正規表現 | commit の hook(semgrep-hy)と `make lint-semgrep` |
| silence-contract-tests | 沈黙の計りの契約の検(packages/doeff-cluster/tests の 4 file) | 台本と読む値 | 台本の道具 | pytest の検 |

共同の不変条件(一緒に守る):

- J1: 起点は、壁の時計と単調な時計を同じ所で続けて読んだ対で書く(片方だけ進めない)。
- J2: 模擬の宿(sim-host)は本番の口(link-anchors)と同じ計り(silent-ms)を呼ぶ。本番の口が沈黙の使い方を変えたら、宿も同じ変更で動かす。
- J3: worker の 1 周の effect の数は、doeff-cluster の使い手の検の歩数の宣言の入力。周の中の effect の数を変える変更は、使い手の
  宣言を同じ着地で測り直す。
- J4: 入口の中で、起動の刻の単調な読みと調整ループの単調な読みは同じ源の時計。

範囲の外(この設計は持たない): coordinator の側が数える worker の沈黙(last-seen — coordinator/core の別の持ち主)・子 process の
止めの猶予などの、coordinator との沈黙でない時間の比べ。

## 将来の変更についての主張と前提

| id | 軸 | 変更の要求 | 変える | 変えない | 収まる理由 | 前提 |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | hardware | 単調な時計の源を、機体の眠りの間も進む時計(CLOCK_BOOTTIME 相当)に替える | worker-entry だけ(GetMonotonic に答える handler と起動の刻の読み — J4) | silence-measure・silence-judgments・link-anchors・worker-due・tick-program・sim-host・sim-machine-wall・timing-contract | 起点の持ち主は時計を effect で読み、源を知らない。silent-ms は 2 つの読みの差を取るだけ | 替えた時計は戻らず、worker の process の間で比べられる |
| S2 | storage | (不適用)起点は worker の process の記憶だけに在り、保存しない。worker が起き直すと新しい世代で名乗り、起動の刻から数え直す | — | — | — | — |
| S3 | effects | 自己停止の記録(途絶の絞りを返す時の log)に、壁の差と単調な差の両方を載せる | silence-measure(2 つの差を返す関数を足す — max は silent-ms の中のまま)・link-anchors(log を出す)・anchor-rule(許す読み手の名に足した関数を加える) | silence-judgments・worker-due・tick-program・worker-entry・sim-machine-wall・timing-contract・silent-ms の署名と答え | 起点を直に引く読みは規則が止めるので、差を作る所は silence-measure に集まる | 型は足さない。log は判断に使わない |
| S4 | distribution | (不適用)沈黙の計りは worker 1 台の中の 2 つの時計の読みだけを比べ、機体をまたぐ時計の比べを持たない。worker や coordinator の数を増やしても入力が変わらない | — | — | — | — |
| S5 | concurrency | worker の計器に今の沈黙の長さを出す — 調整ループの周とは別の task が定期に読む | 新しい読み手(計器の task)だけ — link-silence を呼ぶ | silence-measure・silence-judgments・link-anchors・worker-due・tick-program・sim-host・timing-contract | 起点は 1 つの setv で対のまま書く(J1)ので、別の task がいつ読んでも対は揃う。読み手は link-silence の答え(int)だけを受け、欄を直に読む読み手は規則が止める | doeff の task の切り替えは effect の所でだけ起きる |
| S6 | simulation | 模擬の世界で、worker の機体が眠る(単調な時計が進まず壁の時計だけ進む)筋書きを書けるようにする | sim-machine-wall だけ(機体ごとの単調な時計のずれを世界が持ち、worker-wall が GetMonotonic にも答える・検の effect を足す) | sim-host・silence-measure・link-anchors・worker-due・tick-program・timing-contract | 宿は時計を effect で読み、機体の時計の代役は宿の外側に並ぶ | 眠っている間に調整ループが回らない事は、既に在る検の effect(StallWorker)で作る |

## 強制方法と検査

| 守る責務 | 強制方法 | 限界(設計者が知っている範囲) |
| --- | --- | --- |
| 沈黙の長さは silence-measure だけが作る(起点を直に引かない・生の起点を期限へ渡さない・2 つの引数を入れ替えない)・J1 | semgrep の規則 doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure。起点の欄の名の読み書きを全部拾い、許す形(silent-ms の 3・4 番目に同じ持ち主の対・隣り合う 2 組の書き・鍵の対・`(is-not x.started-at None)`)だけを外す | 欄の名で見る。周の頭の刻(began・began-mono-ms)は局所の名なので見ない |
| 計りの式(長い方)と、口・宿・期限がそれを使う事 | 契約の検 4 file(台本: 壁を 9 秒・45 秒・70 秒戻す・5 秒進める・suspend 30 秒・模擬の世界で 45 秒戻す) | — |
| silent-ms の引数と答えは int | defk の :pre / :post(実行時の契約)・LinkState と WorkerWakes と HostTruth の必須の欄 | 引数の意味(壁か単調か)は型で分けない |
| C4 の値の関係 | 起動の検め timing-checked と条の検 packages/doeff-cluster/tests/test_cluster_timing.hy | — |

検査の実行(この作業木で・どれも前で実行できる):

```
# semgrep の規則(commit の hook と同じ呼び方 — stage した .hy に当たる。file を名指して当てる)
uv run --no-project --python 3.12 python scripts/semgrep_locked.py --metrics=off --disable-version-check \
  --project-root . --no-git-ignore --config .semgrep.yaml --error <file…>

# 規則の検(検体と実の木)
uv run pytest tests/semgrep/test_vm_failfast_semgrep_rules.py -k silence_anchor -q

# 契約の検(packages/doeff-cluster で・1 file ずつ。4 file で約 1 分)
cd packages/doeff-cluster && uv run pytest tests/test_silence_wall_clock_step.hy -q
cd packages/doeff-cluster && uv run pytest tests/test_wall_clock_step_keeps_one_place.hy -q
cd packages/doeff-cluster && uv run pytest tests/test_worker_wakes.hy tests/test_sim_host_wakes.hy -q
```

この機体に無い検査: doeff-linter(定義の書き方・層の規則)の binary は置いていない。linter の結果は提示していない検査として扱って
ください(推測しない)。package 全体の検・repo 全体の検は走らせないでください(1 回の pytest は名指した file だけ)。

作業の決まり: この作業木の source は書き換えてよい(使い捨て)が、commit・push はしないでください。作業 file は指定の /tmp の dir に
置いてください。他の作業木・共有の checkout・他の会話には触れないでください。
