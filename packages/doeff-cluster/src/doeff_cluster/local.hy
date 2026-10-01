;;; 手元の runner sim-cluster — 系(defsystem の関数を sim の土台で呼んだ System の値)を、本物の coordinator と本物の worker の上で、
;;; 1 process・仮想の時計で走らせる(ADR-DOE-CLUSTER-001・計画 2.6・10.2・段 5・5b)。同じ物を壁の時計で走らせる入口が wall-sim-cluster。
;;;
;;;   (<- answer (sim-cluster (lab sim-foundation) (scenario) :workers #((SimWorker :name "w1" :provides #{"net"})) :environ {"tally" {"STEP" "3"}}))
;;;   (<- answer (wall-sim-cluster (lab sim-foundation) (scenario) :workers #((SimWorker :name "w1" :provides #{"net"}))))
;;;
;;; 時計(入口が選ぶ — 内側の仕組みは同じ):
;;;   sim-cluster       仮想の時計(doeff-time の sim-time-handler・起点 start-ms)。時計は scheduler の中の task が全部止まった時だけ進む
;;;                     (検は時間の長い筋書きを実時間の一瞬で回せる)。外の thread・本物の socket の相手とは時刻が合わない。
;;;   wall-sim-cluster  壁の時計(doeff-time の async-time-handler と await-handler — 今の時刻から始まり、Delay は実時間で待つ)。外の
;;;                     thread の客・本物の待ち受け・実時間の遅れを確かめる検と、手元で系を実時間で回す道具のため(#908・#1086)。
;;;                     筋書きは Await を出してよい。job の Await は柵を通らないので、本物の I/O を持つ job は本番の土台と同じく自分の土台に
;;;                     await-handler を並べる(cluster_foundation.hy の見本)か、その I/O を SimOutside の handler に置く。
;;;
;;; 中身(写しを作らない — 起こし直しの間隔・readiness の窓・handoff の期限・needs ⊆ provides の置き方・lease・fence は本物が決める):
;;;   - coordinator の Pod = 本物の run-coordinator を coordinator_handler_sets.emulated-handlers(要求の列 RequestQueue・memory の
;;;     置き場・偽の k8s)の上で回す。置き場は入口の store(MemoryWalStore の値を作る関数)で差し替えられる — 置き場の欠陥が不変条件を
;;;     破るのを確かめる反例の壊れた置き場のため(#989)。止まれば(止めの合図・Persist の失敗)返事の無い要求に接続の失敗を返し、止まっている秒の後に同じ
;;;     置き場から load-state で読み直して作り直す。一番内側の見張り(observe-requests)が、届いた ReportReady / ReportMetrics を世界へ
;;;     記録し、網の切れた worker から届いた要求を落とし(送り手には接続の失敗)、筋書きの止まり・落ちを注入する。
;;;   - worker = 本物の run-worker を、worker ごとの偽の宿(sim-host)の上で回す(worker-keeper が node の一生を持つ — 死んだ・止めた
;;;     worker は StartWorker で新しい世代として起き直す)。heartbeat・状態の報告・lease を返す要求・温める表の行の読み・root の名乗りは
;;;     本番の CoordinatorLink と同じ形(handlers.heartbeat-body・status-report・desired-when-unreachable・env-report・env-heartbeat-part・
;;;     warm-env-of-row)。コードの木と実行環境の root の準備は SimWorker の prepare-seconds の後に揃う(既定 0 = 即座)・env-failure を
;;;     持つ worker の実行環境の準備はその失敗で終わる。入口の検めは通る(準備の層そのものは env_world と丁寧な模擬の検が持つ)。
;;;   - 偽の宿の StartJob は、coordinator の /programs/<sha> から取った詰めた文字列を decode し(検の物と object を共有しない・運べる値か
;;;     を検める)、「柵(fence)→ クラスタの約束の答え(coordinator-answers)→ 宿の答え(host-answers)→ Program」の順に包んで、宿の
;;;     handler の節の中から Spawn する。Spawn は節の外側の handler(世界・時計)だけを持ち運ぶので、run-worker の中の handler も、他の
;;;     job の handler も混ざらない(service ごとの別のスコープ)。終わり(値・例外・取り消し)は世界へ書き、ObserveWorld が exit-code として
;;;     返す。process の中で Spawn した task は柵が tracked-child で包み直して(元の継続のまま — 子の task は柵より内の handler を持ち
;;;     運ぶ)把手を世界に覚えさせ、process の終わり(値・例外・止めの合図・Crash・worker の死)で一緒に取り消す(本番は子 process ごと
;;;     消える)。task の process は終わりを書く前に、結果を coordinator へ直に届ける(本番の job_entry.run-task と同じ要求 —
;;;     report_client.task-result-request・#1387。届かなければ worker の heartbeat が運ぶ)。
;;;   - 宿の答え(host-answers — process ごと)= host_contract.HOST-CONTRACT の 3 つ(run-context・Program の path・宣言の environ の名の
;;;     Ask — environ は本番の土台と同じ読みの定義 host_contract.environ-reader を子の spec.environ の上に並べる:
;;;     値は字面どおり)と ReportReady・ReportMetrics。クラスタの約束の答え(coordinator-answers — 送り手の口 SimLink ごと)= ReadShared / WriteShared・
;;;     LeaseOp・RemoteJob・SubmitDetached / AwaitDetached / CancelDetached / ReleaseDetached / ReadRunners / AwaitRunnersChange・WarmRuntimeEnv /
;;;     ReadWarmState。
;;;     本番では土台の HTTP の handler が coordinator へ送る物で、要求の形は本番の送り手と同じ関数(report_client.report-request・
;;;     shared_handlers.board-*-request / lease-request・remote.task-submit-body / outcome-of / settled-value・detached.detached-path /
;;;     detached-submit-body / detached-refusal / awaited-answer / warm-request-body)。何度送っても同じ意味の要求(読み・lease の claim と
;;;     renew・切り離した task の口・温める表)は、本番の send-idempotent と同じ期限と間で、sim の時計で送り直す。どちらの答えも柵の内側に
;;;     在るので世界の effect を出さず、要求の列を値で受けて scheduler と時計の effect だけで coordinator と話す。
;;;   - 柵(fence)= host_contract.SIM-PASSABLE(scheduler と doeff-time の時計の effect)だけを外へ通し、それ以外を本番の子と同じ
;;;     doeff.UnhandledEffect で Program へ投げ返す — sim の外側(検の handler・sim の世界)が本番には無い答えを黙って返さない。
;;;   - 筋書き(scenario)は検の側の呼び手(本番の DetachedClient などを持つ機体の外の process)として、同じ coordinator-answers(送り手 sim-client)
;;;     の下で走る。別の送り手(実行環境の宣言・版の違う呼び手)が要る筋書きは ClientLink の値を置き換えて coordinator-answers を自分で被せる。
;;;
;;; 検の effect(sim の世界が答える — scenario の中で出す。service の Program が出すと柵で落ちる):
;;;   Crash 名                  動いている process を exit 1 で落とす(答え = 落とした数)。worker が本物の判断で起こし直す。
;;;   Redeclare 系              宣言し直す(本番の declare と同じ順で Program を置いてから Service の行を書く — update に従い recreate / handoff)。
;;;   DeclareRollout 名 spec     POST /resources/Rollout で作る(本番と同じ検証・所有者・重複検査)。
;;;   KubeCalls                 偽の k8s が受けた書きの履歴の写し(tuple)。
;;;   SettleDeployment ns 名     Deployment の Pod を宣言の台数へ進める(ready で準備済み台数を指定できる)。
;;;   ReportsOf 名              coordinator に届いた ReportReady / ReportMetrics の列(SimReport)。
;;;   ReadinessOf 名            coordinator の Service の status の ready(SimReadiness — Ready / NotReady / Unknown / Missing)。
;;;   ProcessesOf 名            その job の process の列(SimProcess — 世代・worker・始まり・終わり・exit-code)。task は task/<id>。
;;;   AwaitProcessStarted 名    その job の最初の process が起きるまで待ち、その記録を返す(世界が process を記録した時に起きる)。
;;;   AwaitProcessEnded 名      契約の effect(process_model.hy — 本番は detached-cluster が coordinator を読んで答える)。sim では世界が
;;;                             答え、job の今の最後の process の終わりを世界が書いた時に待ち手の Promise を満たす(読み直さない)。
;;;   SharedRows 頭             coordinator の盤の行(鍵が頭で始まる物)。
;;;   ReadCoordinator path      coordinator の口の GET の本文(/state・/workers/<名>・/metrics など)。
;;;   StopCoordinator 秒        coordinator の Pod を優雅に止め(次の拍の止めの合図)、秒の間止めてから作り直す(置き場から読み直す)。
;;;   CrashCoordinator 秒       次の Persist を失敗させる(返事をせずに落ちる — 取った要求の送り手には接続の失敗)。秒の後に作り直す。
;;;   FailRoute 型 path 状態 秒  coordinator の口 型 path(完全一致)への要求に、秒の間 状態(5xx など)で答える(本番の coordinator の前の
;;;                             ingress や作り直しの最中の答え — 要求は調停ループに届かず、状態を変えない)。
;;;   CoordinatorRuns           coordinator の Pod の一生の列(SimCoordinatorRun — 始まり・終わり・止まり方)。
;;;   KillWorker 名             worker が node ごと死ぬ: 子 process は全部 exit -9 で止まり(中で Spawn した task も)、heartbeat が止まる。
;;;                             答え = 止めた process の数。
;;;   StopWorker 名             worker を優雅に止める(本番の SIGTERM — 全 job を止めの手順で回収して抜ける)。抜けるまで待つ。
;;;   StartWorker 名            死んだ・止めた worker を新しい世代(boot)で起こす(答え = 起こしたか — 動いている worker には偽)。
;;;   CutWorker 名 秒           worker の網を秒の間切る: その worker と子 process の要求は coordinator に届かない(接続の失敗)。子は
;;;                             動き続け、fence を越えると本物の worker_policy の判断で lease を持たない job を止める。
;;;   DrainWorker 名 [ttl]      本番の preStop と同じ要求(drain_client.drain-request — 今の世代の boot を載せる)で drain を頼む。
;;;                             答え = 本番の CoordinatorCall と同じ形 {status body} / {error}。
;;;   PreparationsOf 名         worker が起こした準備の列(SimPreparation — コードの版か env-<キー>・先読みか・始まり・終わり・失敗)。
;;;   WatchFailuresOf 名        worker の名指しの待ちの task が思わぬ例外で止まった記録の列(SimWatchFailure — 本番の「待ちの thread が
;;;                             止まった」の 1 行に当たる。止まった世代は拍ごとの heartbeat に戻る — #1933)。
;;;   ClientLink                筋書きの送り手の口(SimLink — 置き換えて coordinator-answers を被せれば別の送り手になる)。
;;; 仮想の時計の時間を進めるのは scenario の Delay(doeff-time — 壁の時計では実時間で待つ)。scenario が終われば worker を止め(全 job を
;;; 止めの手順で回収)、coordinator を止める。
;;;
;;; 本番との既知の差(検めない):
;;;   - 1 process なので、import した module の大域の状態は job の間で共有されうる(改訂 1 の Q)。effect 以外の共有は機械で全部は断れない。
;;;   - environ は宿が宣言の :environ の名の Ask にだけ答える(本番の土台の (environ-reader) は子の os.environ を読むので、PATH などの宣言の
;;;     外の名にも答える)。宣言の名の値は、どちらも同じ読みの定義(host_contract.environ-reader)で字面どおり返る。
;;;   - 土台の関数の :needs が中の handler の :needs を漏らしていても見つからない(計画 9 の P — doeff-linter の照合は別便)。
;;;   - sim の土台は scheduler と時計を含まないので、本番の土台に scheduler を入れ忘れてもここでは見つからない(計画 7)。
;;;   - coordinator に届かない・断られた時の例外の型は RemoteJobFailed(本番は httpx の例外)。書きの要求は 1 回だけ送る(本番の
;;;     CoordinatorEndpoint は接続の段の失敗だけを間を置いて 4 回まで送り直す)。
;;;   - 実行環境の root は準備の中身(git・uv・disk)を模擬しない(prepare-seconds の後に揃うか env-failure で終わる)。disk は常に ok。
;;;   - process の中で Spawn した task は 1 段の包み(tracked-child)の task として起きる(Program が受ける把手は包みの物 — 取り消し・待ち・
;;;     答えは同じ)。
;;;   - AwaitProcessEnded は筋書きにだけ答える(世界の真実で答える — 本番の答えは coordinator の信念で、heartbeat の分だけ遅れる)。
;;;     job の Program が出すと柵で落ちる。
;;;   - AwaitDetached は読み直さず、模擬の coordinator がその task の終わりの phase を書いた時(Persist)に起きる(本番の detached-cluster は
;;;     poll-seconds ごとに読む — 答えの意味は同じ)。
;;;
;;; 状態の置き場(ADR-DOE-HY-007): 世界の状態は世界の handler(sim-world)の session var に置き、値は defrecord、変化は effect で書く。
;;; 要求の列・memory の置き場・停止の合図・偽の k8s は coordinator_handler_sets の既存の資源(世界の session val が 1 回だけ作る)。
;;; 世界の節は session の書きを scheduler の切り替わる effect(Spawn・Wait・CompletePromise)より前に済ませる(切り替わりの間に他の task
;;; が書いた値を、節の古い写しで上書きしない)。
(require doeff-hy.macros [defk deff defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import copy [deepcopy])
(import dataclasses [dataclass replace])
(import pathlib [Path])
(import urllib.parse [quote :as url-quote])
(import doeff [with-handlers EffectBase UnhandledEffect DoExpr Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [state :as session-store await-handler])
(import doeff_core_effects.scheduler [scheduled CreatePromise CompletePromise Wait Spawn Gather Cancel Promise Task
                                      TaskCancelledError])
(import doeff_time [Delay sim-time-handler async-time-handler])
(import doeff_cluster.clock [now-epoch-ms datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request Reply CoordinatorStopRequested PlainText])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming NextRequests Persist ENDED-PHASES])
(import .process_model [AwaitProcessEnded ProcessEnded ProcessWaitExpired])
(import doeff_cluster.coordinator.core.cluster_policy [fresh-task-prefix])
(import doeff_cluster.coordinator.core.program [run-coordinator])
(import doeff_cluster.coordinator.entry.main [load-state])
(import .coordinator_http [IDEMPOTENT-DEADLINE-SECONDS RESEND-PAUSE-SECONDS])
(import doeff_cluster.foundation.coordinator_inbox [StopState http-request])
(import doeff_cluster.coordinator.entry.handler_sets [RequestQueue MemoryWalStore emulated-handlers enqueue-request nudge-takers])
(import .promise_wait [promise-or-timeout])
(import doeff_cluster.foundation.kube_handlers [KubeMemory])
(import .declare [create-body spec-for-update])
(import .detached [detached-path detached-submit-body detached-refusal submit-unreachable awaited-answer runner-facts-of-view
                   runners-unreachable warm-request-body warm-path absent-warm-state SERVER-ERROR warm-unconnected
                   warm-server-failure runners-change-of watch-query])
(import .detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached ReadRunners DetachedSubmitted
                         DetachedSubmitAnswer DetachedAwaited RunnersUnreachable WARMING-PHASE AwaitRunnersChange RunnersChangeAnswer])
(import .drain_client [drain-request DRAIN-DEADLINE-SECONDS DRAIN-TTL-MARGIN-SECONDS])
(import .handlers [declared-job-spec task-spec heartbeat-body status-report desired-when-unreachable env-report env-heartbeat-part
                   warm-env-of-row])
(import .beat_policy [WatchKind WatchReading beat-interval-ms heartbeat-due watch-params watch-reading reply-revision
                      WATCH-RETRY-SECONDS WAKE-HOLD-SECONDS])
(import .host_contract [HOST-CONTRACT SIM-PASSABLE environ-reader])
(import .job_context [RunContext worker-context-environ process-context-environ context-of-environ runtime-env-of-context])
(import .job_entry [decoded-program])
(import .metrics_model [ReportMetrics])
(import .readiness_model [ReportReady])
(import .remote [task-submit-body outcome-of settled-value])
(import .remote_model [RemoteJob RemoteJobFailed TaskSucceeded TaskFailed encode-program encode-outcome failed-from program-sha])
(import .process_versions [current-versions])
(import .report_client [report-request task-result-request task-id-of-job])
(import .runtime_env_model [RuntimeEnv EnvFailure runtime-env->json current-platform])
(import .semaphore_model [LeaseOp SEMAPHORE-PREFIX drop-holders lease-holder holder-tokens-prefix])
(import .service_model [System Declaration system-declaration])
(import .shared_handlers [board-read-request board-write-request lease-request])
(import .shared_model [ReadShared WriteShared])
(import .warm_model [WarmRuntimeEnv ReadWarmState WarmState WarmAnswer warm-state-of-json])
(import .worker [run-worker])
(import .worker_model [JobSpec WorkerPolicy WorkerState WorldView CodeView CodeState ProcessView ProbeView ProbeState
                       DesiredJobs DesiredUnreadable ReadDesired ObserveWorld WorkerStopRequested PublishStatus
                       PrepareCode PrepareEnv SweepEnvs StartJob SignalJob ReapJob RetireJob ProbeEntry ForgetProbes
                       ReleaseLeases spec-hash])

;; load-state は置き場がまだ無い時だけ以前の形の file を探す。sim は置き場(MemoryWalStore)が在る時だけ load-state を呼ぶので読まれない。
(val NO-STATE-FILE "/nonexistent/doeff-sim/coordinator/state.json")
(val SIM-URL "sim://coordinator")            ; 宿の契約の run-context の coordinator の URL(sim の宿は URL で話さない — 表示だけ)
(val SIM-START-MS 1767225600000)             ; 仮想の時計の起点(2026-01-01T00:00:00Z)
(val DECLARE-ACTOR "sim-declare")            ; 宣言と検の読みの送り手(資源の書きの X-Actor)
(val CLIENT-NAME "sim-client")               ; 筋書き(検の側の呼び手)の送り手の名と居る所
(val TASK-POLL-SECONDS 1.0)                  ; RemoteJob の問い合わせの間隔(本番の remote-cluster の既定と同じ)
(val TASK-LEASE-SECONDS 15.0)                ; RemoteJob の lease(同じ)
(val DETACHED-POLL-SECONDS 1.0)              ; 切り離した task の待ちの問い合わせの間隔(本番の detached-cluster の既定と同じ)
(val QUEUE-WAIT-SECONDS 0.01)                ; coordinator が受け付けを始める・worker が最初に名乗るのを待つ間隔
(val WORKER-WAIT-SECONDS 5.0)                ; 筋書きの前に worker が最初に名乗るのを待つ上限(名乗れない worker が在っても筋書きは始める)
(val DRAIN-TTL-SECONDS (float (+ DRAIN-DEADLINE-SECONDS DRAIN-TTL-MARGIN-SECONDS)))  ; drain の期限(本番の preStop の頼みと同じ)
(val REPORT-KINDS #("readiness" "metrics"))
(val KILLED-CODE -9)                         ; node ごと死んだ worker の子 process の exit-code
(val PAUSE-STOP "stop")                      ; coordinator の止まりの種類: 優雅な停止
(val PAUSE-CRASH "crash")                    ;                         Persist の失敗(返事をせずに落ちる)
(val CUT-REASON "網が切れている(sim — 送り手の居る worker の網)")
(val FAULT-REASON "sim: 注入した故障(FailRoute — coordinator の口がこの状態で答える)")


;; --- 公開の値 --------------------------------------------------------------------------------------------

(defrecord SimWorker
  "sim の worker 1 台(本番の worker の --provides・--exclusive・--capacity・node に当たる)。provides = 提供する能力の名・
   exclusive = 専用の能力(この能力を needs に持つ job だけを受ける)・node = 置かれた k8s の node の名(空 = k8s の外)・
   versions = 名乗る版(None = 送り手と同じ current-versions — 違えば版の合わない task は置かれない)・prepare-seconds = コードの木と
   実行環境の root の準備にかかる仮想の秒・env-failure = 実行環境の root の準備がこの失敗で終わる worker(None = 揃う)・
   starts-down = 止まったまま始まる(StartWorker で起きる — 後から加わる node)・ignores-fence = 反例の世界だけの壊れた worker
   (coordinator に届かない間 fence を越えても job を止めない — 本番の worker_policy の判断を使わない)・beat-every-ms = 反例の世界だけの
   壊れた worker(heartbeat の間隔を本番の beat_policy.beat-interval-ms でなくこの値にする — None = 本番の判断)・retire-stops = 反例の
   世界だけの壊れた worker(入れ替えで旧を名から外す RetireJob の handler が、外すと同時に旧を止める — 条 W1 の反例)。"
  (#^ str name)
  (#^ frozenset provides)
  (setv #^ frozenset exclusive (frozenset))
  (setv #^ int capacity 10)
  (setv #^ str node "")
  (setv #^ (| dict None) versions None)
  (setv #^ float prepare-seconds 0.0)
  (setv #^ (| EnvFailure None) env-failure None)
  (setv #^ bool starts-down False)
  (setv #^ bool ignores-fence False)
  (setv #^ (| int None) beat-every-ms None)
  (setv #^ bool retire-stops False))


(defrecord SimProcess
  "sim の宿が起こした process 1 つ(ProcessesOf の答えの要素)。job = 起こした時の job の名(task は task/<id>)・instance = 世代の名・
   attempt = worker の試行の番号・pid = sim の中の番号・spec-hash = 起こした spec の指紋・ended-ms / exit-code = 終わった時だけ
   (exit-code: 0 = 値で終わった / task は結果を書いて終わった・1 = 例外か Crash・3 = Program を解けない・-15 = 止めの合図・
   -9 = worker が node ごと死んだ)・detail = 終わった理由の 1 行(例外の型と文 — 本番の子の log の最後の行に当たる。値で終わった時は空)・
   value = service の Program が値で終わった時のその値(本番では捨てる — 検が有限の周回の答えを読むための sim だけの観測。task と値で終わらなかった
   process は None)。"
  (#^ str job)
  (#^ str worker)
  (#^ str instance)
  (#^ int attempt)
  (#^ int pid)
  (#^ str spec-hash)
  (#^ int started-ms)
  (setv #^ (| int None) ended-ms None)
  (setv #^ (| int None) exit-code None)
  (setv #^ str detail "")
  (setv #^ object value None))


(defrecord SimReport
  "coordinator に届いた service の報告 1 つ(ReportsOf の答えの要素)。kind = readiness | metrics・instance = 送り手の process の世代・
   at = 届いた時刻(epoch ms)・ready / reason / role = readiness の欄(metrics では None / 空)・metrics = 計器(readiness では None)。"
  (#^ str job)
  (#^ str kind)
  (#^ str instance)
  (#^ int at)
  (#^ (| bool None) ready)
  (#^ str reason)
  (#^ str role)
  (#^ (| dict None) metrics))


(defrecord SimReadiness
  "coordinator の Service の status の ready(ReadinessOf の答え)。state = Ready | NotReady | Unknown | Missing(Service が無い)。"
  (#^ str state)
  (#^ str reason))


(defrecord SimPreparation
  "worker の宿が起こした準備 1 つ(PreparationsOf の答えの要素)。key = コードの版か env-<キー>・env = 実行環境の root か・warm = 先読みか・
   started-ms / ready-ms = 始まりと揃う(か失敗で終わる)時刻(epoch ms)・failure = 実行環境の準備の失敗(揃うなら None)。"
  (#^ str worker)
  (#^ str key)
  (#^ bool env)
  (#^ bool warm)
  (#^ int started-ms)
  (#^ int ready-ms)
  (#^ (| EnvFailure None) failure))


(defrecord SimWatchFailure
  "worker の名指しの待ちの task が思わぬ例外で止まった記録 1 つ(WatchFailuresOf の答えの要素 — 本番の「待ちの thread が止まった」の
   1 行に当たる)。boot = 止まった世代・at = 止まった時刻(epoch ms)・reason = 例外の型と文。"
  (#^ str worker)
  (#^ str boot)
  (#^ int at)
  (#^ str reason))


(defrecord SimCoordinatorRun
  "coordinator の Pod の一生 1 つ(CoordinatorRuns の答えの要素)。outcome = 止まり方(stopped = 止めの合図・それ以外は落ちた理由の
   1 行)— まだ動いていれば ended-ms は None で outcome は空。"
  (#^ int started-ms)
  (setv #^ (| int None) ended-ms None)
  (setv #^ str outcome ""))


(defrecord SimLink
  "coordinator へ話す送り手の口 1 つ(クラスタの約束の答え coordinator-answers の引数)。queue = coordinator の受け口(要求の列)・
   actor = 書きの送り手(X-Actor)・revision = 送り手の版(task の revision)・peer = 送り手の居る所(網の切断は worker の名で数える)・
   runtime-env = 送る task(RemoteJob と切り離した task)の実行環境の宣言(本番の TaskClient・DetachedClient の runtime-env — None =
   送り手の版のコードだけ)。"
  (#^ RequestQueue queue)
  (#^ str actor)
  (#^ str revision)
  (#^ str peer)
  (setv #^ (| RuntimeEnv None) runtime-env None))


;; --- 検の effect(sim の世界が答える)----------------------------------------------------------------------

(defeffect Crash
  "検の effect: job name の動いている process を全部 exit 1 で落とす(本番の子の異常終了)。答え = 落とした数。"
  {:fields [(: name str)]
   :answer int
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect Redeclare
  "検の effect: 系を宣言し直す(版 = environ か引数を変えた系の値)。答え = 宣言した Service の名の tuple。"
  {:fields [(: system System)]
   :answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect DeclareRollout
  "検の effect: 本番の資源 API と同じ要求で Rollout を作る。答え = 資源の本文。拒否・接続失敗は RemoteJobFailed。"
  {:fields [(: name str) (: spec dict)]
   :answer dict
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KubeCalls
  "検の effect: KubeMemory.calls の写し(受けた順の dict の tuple)。dryRun の書きも含む。"
  {:answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect SettleDeployment
  "検の effect: 偽の Deployment の Pod を宣言の台数に揃える。ready = None は全台準備済み。worker は StartWorker で別に起こす。"
  {:fields [(: namespace str) (: name str) (: ready (| int None) None)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReportsOf
  "検の effect: job name の process から coordinator に届いた ReportReady / ReportMetrics の列(SimReport の tuple・届いた順)。"
  {:fields [(: name str)]
   :answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReadinessOf
  "検の effect: coordinator が数えている Service name の ready(SimReadiness)。"
  {:fields [(: name str)]
   :answer SimReadiness
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ProcessesOf
  "検の effect: job name の process の列(SimProcess の tuple・起こした順)。"
  {:fields [(: name str)]
   :answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect AwaitProcessStarted
  "検の effect: job name の最初の process が起きるまで待ち、その記録(SimProcess)を返す(既に起きていればすぐ)。読み直さず、世界が
   process を記録した時(NoteProcess)に起きる。"
  {:fields [(: name str)]
   :answer SimProcess
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect SharedRows
  "検の effect: coordinator の盤の行のうち鍵が prefix で始まる物(鍵 → 値)。"
  {:fields [(: prefix str)]
   :answer dict
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReadCoordinator
  "検の effect: coordinator の口 path の GET の本文(/state・/workers/<名>・/metrics など — 届かない・断られたら RemoteJobFailed)。"
  {:fields [(: path str)]
   :answer (| dict PlainText)
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StopCoordinator
  "検の effect: coordinator の Pod を次の拍で優雅に止め(止めの合図 — 取った要求には返事を済ませる)、seconds 秒止めてから作り直す
   (同じ置き場から読み直す)。止まっている間の要求は接続の失敗。答え = None(止まるのは次の拍)。"
  {:fields [(: seconds float)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CrashCoordinator
  "検の effect: coordinator の次の Persist を失敗させる(fsync の失敗 — 返事をせずに落ちる。取った要求の送り手には接続の失敗・その拍の
   書きは置き場に残らない)。seconds 秒の後に同じ置き場から読み直して作り直す。答え = None(落ちるのは次の書き)。"
  {:fields [(: seconds float)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect FailRoute
  "検の effect: coordinator の口 method path(完全一致)への要求に、seconds 秒の間 status で答える(本番の coordinator の前の ingress・
   作り直しの最中の 5xx — 要求は調停ループに届かず、状態を変えない。本文は {\"error\" 理由})。網の切れた worker の要求は接続の失敗の
   まま(切断が先)。答え = None。"
  {:fields [(: method str) (: path str) (: status int) (: seconds float)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CoordinatorRuns
  "検の effect: coordinator の Pod の一生の列(SimCoordinatorRun の tuple・起きた順)。"
  {:answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KillWorker
  "検の effect: worker name が node ごと死ぬ — 動いている子 process は全部 exit -9 で止まり(中で Spawn した task も)、heartbeat が
   止まる(coordinator は lease の後に生きていないと数える)。答え = 止めた process の数(もう死んでいれば 0)。"
  {:fields [(: name str)]
   :answer int
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StopWorker
  "検の effect: worker name を優雅に止める(本番の SIGTERM — 宣言を空として全 job を止めの手順で回収し、lease を返して抜ける)。抜けるまで
   待つ。答え = None。"
  {:fields [(: name str)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StartWorker
  "検の effect: 死んだ・止めた worker name を新しい世代(boot)で起こす。答え = 起こしたか(動いている worker には偽)。"
  {:fields [(: name str)]
   :answer bool
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CutWorker
  "検の effect: worker name の網を seconds 秒切る — その worker と子 process の要求は coordinator に届かない(接続の失敗)。子 process は
   動き続け、fence を越えると本物の worker_policy の判断で lease を持たない job を止める。答え = None。"
  {:fields [(: name str) (: seconds float)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect DrainWorker
  "検の effect: worker name の drain を、本番の preStop と同じ要求(drain_client.drain-request — 今の世代の boot を載せる)で頼む。
   ttl-seconds = drain の期限。答え = 本番の CoordinatorCall と同じ形({\"status\" int \"body\" dict} か {\"error\" 理由})。"
  {:fields [(: name str) (: ttl-seconds float DRAIN-TTL-SECONDS)]
   :answer dict
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PreparationsOf
  "検の effect: worker name が起こした準備の列(SimPreparation の tuple・起こした順)。"
  {:fields [(: name str)]
   :answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect WatchFailuresOf
  "検の effect: worker name の名指しの待ちの task が思わぬ例外で止まった記録の列(SimWatchFailure の tuple・起きた順)。"
  {:fields [(: name str)]
   :answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ClientLink
  "検の effect: 筋書きの送り手の口(SimLink — 送り手 sim-client)。値を置き換えて coordinator-answers を被せれば、実行環境の宣言や版の
   違う送り手として話せる。"
  {:answer SimLink
   :tags {:context "doeff-cluster" :role "intent"}})


;; --- sim の中の値 ---------------------------------------------------------------------------------------

(defrecord SimOutside
  "sim の外の世界(本番では job の土台の handler が外の系 — 業務の store・外部の API — と話して答える effect に、sim では系の外側で
   答える物)。handlers = sim の全部の job と筋書きの外側に置く handler の組(外側が先 — 時計の内側)・effects = それが答える effect の型
   (柵が外へ通す — isinstance で数えるので基底の型でよい)。job は effect を通してだけ外の世界を共有する(object を共有しない)。
   per-process = process ごとの外の世界を作る関数 (job の名 worker の名) → ProcessOutside(handler と、その process の柵だけが通す型 —
   None = 無し)。宿が process を起こす
   時に 1 回呼び、柵の外側・sim の世界の内側に並べる — 本番で job ごと・機体ごとに違う外の口(記録の service の身元の token・預かり所の
   借り手・機体の session の置き場)を、共有の外の世界(handlers)の手前で答えるため(#833 の条件「sim-cluster は担い手ごとに
   handler の組を持つ」・#834)。作る handler も effects に載った型にだけ答える(柵がそれ以外を通さない)。"
  (#^ list handlers)
  (#^ tuple effects)
  (setv #^ (| Callable None) per-process None))


(defrecord SimPlan
  "sim の 1 回の走りの筋(sim-cluster が引数から作る)。declaration = 最初の宣言(environ の上書きを重ねた行)・environ = job 名 →
   上書きの環境変数(Redeclare にも重ねる)・passable = 柵が外へ通す effect の型(SIM-PASSABLE と外の世界の effects)・
   per-process = process ごとの外の handler の組を作る関数(SimOutside.per-process — None = 無し)・store = coordinator の置き場を作る
   関数(引数なし → MemoryWalStore の値 — 派生の class をそのまま渡せる。None = MemoryWalStore)。deployments = 偽の k8s の
   初期観測(「namespace/名」→ dict)。parts-of が深い写しを作り、1 回の走りの間だけ変更する。runtime-env = 宣言の実行環境の宣言
   (本番の declare の --runtime-env と同じ — Redeclare にも載せる。None = 送り手の版のコードだけ)。skip-idle = coordinator の要求の列が、要求の無い間に何も変えない拍を
   一度に眠るか(仮想の時計の入口 sim-cluster だけが真 — 本番の拍の間隔と判断の刻は変えない・2026-09-30)。"
  (#^ System system)
  (#^ Declaration declaration)
  (#^ tuple workers)
  (#^ dict environ)
  (#^ str revision)
  (#^ int start-ms)
  (#^ ClusterTiming timing)
  (#^ ClusterNaming naming)
  (#^ WorkerPolicy policy)
  (#^ tuple passable)
  (setv #^ (| Callable None) per-process None)
  (setv #^ (| Callable None) store None)
  (setv #^ (| dict None) deployments None)
  (setv #^ (| RuntimeEnv None) runtime-env None)
  (setv #^ bool skip-idle False))


(defrecord SimParts
  "coordinator の Pod の部品(1 回の走りに 1 組 — 世界の session val が 1 回だけ作る)。emulated-handlers が受ける要求の列・置き場・
   停止の合図・偽の k8s。"
  (#^ RequestQueue queue)
  (#^ MemoryWalStore store)
  (#^ StopState stop)
  (#^ KubeMemory kube))


(defrecord SimExit
  "sim の子 process の終わり方(本番の job_entry の入口の終わり方と同じ): code = exit-code・result = task の詰めた結果(service は None)・
   detail = 終わった理由の 1 行(例外の型と文・値で終わった時は空)・value = service が値で終わった時のその値(SimProcess の value へ写す)。"
  (#^ int code)
  (#^ (| str None) result)
  (setv #^ str detail "")
  (setv #^ object value None))


(defrecord HostTruth
  "worker の宿 1 つの真実(世界の session に在る)。boot = この世代の名(StartWorker で新しくなる)・processes = 子 process の観測・
   codes = 準備(鍵 → SimPreparation)・probes = 入口の検め・statuses = 最後に出した状態の報告(heartbeat の本文)・last-ok-ms =
   coordinator が最後に返事をした時刻・fence-ms = 自己停止の閾値・last-desired / last-warm = 最後に読めた job と task・温める表の行・
   programs = 取った詰めた Program(sha → 文字列)・results = 終わった task の結果(id → 詰めた結果)・task-echo = 切り離した task の
   返事の行(id → 行)・beats = coordinator が返事をした heartbeat の数・down = 死んだか止まった・stopping = 優雅な停止を頼まれた。
   heartbeat の切り離し(#1933 — 本番の CoordinatorLink の WatchState と同じ意味・beat_policy): fresh = 前の heartbeat が届いた・
   sent-statuses = 前に届けた状態の報告・beat-interval-ms = 送る間隔・watch-after = 次の名指しの待ちの版(返事に版が無ければ None)・
   watch-confirmed = 待ちが 1 度答えた・watch-unsupported = 待つ口が無い(404)・woken = 待ちが「変わった」と答えた印・beat-bells =
   heartbeat が届いた時に鳴らす呼び鈴(起こした後の待ちが次の版を待つ)・watch-failure = 待ちの task が思わぬ例外で止まった理由
   (在れば拍ごとの heartbeat に戻る — 本番の CoordinatorLink.watching が thread の死に気づくのと同じ)。"
  (#^ str boot)
  (#^ int boot-at)
  (#^ tuple processes)
  (#^ dict codes)
  (#^ dict probes)
  (#^ list statuses)
  (#^ int last-ok-ms)
  (#^ int fence-ms)
  (#^ tuple last-desired)
  (#^ tuple last-warm)
  (#^ dict programs)
  (#^ dict results)
  (#^ dict task-echo)
  (setv #^ int beats 0)
  (setv #^ bool down False)
  (setv #^ bool stopping False)
  (setv #^ bool fresh False)
  (setv #^ (| list None) sent-statuses None)
  (setv #^ int beat-interval-ms (beat-interval-ms None {}))
  (setv #^ (| int None) watch-after None)
  (setv #^ bool watch-confirmed False)
  (setv #^ bool watch-unsupported False)
  (setv #^ bool woken False)
  (setv #^ tuple beat-bells #())
  (setv #^ (| str None) watch-failure None))


(defclass WorkerDied [Exception]
  "偽の宿の世代が終わった(node ごと死んだ・止めた後に次の世代が起きた)— その世代の run-worker をその場で終わらせる。")


;; --- 世界の仕組みの effect(答えるのは世界の handler sim-world だけ — Program からは柵で届かない)---------------------

(defeffect PlanOf
  "sim の筋(SimPlan)。"
  {:answer SimPlan :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PartsOf
  "coordinator の Pod の部品(SimParts)。"
  {:answer SimParts :tags {:context "doeff-cluster" :role "intent"}})

(defeffect HostTruthOf
  "worker name の宿の真実(HostTruth)。"
  {:fields [(: name str)] :answer HostTruth :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PutHostTruth
  "worker name の宿の真実を置き直す。"
  {:fields [(: name str) (: truth HostTruth)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NextPid
  "次の process の番号(sim の中で重ならない)。"
  {:answer int :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KeepHandle
  "process pid の task の把手を覚える(止めの合図と Crash が取り消す相手)。既に止めると決まった process なら、その場で取り消す。"
  {:fields [(: pid int) (: task Task)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect HandleOf
  "process pid の task の把手(終わっていれば None)。"
  {:fields [(: pid int)] :answer (| Task None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KeepChild
  "process pid の中で Spawn した task を覚える(process の終わりで一緒に取り消す)。process がもう終わっていれば、その場で取り消す。"
  {:fields [(: pid int) (: task Task)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NoteProcess
  "起こした process を記録する。"
  {:fields [(: process SimProcess)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect EndProcess
  "process の終わり(SimExit — exit-code・task の結果・理由)を書き(worker の宿の観測の exit-code・task の結果・記録の終わり)、
   process の中で Spawn した task を取り消す。"
  {:fields [(: worker str) (: pid int) (: ended SimExit)] :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KillOf
  "process pid の取り消しの終わり方(Crash = exit 1・worker の死 = exit -9 — 止めの合図なら None)。"
  {:fields [(: pid int)] :answer (| SimExit None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NoteReports
  "coordinator に届いた報告を記録する。"
  {:fields [(: batch tuple)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NotePreparation
  "worker の宿が起こした準備を記録する。"
  {:fields [(: preparation SimPreparation)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NoteWatchFailure
  "worker の世代の名指しの待ちの task が思わぬ例外で止まったことを記録し、その世代(今の世代なら)を拍ごとの heartbeat に戻す。"
  {:fields [(: failure SimWatchFailure)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect WorkersStopping
  "全 worker が止まる時か(scenario が終わった後に真)。"
  {:answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StopWorkers
  "全 worker に止まれと合図する(死んだ worker の起き直しの待ちも解く)。"
  {:answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect WorkerEnded
  "worker name の世代 boot の run-worker が抜けた(その世代がまだ今の世代なら止まったと記録し、StopWorker の待ちを解く)。"
  {:fields [(: name str) (: boot str)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect RevivalOf
  "worker name が止まっていれば、StartWorker で完了する Promise(動いていれば None — すぐ次の世代を起こす)。"
  {:fields [(: name str)] :answer (| Promise None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CutPeers
  "今網の切れている worker の名。"
  {:answer frozenset :tags {:context "doeff-cluster" :role "intent"}})

(defeffect FailedRoutes
  "今故障を入れている coordinator の口(#(method path) → 答える status)。"
  {:answer dict :tags {:context "doeff-cluster" :role "intent"}})

(defeffect HoldRequests
  "coordinator が取った要求(返事の前に落ちたら接続の失敗を返す相手)を覚える。"
  {:fields [(: batch tuple)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReleaseRequest
  "返事を済ませた要求を覚えから外す。"
  {:fields [(: request Request)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect TakeHeldRequests
  "覚えている要求を全部取り出して空にする(答え = Request の tuple)。"
  {:answer tuple :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PauseDue
  "coordinator の止まりの kind(stop | crash)が頼まれているか。頼まれていれば筋書きから外し、止まっている秒を覚える。"
  {:fields [(: kind str)] :answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect DowntimeOf
  "止まっている秒(None = 作り直さない)を取り出して空にする。"
  {:answer (| float None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CoordinatorStarted
  "coordinator の Pod が起きた(時刻 ms)。"
  {:fields [(: ms int)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CoordinatorEnded
  "coordinator の Pod が止まった(時刻 ms・止まり方 outcome)。"
  {:fields [(: ms int) (: outcome str)] :answer None :tags {:context "doeff-cluster" :role "intent"}})


;; --- 筋の組み立て(純粋)--------------------------------------------------------------------------------

(defk default-workers [system]
  {:pre [(: system System)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker を名指さない時の既定 = 系の全 job の needs の和を提供する 1 台(どの job も置ける)。"
  (val needs (frozenset (gfor j system.jobs n j.needs n)))
  #((SimWorker :name "sim-worker" :provides needs)))


(defk declaration-of [system revision environ runtime-env]
  {:pre [(: system System) (: revision str) (: environ dict) (: runtime-env (| RuntimeEnv None))] :post [(: % Declaration)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "系 → coordinator へ渡す宣言(本番の declare と同じ system-declaration)に、job ごとの environ の上書きと実行環境の宣言を重ねるため
   (計画 2.7 の H・改訂 1 の M — whole.hy の overrides の置き換え先)。上書きの規則(系に無い job・宣言の :environ に無い名・文字列でない
   値は断る)は本番の declare と同じ 1 つ(service_model.environ-overlay-refusal)。実行環境の宣言は本番の declare の --runtime-env と同じ
   欄に載り、本物の worker が子へ DOEFF_RUNTIME_ENV で渡す(子の run-context の runtime-env)。"
  (system-declaration system revision :versions (current-versions) :runtime-env runtime-env :environ environ))


(defk sim-plan [system workers environ revision start-ms timing policy outside store [deployments None] [runtime-env None] [skip-idle False]]
  {:pre [(: system System) (: workers (| tuple None)) (: environ (| dict None)) (: revision str) (: start-ms int)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None)) (: store (| Callable None))
         (: deployments (| dict None)) (: runtime-env (| RuntimeEnv None)) (: skip-idle bool)]
   :post [(: % SimPlan)] :tags {:context "doeff-cluster" :role "judgment"}}
  "sim-cluster の引数を検めて筋にするため(走らせる前に断る — environ の上書きの誤り・名の重なる worker)。"
  (<- fallback tuple (default-workers system))
  (val chosen (if (is workers None) fallback workers))
  (val names (lfor w chosen w.name))
  (when (or (not chosen) (!= (len names) (len (set names))) (not (all (gfor w chosen (isinstance w SimWorker)))))
    (raise (ValueError (.format "workers は名の重ならない SimWorker の 1 つ以上の tuple: {!r}" chosen))))
  (<- declaration Declaration (declaration-of system revision (or environ {}) runtime-env))
  (SimPlan :system system :declaration declaration :workers chosen :environ (or environ {}) :revision revision
           :per-process (if (is outside None) None outside.per-process) :store store :deployments deployments :runtime-env runtime-env
           :skip-idle skip-idle
           :start-ms start-ms :timing (or timing (ClusterTiming)) :naming (ClusterNaming) :policy (or policy (WorkerPolicy))
           :passable (+ SIM-PASSABLE (if (is outside None) #() outside.effects))))


(defk parts-of [plan]
  {:pre [(: plan SimPlan)] :post [(: % SimParts)] :tags {:context "doeff-cluster" :role "foundation"}}
  "coordinator の Pod の部品を作るため(世界の handler が session で 1 回だけ呼ぶ — 置き場は 1 回の走りに 1 つで、作り直した
   coordinator も同じ置き場から読み直す)。置き場は筋の store が作る(無ければ MemoryWalStore)。"
  (val store (if (is plan.store None) (MemoryWalStore) (plan.store)))
  (when (not (isinstance store MemoryWalStore))
    (raise (TypeError (.format "store は MemoryWalStore の値を作る関数: {!r} が {!r} を返した" plan.store store))))
  (SimParts :queue (RequestQueue :skip-idle plan.skip-idle) :store store :stop (StopState) :kube (KubeMemory (deepcopy (or plan.deployments {})))))


(defk fresh-truth [name generation now fence-ms]
  {:pre [(: name str) (: generation int) (: now int) (: fence-ms int)] :post [(: % HostTruth)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "worker name の世代 generation の起きた時の宿の真実を作るため(起きた時刻を最後の連絡とみなす — 本番の CoordinatorLink と同じ)。"
  (HostTruth :boot (.format "{}-boot{}" name generation) :boot-at now :processes #() :codes {} :probes {} :statuses []
             :last-ok-ms now :fence-ms fence-ms :last-desired #() :last-warm #() :programs {} :results {} :task-echo {}))


(defk fresh-hosts [plan]
  {:pre [(: plan SimPlan)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker ごとの最初の世代の宿の真実を作るため(starts-down の worker は止まったまま — StartWorker で起きる)。"
  (var hosts {})
  (for [w plan.workers]
    (<- truth HostTruth (fresh-truth w.name 1 plan.start-ms plan.timing.fence-ms))
    (:= hosts (| hosts {w.name (replace truth :down w.starts-down)})))
  hosts)


(defk reports-in [batch now]
  {:pre [(: batch list) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator が取った要求のうち service の報告(POST /resources/Service/<名>/readiness|metrics)を SimReport にするため。"
  (tuple (gfor r batch
               :setv parts r.parts
               :if (and (= r.method "POST") (= (len parts) 4) (= (get parts 0) "resources") (= (get parts 1) "Service")
                        (in (get parts 3) REPORT-KINDS) (isinstance r.body dict))
               (SimReport :job (get parts 2) :kind (get parts 3) :instance (str (.get r.body "instance" "")) :at now
                          :ready (.get r.body "ready") :reason (str (.get r.body "reason" "")) :role (str (.get r.body "role" "active"))
                          :metrics (.get r.body "metrics")))))


(defk code-view-of [preparation now]
  {:pre [(: preparation SimPreparation) (: now int)] :post [(: % CodeView)] :tags {:context "doeff-cluster" :role "judgment"}}
  "準備 1 つ → worker の観測(揃うまで PREPARING・失敗なら FAILED と失敗の kind・揃えば READY と path)にするため。"
  (cond
    (< now preparation.ready-ms) (CodeView preparation.key CodeState.PREPARING)
    (is-not preparation.failure None) (CodeView preparation.key CodeState.FAILED :detail preparation.failure.detail
                                                :failed-ms preparation.ready-ms :failure preparation.failure)
    True (CodeView preparation.key CodeState.READY (+ "/sim/code/" preparation.key))))


(defk codes-view [codes now]
  {:pre [(: codes dict) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宿の準備の全部を worker の観測の列にするため。"
  (var views #())
  (for [preparation (.values codes)]
    (<- view CodeView (code-view-of preparation now))
    (:= views (+ views #(view))))
  views)


(defk view-of [truth now]
  {:pre [(: truth HostTruth) (: now int)] :post [(: % WorldView)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宿の真実 → worker の観測(準備・子 process・入口の検め)にするため。"
  (<- codes tuple (codes-view truth.codes now))
  (WorldView codes truth.processes (tuple (.values truth.probes))))


(defk run-context-of [worker spec attempt instance]
  {:pre [(: worker str) (: spec JobSpec) (: attempt int) (: instance str)] :post [(: % RunContext)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "起こす process の宿の契約の run-context を作るため: 本番の worker が子へ渡す環境変数を同じ関数(job_context の
   worker-context-environ・process-context-environ — 本番の main と ProcessHost.launch が呼ぶ物)で作り、本番の子と同じ読み
   (context-of-environ)で読む(報告の世代が coordinator の report-matches と合い、実行環境の job の子は宣言とキーを受ける)。"
  (<- shared dict (worker-context-environ SIM-URL worker))
  (<- own dict (process-context-environ spec instance attempt))
  (<- ctx RunContext (context-of-environ (| shared own)))
  ctx)


(defk program-path-of [sha]
  {:pre [(: sha (| str None))] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宿の契約の Program の path を作るため(本番の worker の cache の file と同じ形 <sha>.json — 記録係の header が置き場のキーを読む)。"
  (if sha (+ "/sim/programs/" sha ".json") ""))


(defk control-link [queue revision]
  {:pre [(: queue RequestQueue) (: revision str)] :post [(: % SimLink)] :tags {:context "doeff-cluster" :role "judgment"}}
  "sim の仕組み(宣言・検の読み)が coordinator へ話す口を作るため(送り手 sim-declare — 網の切断は受けない)。"
  (SimLink :queue queue :actor DECLARE-ACTOR :revision revision :peer DECLARE-ACTOR))


;; --- coordinator との話し方(scheduler と時計の effect だけ — 柵の内側の答えも使う)--------------------------------

(defk send-request [link method path query body]
  {:pre [(: link SimLink) (: method str) (: path str) (: query dict) (: body (| dict None))]
   :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の受け口(要求の列)へ 1 件送り、返事 #(status 本文) を待つため。止まっている coordinator には接続の失敗
   #(None {\"error\" …}) を返す(本番の送り手の接続の失敗に当たる)。"
  (if (not link.queue.up)
      #(None {"error" "coordinator に接続できない(止まっている)"})
      (do (<- promise Promise (CreatePromise))
          (<- (enqueue-request link.queue (http-request method path query body :slot promise :actor link.actor :peer link.peer)))
          (<- answer tuple (Wait promise.future))
          answer)))


(defk send-resent [link method path query body]
  {:pre [(: link SimLink) (: method str) (: path str) (: query dict) (: body (| dict None))]
   :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "何度送っても同じ意味の要求を、届かなければ本番の send-idempotent と同じ期限(IDEMPOTENT-DEADLINE-SECONDS)と間(RESEND-PAUSE-SECONDS)で
   送り直すため(sim の時計で眠る)。答え = 最後の返事(期限を過ぎても届かなければ接続の失敗)。"
  (<- started int (now-epoch-ms))
  (var answer #(None {"error" "送っていない"}))
  (var going True)
  (while going
    (<- sent tuple (send-request link method path query body))
    (<- now int (now-epoch-ms))
    (:= answer sent)
    (if (or (is-not (get sent 0) None)
            (> (+ (/ (- now started) 1000.0) RESEND-PAUSE-SECONDS) IDEMPOTENT-DEADLINE-SECONDS))
        (:= going False)
        (<- (Delay RESEND-PAUSE-SECONDS))))
  answer)


(defk send-shaped [link shape]
  {:pre [(: link SimLink) (: shape tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の送り手と同じ関数が作った要求 #(method path query 本文) を 1 回送るため。"
  (<- answer tuple (send-request link (get shape 0) (get shape 1) (get shape 2) (get shape 3)))
  answer)


(defk send-shaped-resent [link shape]
  {:pre [(: link SimLink) (: shape tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の送り手と同じ関数が作った、何度送っても同じ意味の要求を、届くまで期限の内で送り直すため。"
  (<- answer tuple (send-resent link (get shape 0) (get shape 1) (get shape 2) (get shape 3)))
  answer)


(deff answered-body [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % (| dict list str int float bool None PlainText))] :tags {:context "doeff-cluster" :role "judgment"}}
  "返事 #(status 本文) の本文を返すため(300 以上・届かないなら理由つきの RemoteJobFailed — 本番の raise-for-status に当たる)。"
  (when (or (is (get answer 0) None) (>= (get answer 0) 300))
    (raise (RemoteJobFailed (.format "{}: coordinator の返事 {} {}" what (get answer 0) (get answer 1)))))
  (get answer 1))


(deff refused-or-body [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % (| dict list str int float bool None PlainText))] :tags {:context "doeff-cluster" :role "judgment"}}
  "切り離した task と温める表の口の返事を読むため: 呼び手の誤り(400・409・413・429)は本番の client と同じ DetachedRefused
   (detached.detached-refusal)、それ以外は answered-body。"
  (let [refusal (detached-refusal (get answer 0) (if (isinstance (get answer 1) dict) (get answer 1) None))]
    (when refusal (raise refusal))
    (answered-body answer what)))


(deff object-body [#^ (| dict list str int float bool None PlainText) body #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: body (| dict list str int float bool None PlainText)) (: what str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "欄を読む口の本文が JSON の object であることを確かめて dict として返すため(object でない本文は本番の送り手と同じく
   RemoteJobFailed — 型を持たない本文を添字で読まない)。"
  (if (isinstance body dict)
      body
      (raise (RemoteJobFailed (.format "{}: coordinator の返事の本文が object でない: {!r}" what body)))))


(deff answered-object [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "欄を読む口の返事 #(status 本文) の本文を dict で返すため(answered-body の後に object であることを確かめる)。"
  (object-body (answered-body answer what) what))


(deff refused-or-object [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "切り離した task と温める表の口の返事の本文を dict で返すため(refused-or-body の後に object であることを確かめる)。"
  (object-body (refused-or-body answer what) what))


(deff unreached-reason [#^ tuple answer]  ; defk にできない: 答えの節が返事の理由を読む純粋な判断
  {:pre [(: answer tuple)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "届かなかった返事 #(None {\"error\" 理由}) の理由の 1 行を読むため。"
  (if (isinstance (get answer 1) dict) (str (.get (get answer 1) "error" "")) (str (get answer 1))))


(deff board-written [#^ tuple answer]  ; defk にできない: 答えの節が返事を Program への答えに変える純粋な判断
  {:pre [(: answer tuple)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "盤の compare-and-set の返事を WriteShared の答えにするため(409 = 合わなかった = 偽 — 本番の SharedClient.write と同じ読み)。"
  (if (= (get answer 0) 409) False (do (answered-body answer "盤に書けない") True)))


(defk remote-outcome [link program needs name environ]
  {:pre [(: link SimLink) (: program (| Program EffectBase)) (: needs frozenset) (: name str) (: environ dict)]
   :post [(: % (| TaskSucceeded TaskFailed))] :tags {:context "doeff-cluster" :role "protocol"}}
  "RemoteJob を本番の remote-cluster と同じ手順で coordinator へ出し、結果を待つため: 詰めた Program を PUT /programs/<sha> で置き、
   POST /tasks(task-submit-body)で出し、問い合わせ(lease を延ばす)を終わるまで続け、抜ける時は task を落とす。送れない値は送る前に
   断る(encode-program の UnsendableProgram)。版は送り手の版(link.revision)・実行環境の宣言は送り手の宣言(link.runtime-env —
   本番の TaskClient の runtime-env と同じく本文の runtimeEnv に載せる)。"
  (val blob (encode-program program))
  (val sha (program-sha blob))
  (<- put tuple (send-resent link "PUT" (+ "/programs/" sha) {} {"blob" blob "versions" (current-versions)}))
  (answered-body put "task の Program を置けない")
  (<- sent tuple (send-request link "POST" "/tasks" {}
                               (task-submit-body sha link.revision needs name TASK-LEASE-SECONDS link.runtime-env environ)))
  (val id (get (answered-object sent "task を出せない") "task"))
  (var outcome None)
  (try
    (while (is outcome None)
      (<- (Delay TASK-POLL-SECONDS))
      (<- polled tuple (send-request link "GET" (+ "/tasks/" id) {} None))
      ;; 届かない問い合わせは次の拍で送り直す(本番の send-idempotent と同じく、読みは何度送っても同じ意味)。
      (when (= (get polled 0) 200)
        (:= outcome (outcome-of (get polled 1) id link.revision))))
    (finally
      (<- (send-request link "DELETE" (+ "/tasks/" id) {} None))))
  outcome)


(defk declared-env [env]
  {:pre [(: env (| RuntimeEnv None))] :post [(: % (| dict None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "送り手の実行環境の宣言を本文の JSON にするため(無ければ None)。"
  (if (is env None)
      None
      (do (<- declared dict (runtime-env->json env))
          declared)))


(defk submit-detached [link program key needs name lease-seconds retain-seconds environ]
  {:pre [(: link SimLink) (: program (| Program EffectBase)) (: key str) (: needs frozenset) (: name str) (: lease-seconds float)
         (: retain-seconds float) (: environ dict)]
   :post [(: % DetachedSubmitAnswer)] :tags {:context "doeff-cluster" :role "protocol"}}
  "SubmitDetached を本番の DetachedClient と同じ手順で送るため: 詰めた Program を版と一緒に PUT /programs/<sha> に置き、PUT /detached/<key>
   (detached-submit-body)で出す。どちらも何度送っても同じ意味なので期限まで送り直し、届かなければ DetachedUnreachable(送れたかは
   分からない — key で冪等)。送れない値は送る前に断る(UnsendableProgram)・呼び手の誤りは DetachedRefused。"
  (val blob (encode-program program))
  (val sha (program-sha blob))
  (<- put tuple (send-resent link "PUT" (+ "/programs/" sha) {} {"blob" blob "versions" (current-versions)}))
  (if (is (get put 0) None)
      (submit-unreachable (unreached-reason put))
      (do (refused-or-body put "task の Program を置けない")
          (<- declared (| dict None) (declared-env link.runtime-env))
          (<- sent tuple (send-resent link "PUT" (detached-path key "") {}
                                      (detached-submit-body sha link.revision needs name lease-seconds retain-seconds declared environ)))
          (if (is (get sent 0) None)
              (submit-unreachable (unreached-reason sent))
              (DetachedSubmitted key (get (refused-or-object sent "task を出せない") "created"))))))


(defk bell-span [view timeout-seconds waited]
  {:pre [(: view (| dict None)) (: timeout-seconds (| float int None)) (: waited float)] :post [(: % (| float None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "切り離した task の待ちの 1 回の眠りの上限を決めるため: coordinator に届かない・起きた直後(warming)の間は送り直しの間隔
   DETACHED-POLL-SECONDS(書きが来ない — 本番の送り手の送り直しと同じ)、それ以外は期限までの残り(期限なし = None — 終わりの書きの
   呼び鈴だけで起きる)。"
  (cond
    (or (is view None) (= (.get view "phase") WARMING-PHASE)) DETACHED-POLL-SECONDS
    (is timeout-seconds None) None
    True (max 0.0 (- (float timeout-seconds) waited))))


(defk await-detached [link key timeout-seconds]
  {:pre [(: link SimLink) (: key str) (: timeout-seconds (| float int None))] :post [(: % DetachedAwaited)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitDetached を本番の await-cluster と同じ読みで待つため: GET /detached/<key>(期限まで送り直す・503 = 起きた直後の warming は本文)を
   読み、1 回の読みは本番と同じ判断(detached.awaited-answer)で答えか待ち続けるかを決める。待つ間は読み直さず、読む前に掛けた
   呼び鈴(RequestQueue.bells — 模擬の coordinator がその task の終わりの phase を書いた時に鳴らす)か期限で起きる(proboscis/doeff#631)。
   読みの回数は終わりの書きの前後の 2 回と期限の 1 回ほど(coordinator に届かない間だけ DETACHED-POLL-SECONDS ごとに送り直す)。"
  (<- started int (now-epoch-ms))
  (val bells link.queue.bells)
  (var answer None)
  (while (is answer None)
    (<- bell Promise (CreatePromise))
    ;; 読む前に掛ける(読みの返事と終わりの書きの間に鳴らしを取りこぼさない)。
    (setv (get bells key) (+ (.get bells key #()) #(bell)))
    (<- read tuple (send-resent link "GET" (detached-path key "") {} None))
    (<- now int (now-epoch-ms))
    (val waited (/ (- now started) 1000.0))
    (val view (cond (is (get read 0) None) None
                    (= (get read 0) 503) (object-body (get read 1) "task を読めない")
                    True (refused-or-object read "task を読めない")))
    (:= answer (awaited-answer view (unreached-reason read) key waited timeout-seconds))
    (when (is answer None)
      (<- span (| float None) (bell-span view timeout-seconds waited))
      (<- (promise-or-timeout bell.future span)))
    ;; 鳴らなかった呼び鈴を外す(鳴った物は鳴らした側が外している)。
    (val left (tuple (gfor b (.get bells key #()) :if (is-not b bell) b)))
    (if left (setv (get bells key) left) (.pop bells key None)))
  answer)


(defk ended-task-keys [delta]
  {:pre [(: delta dict)] :post [(: % frozenset)] :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator の 1 回の Persist の差分から、終わりの phase(cluster_model.ENDED-PHASES)を書いた切り離した task の key を読むため
   (その key の呼び鈴を鳴らす)。"
  (frozenset (gfor #(k row) (.items delta)
                   :if (and (.startswith k "task/") (isinstance row dict) (.get row "detached") (.get row "key")
                            (in (.get row "phase") ENDED-PHASES))
                   (get row "key"))))


(defk ring-ended-tasks [queue delta]
  {:pre [(: queue RequestQueue) (: delta dict)] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "Persist が書き終えた差分で終わった切り離した task の呼び鈴を外して鳴らすため(待っている送り手が 1 回だけ読み直す)。
   答え = 鳴らした呼び鈴の数。"
  (<- keys frozenset (ended-task-keys delta))
  (val rung (lfor key (sorted keys) bell (.pop queue.bells key #()) bell))
  (for [bell rung]
    (<- (CompletePromise bell None)))
  (len rung))


(defk read-runners [link]
  {:pre [(: link SimLink)] :post [(: % (| tuple RunnersUnreachable))] :tags {:context "doeff-cluster" :role "protocol"}}
  "ReadRunners を本番の DetachedClient.runners と同じく GET /state の workers から読むため(届かなければ RunnersUnreachable)。"
  (<- read tuple (send-resent link "GET" "/state" {} None))
  (if (is (get read 0) None)
      (runners-unreachable (unreached-reason read))
      (runner-facts-of-view (get (answered-object read "名簿を読めない") "workers"))))


(defk await-runners-change [link after timeout-seconds]
  {:pre [(: link SimLink) (: after int) (: timeout-seconds float)] :post [(: % RunnersChangeAnswer)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitRunnersChange を本番の DetachedClient.runners-change と同じく GET /watch で 1 回待ち、同じ読み(detached.runners-change-of)で
   答えるため(#1934)。"
  (<- answer tuple (send-request link "GET" "/watch" (watch-query after timeout-seconds) None))
  (runners-change-of (get answer 0) (if (is (get answer 0) None) (unreached-reason answer) (get answer 1))))


(deff warm-answer-of [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えに変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % WarmAnswer)] :tags {:context "doeff-cluster" :role "judgment"}}
  "温める表の返事を、本番の WarmClient と同じ読み(同じ定義 detached.warm-unconnected・warm-server-failure)で答えにするため:
   期限まで届かない = WarmUnreachable・coordinator の 5xx = WarmUnreachable・断り(400 ほか)= DetachedRefused・それ以外 = 行の姿。"
  (cond
    (is (get answer 0) None) (warm-unconnected (unreached-reason answer))
    (>= (get answer 0) SERVER-ERROR) (warm-server-failure (get answer 0) (str (get answer 1)))
    True (let [body (refused-or-body answer what)]
           ;; 温める表の口の本文は行の dict(refused-or-body の答えの形は口ごとなので、ここで確かめて絞る)。
           (if (isinstance body dict)
               (warm-state-of-json body)
               (raise (TypeError (.format "{}: 温める表の返事の本文が dict でない: {!r}" what body)))))))


(defk warm-write [link env needs ttl-seconds holder]
  {:pre [(: link SimLink) (: env RuntimeEnv) (: needs frozenset) (: ttl-seconds float) (: holder str)] :post [(: % WarmAnswer)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "WarmRuntimeEnv を本番の WarmClient.write と同じ本文(warm-request-body)で POST /warm に書き、今の姿を読むため(届かない・coordinator の
   5xx は本番と同じ WarmUnreachable)。"
  (<- declared dict (runtime-env->json env))
  (<- written tuple (send-resent link "POST" "/warm" {} (warm-request-body declared needs ttl-seconds holder)))
  (warm-answer-of written "温める表に書けない"))


(defk warm-read [link key]
  {:pre [(: link SimLink) (: key str)] :post [(: % WarmAnswer)] :tags {:context "doeff-cluster" :role "protocol"}}
  "ReadWarmState を本番の WarmClient.read と同じく GET /warm/<キー> で読むため(表に無い行 = 404 は空の姿・届かない・coordinator の
   5xx は本番と同じ WarmUnreachable)。"
  (<- read tuple (send-resent link "GET" (warm-path key) {} None))
  (if (= (get read 0) 404)
      (absent-warm-state key)
      (warm-answer-of read "温める表を読めない")))


;; --- 柵と答え(process ごと・Program のすぐ外)--------------------------------------------------------------

(defclass TrackedSpawn [Spawn]  ; class にする理由: scheduler が isinstance で Spawn と読む印つきの形(外の基底を継ぐ — 欄も状態も足さない)
  "柵が包み直した Spawn の印(柵はこの形を包み直さずに通す — 包み直しを繰り返さない)。")


(defk tracked-child [pid body priority daemon]
  {:pre [(: pid int) (: body Program) (: priority int) (: daemon bool)] :post [(: % "body の答え")]
   :tags {:context "doeff-cluster" :role "program"}}
  "process pid の中で Spawn した task の本体: body を同じ handler の下の task として起こし、その把手を世界に覚えさせ(process の終わりで
   取り消す)、答えを待って返すため。呼び手がこの task を取り消せば body も取り消す。"
  (<- inner Task (TrackedSpawn body :priority priority :daemon daemon))
  (<- (KeepChild pid inner))
  (try
    (<- value (Wait inner))
    value
    (except [TaskCancelledError]
      (<- (Cancel inner))
      (raise))))


(defhandler fence [#^ int pid #^ tuple passable]
  {:tags {:context "doeff-cluster" :role "protocol"}}
  ;; 引数に残す理由: process の中で Spawn した task を、その process の番号で覚える(process の終わりで一緒に取り消す)。柵は宿の答えより
  ;; 外に在り、Program の effect ではない番号を Ask で問えない。
  ;; 宿の答えより外で、SIM-PASSABLE(scheduler と時計)の外の effect を本番の子と同じ未処理の例外で Program へ投げ返す(改訂 1 の B)。
  ;; これが無いと、sim の外側(検の handler・sim の世界)が本番には無い答えを黙って返し、本番で答えの無い effect が sim だけで通る。
  ;; Spawn は tracked-child で包み直して元の継続のまま送る(reperform — 子の task は Program の中の handler と柵を持ち運ぶ。答えを受けて
  ;; 把手を覚える形にすると、子の task が柵より内の handler を失う)。子の本体が起きた時に把手を世界に覚えさせる(KeepChild は柵の
  ;; 仕組みの effect なので、ここだけは外へ通す)。本番の子 process の中の task は process と一緒に消える — 柵の外で動き続けさせない。
  (Spawn []
    :when (not (isinstance effect TrackedSpawn))
    (reperform (TrackedSpawn (tracked-child pid effect.program effect.priority effect.daemon)
                             :priority effect.priority :daemon effect.daemon)))
  (KeepChild []
    (reperform effect))
  (KillOf []
    ;; 一番内側の dead-process-gate が問う sim の仕組みの effect(KeepChild と同じく通す)。
    (reperform effect))
  (EffectBase []
    :when (not (isinstance effect passable))
    (raise (UnhandledEffect (.format "sim の柵: 答えの無い effect {} ({!r}) — 本番の子 process でも答える handler が無い"
                                     (. (type effect) __name__) effect)))))


(defhandler dead-process-gate [#^ int pid]
  {:tags {:context "doeff-cluster" :role "protocol"}}
  ;; 引数に残す理由: どの process が殺されたかを process の番号で問う(Program の effect ではない番号を Ask で問えない)。
  ;; 殺された process(worker の死 = -9・Crash = 1)の取り消しの巻き戻しの中で撃たれた effect を、宿の答えにも外の世界にも届けない —
  ;; 本物の機体の死と子 process の落ちでは、落ちた後の process から何も届かない。Program の一番内側に置き、scheduler と時計の effect
  ;; (SIM-PASSABLE — 巻き戻しの Wait・Cancel)は通す。止めの合図(KillOf が None — 優雅な停止)の後の後始末は通す。
  (EffectBase []
    :when (not (isinstance effect SIM-PASSABLE))
    (<- killed (| SimExit None) (KillOf pid))
    (if (is killed None)
        (reperform effect)
        (raise (UnhandledEffect (.format "sim: 落ちた process {} の effect {} は届かない(exit {})" pid (. (type effect) __name__)
                                         killed.code))))))


(defrecord SimChild
  "sim の子 process 1 つに宿が答える物(宿の答え host-answers の引数)。ctx = 宿の契約の run-context・program-path = Program の path・
   environ = 宣言の :environ(上書きを重ねた物)か task の effect の :environ(名 → 値 — どちらも spec.environ)・link = coordinator へ話す口(送り手 = job の名・居る所 = worker)・
   pid = sim の中の process の番号・passable = 柵が外へ通す effect の型(SimPlan.passable)・outside = この process の外の handler の組
   (SimOutside.per-process が作った物 — 柵の外側に並べる)。"
  (#^ RunContext ctx)
  (#^ str program-path)
  (#^ dict environ)
  (#^ SimLink link)
  (#^ int pid)
  (#^ tuple passable)
  (setv #^ tuple outside #()))


(defk send-report [child kind payload]
  {:pre [(: child SimChild) (: kind str) (: payload dict)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "service の報告を本番の ServiceReportClient と同じ形で送るため。届かなくても業務を止めない(報告は観測)。"
  (val ctx child.ctx)
  (<- (send-shaped child.link (report-request ctx.job (| {"worker" ctx.worker "pid" child.pid "revision" ctx.revision} (.identity ctx))
                                              kind payload)))
  None)


(defhandler host-answers [#^ SimChild child]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 答えは process ごとに違い(世代・Program の path・environ)、宿の契約の Ask の鍵は本番の宿と同じなので、Ask で
  ;; 区別できない。柵の内側に在るので世界の effect で読むこともできない。
  ;; 本番の宿と土台の HTTP の handler が process に答える物(宿の契約 HOST-CONTRACT の run-context と Program の path・service の報告)に、
  ;; 同じ本文で答える。宣言の :environ は、本番の土台と同じ読みの定義 environ-reader を子の spec.environ の上に並べて
  ;; 答える(run-fenced — ここで第 2 の読みを持たない)。
  (Ask [key]
    :when (in key #(HOST-CONTRACT.run-context-key HOST-CONTRACT.program-key))
    (resume (if (= key HOST-CONTRACT.run-context-key) child.ctx child.program-path)))
  (ReportReady [ready reason role]
    (<- (send-report child "readiness" {"ready" ready "reason" reason "role" role}))
    (resume None))
  (ReportMetrics [metrics]
    (<- (send-report child "metrics" {"metrics" metrics}))
    (resume None)))


(defhandler coordinator-answers [#^ SimLink link]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 送り手ごとに口が違い(job の名・居る所の worker・版・実行環境の宣言)、柵の内側に在るので世界の effect で読めない。
  ;; 本番では土台の HTTP の handler(shared-http・remote-cluster・detached-cluster・warm-cluster)が coordinator へ送るクラスタの約束の
  ;; effect に、同じ本文と同じ送り直しで答える。世界の effect を出さず、要求の列(値)と scheduler・時計の effect だけで話す。
  (ReadShared [prefix]
    (<- answer tuple (send-shaped-resent link (board-read-request prefix)))
    (resume (answered-body answer "盤を読めない")))
  (WriteShared [key value expect ttl-seconds]
    (<- answer tuple (send-shaped link (board-write-request key value expect ttl-seconds)))
    (resume (board-written answer)))
  (LeaseOp [name op token permits ttl-ms]
    ;; claim と renew は同じ token で何度送っても同じ意味(本番の SharedClient.lease と同じく送り直す)。release・drop は 1 回だけ。
    (val shape (lease-request name op token permits ttl-ms))
    (<- answer tuple (if (in op #("claim" "renew")) (send-shaped-resent link shape) (send-shaped link shape)))
    (resume (answered-body answer (.format "lease {} の {}" name op))))
  (RemoteJob [program needs name environ]
    (<- outcome (remote-outcome link program needs name environ))
    (resume (settled-value outcome)))
  (SubmitDetached [program key needs name lease-seconds retain-seconds environ]
    (<- submitted (submit-detached link program key needs name (float lease-seconds) (float retain-seconds) environ))
    (resume submitted))
  (AwaitDetached [key timeout-seconds]
    (<- awaited (await-detached link key timeout-seconds))
    (resume awaited))
  (CancelDetached [key]
    (<- answer tuple (send-resent link "POST" (detached-path key "/cancel") {} None))
    (resume (get (refused-or-object answer "取り消せない") "cancelled")))
  (ReleaseDetached [key]
    (<- answer tuple (send-resent link "DELETE" (detached-path key "") {} None))
    (resume (get (refused-or-object answer "保持を解けない") "released")))
  (ReadRunners []
    (<- runners (read-runners link))
    (resume runners))
  (AwaitRunnersChange [after timeout-seconds]
    (<- change (await-runners-change link after (float timeout-seconds)))
    (resume change))
  (WarmRuntimeEnv [env needs ttl-seconds holder]
    (<- warmed WarmAnswer (warm-write link env needs (float ttl-seconds) holder))
    (resume warmed))
  (ReadWarmState [key]
    (<- warm WarmAnswer (warm-read link key))
    (resume warm)))


(defrecord ProcessOutside
  "process ごとの外の世界(SimOutside.per-process の答え): handlers = その process の柵の外側に並べる handler(外側が先)・effects =
   その process の柵だけが外へ通す effect の型(isinstance — 基底の型でよい)。共有の外の世界の型(SimOutside.effects)は全 process の
   柵が通すので、本番で job ごとに持つ口(その job の土台だけが答える effect)はここに置く — 系で 1 つの許しの和にすると、本番の土台が
   答えない effect を別の job の外の口が sim で黙って答える(構成のレビュー 2026-09-28 の A)。"
  (#^ tuple handlers)
  (setv #^ tuple effects #()))


(defk process-outside [per-process job worker]
  {:pre [(: per-process (| Callable None)) (: job str) (: worker str)] :post [(: % ProcessOutside)] :tags {:context "doeff-cluster" :role "judgment"}}
  "process ごとの外の世界を作るため(SimOutside.per-process を job の名と worker の名で呼ぶ — 無ければ空)。ProcessOutside でない答えは
   断る(黙って外の世界を欠いた process を起こさない)。"
  (when (is per-process None)
    (return (ProcessOutside :handlers #())))
  (val made (per-process job worker))
  (when (not (isinstance made ProcessOutside))
    (raise (TypeError (.format "SimOutside の per-process の答えは ProcessOutside(job {} ・worker {}): {!r}" job worker made))))
  made)


(defk run-fenced [program child once]
  {:pre [(: program DoExpr) (: child SimChild) (: once bool)] :post [(: % SimExit)] :tags {:context "doeff-cluster" :role "program"}}
  "Program を柵と答えの中で走らせ、終わり方を決めるため(本番の job_entry の service / task の入口の終わり方と同じ: service は
   値 = 0・例外 = 1、task は結果を書いて 0。止めの合図 = -15・Crash = 1・worker の死 = -9)。"
  (try
    (<- value (with-handlers [#* child.outside (fence child.pid child.passable) (coordinator-answers child.link) (host-answers child)
                              (environ-reader child.environ) (dead-process-gate child.pid)]
                             program))
    (SimExit :code 0 :result (if once (encode-outcome (TaskSucceeded value)) None) :value (if once None value))
    (except [TaskCancelledError]
      (<- killed (| SimExit None) (KillOf child.pid))
      (if (is killed None) (SimExit :code -15 :result None :detail "止めの合図") killed))
    (except [error Exception]
      ;; 殺された process の巻き戻しの中で例外が出ても(落ちた後の effect を dead-process-gate が断る時を含む)、終わり方は殺された形
      ;; (worker の死 = -9・Crash = 1)— 本番の子 process は殺された時点で終わっており、後の例外は外から見えない。
      (<- killed (| SimExit None) (KillOf child.pid))
      (if (is-not killed None)
          killed
          (SimExit :code (if once 0 1) :result (if once (encode-outcome (failed-from error)) None)
                   :detail (.format "{}: {}" (. (type error) __name__) error))))))


(defk refused-exit [refusal once]
  {:pre [(: refusal RemoteJobFailed) (: once bool)] :post [(: % SimExit)] :tags {:context "doeff-cluster" :role "judgment"}}
  "Program を解けなかった process の終わり方を決めるため(本番の job_entry と同じ: service は理由を出して 3・task は失敗の結果を書いて 0)。"
  (SimExit :code (if once 0 3)
           :result (if once (encode-outcome (failed-from refusal)) None)
           :detail (.format "{}: {}" (. (type refusal) __name__) refusal)))


(defk deliver-task-result [child result]
  {:pre [(: child SimChild) (: result str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の task の子 process が終わる前に結果を coordinator へ直に届けるのと同じ要求(report_client.task-result-request)を、子の送り手の
   口で 1 回送るため(#1387)。答えは読まない — 届かなければ、世界に書いた結果を worker の heartbeat が運ぶ(本番の file の路と同じ)。
   届ける相手の task の id は本番の子と同じ判断(report_client.task-id-of-job)で子の文脈の job の名から読み、読めなければ送らない。"
  (val task (task-id-of-job child.ctx.job))
  (when (is-not task None)
    (<- (send-shaped child.link (task-result-request task child.ctx.worker child.ctx.instance result))))
  None)


(defk sim-process [worker spec child blob]
  {:pre [(: worker str) (: spec JobSpec) (: child SimChild) (: blob (| str None))]
   :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "sim の子 process 1 つの一生: 詰めた Program を解き(解けなければ本番の入口と同じく service は 3・task は失敗の結果)、柵の中で
   走らせ、task は終わる前に結果を coordinator へ直に届け(本番の job_entry.run-task と同じ)、終わりを世界へ書く(中で Spawn した task も
   一緒に止まる)。殺された process(結果なし)は届けない。"
  (val decoded (if (is blob None)
                   #(None (RemoteJobFailed (.format "Program {} を coordinator の置き場から取れていない" spec.program)))
                   (decoded-program blob)))
  (<- ended SimExit (match decoded
                      #(None refusal) (refused-exit refusal spec.once)
                      #(program None) (run-fenced program child spec.once)))
  (when (is-not ended.result None)
    (<- (deliver-task-result child ended.result)))
  (<- (EndProcess worker child.pid ended))
  None)


;; --- worker の偽の宿 ------------------------------------------------------------------------------------

(defk live-truth [name boot]
  {:pre [(: name str) (: boot str)] :post [(: % HostTruth)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker name の世代 boot の宿の真実を読むため。その世代がもう終わっていれば(死んだ・止めた後に次の世代が起きた)WorkerDied で
   その世代の run-worker を終わらせる。"
  (<- truth HostTruth (HostTruthOf name))
  (when (or truth.down (!= truth.boot boot))
    (raise (WorkerDied (.format "worker {} の世代 {} は終わった(今は {}{})" name boot truth.boot (if truth.down "・止まっている" "")))))
  truth)


(defk accepted-programs [link wanted known]
  {:pre [(: link SimLink) (: wanted list) (: known dict)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言の job と task のうち、まだ取っていない詰めた Program を coordinator の /programs/<sha> から取るため(本番の
   CoordinatorLink.accept-programs と同じ — 中身の sha256 がキーと合わない物は取らない・取れなければ次の拍で試し直す)。"
  (var fetched {})
  (for [sha wanted]
    (when (not-in sha known)
      (<- answer tuple (send-request link "GET" (+ "/programs/" sha) {} None))
      (when (and (= (get answer 0) 200) (= (program-sha (get (get answer 1) "blob")) sha))
        (:= fetched (| fetched {sha (get (get answer 1) "blob")})))))
  fetched)


(defk heartbeat [worker boot]
  {:pre [(: worker SimWorker) (: boot str)] :post [(: % (| DesiredJobs DesiredUnreadable))] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の CoordinatorLink.poll の代役: 生存・能力・版・状態・root の名乗りを同じ本文(heartbeat-body・env-heartbeat-part)で送り、返事の
   job と task と温める表の行を宣言として返す。届かなければ desired-when-unreachable(本番と同じ判断)。"
  (<- parts SimParts (PartsOf))
  (<- plan SimPlan (PlanOf))
  (<- before HostTruth (live-truth worker.name boot))
  (<- sent-at int (now-epoch-ms))
  (<- views tuple (codes-view before.codes sent-at))
  (val link (SimLink :queue parts.queue :actor worker.name :revision plan.revision :peer worker.name))
  (val body (| (heartbeat-body :name worker.name :provides (tuple (sorted worker.provides)) :exclusive (tuple (sorted worker.exclusive))
                               :node worker.node :capacity worker.capacity :versions (or worker.versions (current-versions))
                               :statuses before.statuses :endpoint (+ "sim://" worker.name) :boot before.boot
                               :boot-at before.boot-at :tools {})
               (env-heartbeat-part (env-report views "ok") (current-platform))))
  ;; 送る前に起こしの印を下ろす(送った後に来た変化の印を消さない — 本番の CoordinatorLink.beat と同じ)。
  (<- (PutHostTruth worker.name (replace before :woken False)))
  (<- answer tuple (send-request link "POST" "/heartbeat" {} body))
  (<- now int (now-epoch-ms))
  (if (= (get answer 0) 200)
      (do (val reply (get answer 1))
          (val jobs (tuple (gfor j (get reply "jobs") (declared-job-spec j))))
          (val tasks (tuple (gfor t (.get reply "tasks" []) (task-spec t (Path "/sim/tasks" worker.name)))))
          (val warm (tuple (gfor row (.get reply "warm" []) (warm-env-of-row row (current-platform)))))
          (val ids (sfor t (.get reply "tasks" []) (get t "id")))
          (<- known HostTruth (live-truth worker.name boot))
          (<- fetched dict (accepted-programs link (sorted (sfor s (+ jobs tasks) :if s.program s.program)) known.programs))
          ;; 返事を待つ間に他の task が宿の真実を書く — 書く直前に読み直す(その間に世代が終わっていれば抜ける)。
          (<- truth HostTruth (live-truth worker.name boot))
          (val timing (.get reply "timing"))
          (val echo (dfor t (.get reply "tasks" []) :if (.get t "detached") (get t "id") (dict t)))
          (<- (PutHostTruth worker.name
                            (replace truth :last-ok-ms now :last-desired (+ jobs tasks) :last-warm warm :beats (+ truth.beats 1)
                                     :fence-ms (if (and timing (in "fence_ms" timing)) (int (get timing "fence_ms")) truth.fence-ms)
                                     :programs (| truth.programs fetched)
                                     ;; 返事から外れた task の結果は落とす(本番の accept-tasks が結果の file を消すのと同じ)。
                                     :results (dfor #(k v) (.items truth.results) :if (in k ids) k v)
                                     :task-echo echo
                                     ;; 次の拍の判断の材料(#1933 — 本番の CoordinatorLink.beat と同じ)。
                                     :fresh True :sent-statuses before.statuses :beat-interval-ms (beat-interval-ms timing echo)
                                     :watch-after (reply-revision reply) :beat-bells #())))
          ;; 起こした後の待ちに、版が進んだことを知らせる。
          (for [bell truth.beat-bells]
            (<- (CompletePromise bell None)))
          (DesiredJobs (+ jobs tasks) :warm warm))
      (do (<- truth HostTruth (live-truth worker.name boot))
          ;; 届かない間は毎拍送り直す(前の desired を使い続けない — fence の判断を毎拍する)。
          (<- (PutHostTruth worker.name (replace truth :fresh False)))
          (if worker.ignores-fence
              (DesiredJobs truth.last-desired :warm truth.last-warm)
              (desired-when-unreachable (- now truth.last-ok-ms) truth.fence-ms truth.last-desired truth.last-warm
                                        (unreached-reason answer))))))


(defk release-leases [link job instance]
  {:pre [(: link SimLink) (: job str) (: instance str)] :post [(: % int)] :tags {:context "doeff-cluster" :role "protocol"}}
  "終わった process(job の名 job・世代の名 instance)が持っていた lease を返すため(本番の handlers.release-leases と同じ要求 — 子が
   名乗った担い手と同じ定義の token の頭 <job>/<世代の名>/ の担い手を POST /leases/<名> の drop で外す)。答え = 返した数。届かなければ
   期限で切れる。"
  (val prefix (holder-tokens-prefix (lease-holder job instance)))
  (<- rows tuple (send-shaped link (board-read-request SEMAPHORE-PREFIX)))
  (var dropped 0)
  (when (= (get rows 0) 200)
    (for [#(key row) (.items (get rows 1))]
      (when (is-not (drop-holders row prefix) None)
        (<- answer tuple (send-request link "POST" (+ "/leases/" (url-quote (cut key (len SEMAPHORE-PREFIX) None) :safe "")) {}
                                       {"op" "drop" "token" prefix}))
        (when (and (is-not (get answer 0) None) (< (get answer 0) 300))
          (:= dropped (+ dropped 1))))))
  dropped)


(defk begin-preparation [worker truth key env warm now]
  {:pre [(: worker SimWorker) (: truth HostTruth) (: key str) (: env bool) (: warm bool) (: now int)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "準備 1 つを始めて宿の真実と世界の記録に書くため(揃う時刻 = now + prepare-seconds・実行環境の root は worker の env-failure で終わる)。"
  (val preparation (SimPreparation :worker worker.name :key key :env env :warm warm :started-ms now
                                   :ready-ms (+ now (int (* 1000 worker.prepare-seconds)))
                                   :failure (if env worker.env-failure None)))
  (<- (PutHostTruth worker.name (replace truth :codes (| truth.codes {key preparation}))))
  (<- (NotePreparation preparation))
  None)


(defk prepare [worker boot key env warm]
  {:pre [(: worker SimWorker) (: boot str) (: key str) (: env bool) (: warm bool)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "コードの木か実行環境の root の準備を起こすため: prepare-seconds の後に揃う(実行環境の root は env-failure を持つ worker なら失敗で
   終わる)。worker のループは待たない(揃うのは ObserveWorld で観測する)。本番の CodeStore・EnvStore の start と同じく、同じ鍵の準備が
   走っている・揃っているなら起こし直さない(先読みの準備を job が頼めば job の準備へ上げる)・失敗した物だけ起こし直す。"
  (<- truth HostTruth (live-truth worker.name boot))
  (<- now int (now-epoch-ms))
  (val current (.get truth.codes key))
  (cond
    (is current None) (<- (begin-preparation worker truth key env warm now))
    (and (< now current.ready-ms) current.warm (not warm))
      (<- (PutHostTruth worker.name (replace truth :codes (| truth.codes {key (replace current :warm False)}))))
    (< now current.ready-ms) None
    (is current.failure None) None
    True (<- (begin-preparation worker truth key env warm now)))
  None)


(defhandler sim-host [#^ SimWorker worker #^ str boot]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 同じ組の中で worker ごと・世代ごとに別の宿を並べる(run-worker は自分の名も世代も effect で問わない)ので Ask で
  ;; 区別できない。
  ;; 本物の run-worker の effect に偽の宿で答える(本番の組 = handlers.hy の local-host・coordinator-desired・status-to-coordinator・
  ;; lease-release-coordinator・stop-flag)。宿の真実は世界の session に在り、HostTruthOf / PutHostTruth で読み書きする。どの節も先に
  ;; 世代が今のものかを確かめ(live-truth)、終わった世代の run-worker をその場で終わらせる。
  (ReadDesired []
    ;; heartbeat は送る拍(beat_policy.heartbeat-due — 本番の CoordinatorLink.poll と同じ判断)だけ送り、それ以外は前の返事の desired。
    (<- truth HostTruth (live-truth worker.name boot))
    (<- now int (now-epoch-ms))
    (val watching (and truth.watch-confirmed (not truth.watch-unsupported) (is-not truth.watch-after None)
                       (is truth.watch-failure None)))
    (val due (heartbeat-due watching truth.fresh truth.woken (!= truth.statuses truth.sent-statuses) (- now truth.last-ok-ms)
                            (if (is worker.beat-every-ms None) truth.beat-interval-ms worker.beat-every-ms)))
    (if due
        (do (<- desired (| DesiredJobs DesiredUnreadable) (heartbeat worker boot))
            (resume desired))
        (resume (DesiredJobs truth.last-desired :warm truth.last-warm))))
  (ObserveWorld []
    (<- truth HostTruth (live-truth worker.name boot))
    (<- now int (now-epoch-ms))
    (<- view WorldView (view-of truth now))
    (resume view))
  (PrepareCode [revision]
    (<- (prepare worker boot revision False False))
    (resume None))
  (PrepareEnv [key runtime-env warm]
    (<- (prepare worker boot key True warm))
    (resume None))
  (SweepEnvs [pinned]
    (<- (live-truth worker.name boot))
    (resume None))
  (ProbeEntry [spec code-path]
    (<- truth HostTruth (live-truth worker.name boot))
    (val key (spec-hash spec))
    (<- (PutHostTruth worker.name (replace truth :probes (| truth.probes {key (ProbeView key ProbeState.PASSED)}))))
    (resume None))
  (ForgetProbes [keep]
    (<- truth HostTruth (live-truth worker.name boot))
    (<- (PutHostTruth worker.name (replace truth :probes (dfor #(k v) (.items truth.probes) :if (in k keep) k v))))
    (resume None))
  (StartJob [spec attempt code-path]
    (<- truth HostTruth (live-truth worker.name boot))
    (<- parts SimParts (PartsOf))
    (<- now int (now-epoch-ms))
    (<- pid int (NextPid))
    (val instance (.format "{}-p{}" worker.name pid))
    (<- ctx RunContext (run-context-of worker.name spec attempt instance))
    ;; 観測と記録を Spawn の前に書く(Spawn の直後に新しい task が先に走って終わっても、終わりを書く相手が在る)。
    (<- (PutHostTruth worker.name
                      (replace truth :processes (+ truth.processes #((ProcessView spec.name spec attempt pid now :instance instance))))))
    (<- (NoteProcess (SimProcess :job spec.name :worker worker.name :instance instance :attempt attempt :pid pid
                                 :spec-hash (spec-hash spec) :started-ms now)))
    ;; 節の中から Spawn する — 新しい task は節の外側の handler(世界・時計)だけを持ち、run-worker の中の handler を持たない。
    (<- program-path str (program-path-of spec.program))
    ;; 子の送り手の口は本番の子の TaskClient・DetachedClient と同じく run-context の実行環境の宣言を持つ(cluster_foundation の組)。
    (<- child-env (| RuntimeEnv None) (runtime-env-of-context ctx))
    (val link (SimLink :queue parts.queue :actor spec.name :revision spec.revision :peer worker.name :runtime-env child-env))
    (<- plan SimPlan (PlanOf))
    (<- outside ProcessOutside (process-outside plan.per-process spec.name worker.name))
    (val child (SimChild :ctx ctx :program-path program-path :environ (dict spec.environ) :link link :pid pid
                         :passable (+ plan.passable outside.effects) :outside outside.handlers))
    (<- task Task (Spawn (sim-process worker.name spec child (.get truth.programs spec.program))))
    (<- (KeepHandle pid task))
    (resume None))
  (SignalJob [name pid stage]
    (<- (live-truth worker.name boot))
    (<- handle (| Task None) (HandleOf pid))
    (when (is-not handle None)
      (<- (Cancel handle)))
    (resume None))
  (ReapJob [name pid outcome exit-code]
    (<- truth HostTruth (live-truth worker.name boot))
    (<- (PutHostTruth worker.name (replace truth :processes (tuple (gfor p truth.processes :if (!= p.pid pid) p)))))
    (resume None))
  (RetireJob [name pid new-name]
    (<- truth HostTruth (live-truth worker.name boot))
    (<- (PutHostTruth worker.name
                      (replace truth :processes (tuple (gfor p truth.processes
                                                             (if (= p.pid pid) (replace p :name new-name :retired-from name) p))))))
    (when worker.retire-stops
      (<- handle (| Task None) (HandleOf pid))
      (when (is-not handle None)
        (<- (Cancel handle))))
    (resume None))
  (ReleaseLeases [job instance]
    (<- (live-truth worker.name boot))
    (<- parts SimParts (PartsOf))
    (<- plan SimPlan (PlanOf))
    (<- (release-leases (SimLink :queue parts.queue :actor worker.name :revision plan.revision :peer worker.name) job instance))
    (resume None))
  (PublishStatus [statuses note]
    (<- truth HostTruth (live-truth worker.name boot))
    (<- (PutHostTruth worker.name (replace truth :statuses (status-report statuses truth.task-echo truth.results))))
    (resume None))
  (WorkerStopRequested []
    (<- truth HostTruth (live-truth worker.name boot))
    (<- stopping bool (WorkersStopping))
    (resume (or stopping truth.stopping))))


(defk await-beat [name boot]
  {:pre [(: name str) (: boot str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "起こした後の待ち(または版をまだ知らない待ち)が、次の heartbeat が届くまで眠るため(上限 WAKE-HOLD-SECONDS — 同じ版で待ち直して
   空回りしない)。本番の WatchState.beat-done の待ちに当たる。"
  (<- truth HostTruth (HostTruthOf name))
  (when (= truth.boot boot)
    (<- bell Promise (CreatePromise))
    (<- (PutHostTruth name (replace truth :beat-bells (+ truth.beat-bells #(bell)))))
    (<- (promise-or-timeout bell.future WAKE-HOLD-SECONDS)))
  None)


(defk note-watch [name boot after reading]
  {:pre [(: name str) (: boot str) (: after int) (: reading WatchReading)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "program"}}
  "待ち 1 回の答えを宿の真実へ写すため(本番の CoordinatorLink.watch-loop の枝と同じ意味)。答え = 待ち続けるか。"
  (<- truth HostTruth (HostTruthOf name))
  (if (!= truth.boot boot)
      False
      (match reading.kind
        WatchKind.UNSUPPORTED (do (<- (PutHostTruth name (replace truth :watch-unsupported True)))
                                  False)
        WatchKind.FAILED (do (<- (Delay WATCH-RETRY-SECONDS))
                             True)
        WatchKind.CHANGED (do (<- (PutHostTruth name (replace truth :watch-confirmed True :woken True)))
                              True)
        WatchKind.UNCHANGED (do (<- (PutHostTruth name (replace truth :watch-confirmed True
                                                                :watch-after (if (= truth.watch-after after) reading.revision
                                                                                 truth.watch-after))))
                                True))))


(defk watch-desired [worker boot]
  {:pre [(: worker SimWorker) (: boot str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "worker の世代 1 つの名指しの待ち(本番の CoordinatorLink の背景の thread の代役 — worker-keeper が世代ごとに Spawn し、世代の終わりで
   取り消す): 前の heartbeat の版の後の変化を GET /watch で待ち、「変わった」なら拍に heartbeat を送らせ(woken)、次の heartbeat が
   届くまで眠る。待つ口が無い(404)・世代が終わったら抜ける。網は worker と同じ(切れていれば届かない)。"
  (<- parts SimParts (PartsOf))
  (<- plan SimPlan (PlanOf))
  (val link (SimLink :queue parts.queue :actor worker.name :revision plan.revision :peer worker.name))
  (var going True)
  (while going
    (<- truth HostTruth (HostTruthOf worker.name))
    (cond
      (or truth.down (!= truth.boot boot) truth.watch-unsupported) (:= going False)
      (or (is truth.watch-after None) truth.woken) (<- (await-beat worker.name boot))
      True (do (val after truth.watch-after)
               (<- answer tuple (send-request link "GET" "/watch" (watch-params after worker.name boot truth.watch-confirmed) None))
               (<- more bool (note-watch worker.name boot after (watch-reading (get answer 0) (get answer 1))))
               (:= going more))))
  boot)


(defk guarded-watch [worker boot]
  {:pre [(: worker SimWorker) (: boot str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "世代の名指しの待ちの task の本体: watch-desired を回し、思わぬ例外で止まれば理由を世界の記録に残して、その世代を拍ごとの heartbeat に
   戻すため(本番の「待ちの thread が止まった」の 1 行と watching の気づきに当たる — 模擬で黙って落ちると模擬の検が壊れを隠す)。
   世代の終わりの取り消しは記録しない。"
  (try
    (<- (watch-desired worker boot))
    (except [cancelled TaskCancelledError]
      (raise cancelled))
    (except [error Exception]
      (<- now int (now-epoch-ms))
      (<- (NoteWatchFailure (SimWatchFailure :worker worker.name :boot boot :at now
                                             :reason (.format "{}: {}" (. (type error) __name__) error))))))
  boot)


(defk run-sim-worker [worker policy boot]
  {:pre [(: worker SimWorker) (: policy WorkerPolicy) (: boot str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "worker の世代 1 つ: 本物の run-worker を偽の宿の上で回す(止まれの合図で全 job を止めの手順で回収して終わる)。"
  (<- (with-handlers [(sim-host worker boot)] (run-worker policy)))
  boot)


(defk generation-end [loop]
  {:pre [(: loop Task)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "worker の世代の run-worker が抜けるのを待ち、抜け方(stopped = 止めの手順で回収した・died = 死んだ)を返すため。"
  (try
    (<- (Wait loop))
    "stopped"
    (except [WorkerDied]
      "died")))


(defk worker-keeper [worker policy]
  {:pre [(: worker SimWorker) (: policy WorkerPolicy)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "worker 1 台の node の一生: 止まっていれば(死んだ・止めた・止まったまま始まる worker)StartWorker を待ち、今の世代の run-worker を
   回す。抜ければ(死んだ・止めた)次の世代を待つ。全 worker の止まれの合図で抜ける。"
  (var going True)
  (while going
    (<- revival (| Promise None) (RevivalOf worker.name))
    (when (is-not revival None)
      (<- (Wait revival.future)))
    (<- stopping bool (WorkersStopping))
    (if stopping
        (:= going False)
        (do (<- truth HostTruth (HostTruthOf worker.name))
            (<- loop Task (Spawn (run-sim-worker worker policy truth.boot)))
            ;; 世代ごとの名指しの待ち(本番の CoordinatorLink の背景の thread に当たる — 世代の終わりで取り消す)。
            (<- watcher Task (Spawn (guarded-watch worker truth.boot)))
            (<- (generation-end loop))
            (<- (Cancel watcher))
            (<- (WorkerEnded worker.name truth.boot))
            (<- again bool (WorkersStopping))
            (:= going (not again)))))
  worker.name)


;; --- coordinator の Pod ---------------------------------------------------------------------------------

(defhandler observe-requests
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 調停ループの一番内側: 取った要求のうち網の切れた worker から届いた物を落とし(送り手には接続の失敗 — 本番では届かない)、故障を
  ;; 入れている口(FailRoute)への物に注入した status で答えて調停ループへ渡さず、service の
  ;; 報告(ReportReady・ReportMetrics)を世界へ記録し、返事の前に落ちた時に接続の失敗を返す相手として取った要求を覚える。筋書きの
  ;; 止まり(止めの合図)と落ち(Persist の失敗 — 返事をせずに落ちる)を注入する。効果はそのまま外側(本物の組)へ出し直す。
  (NextRequests [timeout-seconds limit]
    (<- batch list effect)
    (<- cut frozenset (CutPeers))
    (<- failing dict (FailedRoutes))
    (val kept (lfor r batch :if (and (not-in r.peer cut) (not-in #(r.method r.path) failing)) r))
    (<- now int (now-epoch-ms))
    (<- reports tuple (reports-in kept now))
    (when reports
      (<- (NoteReports reports)))
    (<- (HoldRequests (tuple kept)))
    (for [r batch]
      (cond
        (in r.peer cut) (<- (CompletePromise r.slot #(None {"error" CUT-REASON})))
        (in #(r.method r.path) failing) (<- (CompletePromise r.slot #((get failing #(r.method r.path)) {"error" FAULT-REASON})))))
    (resume kept))
  (Reply [request status body]
    (<- (ReleaseRequest request))
    (<- effect)
    (resume None))
  (Persist [delta]
    (<- crash bool (PauseDue PAUSE-CRASH))
    (when crash
      (raise (OSError "sim: Persist の失敗(注入 — fsync の失敗)。返事をせずに落ちる")))
    (<- effect)
    ;; 書き終えた差分で終わった切り離した task の待ち手を起こす(送り手は読み直さずに待っている — proboscis/doeff#631)。
    (<- parts SimParts (PartsOf))
    (<- (ring-ended-tasks parts.queue delta))
    (resume None))
  (CoordinatorStopRequested []
    (<- due bool (PauseDue PAUSE-STOP))
    (if due
        (resume True)
        (do (<- asked bool effect)
            (resume asked)))))


(defk fail-open-requests [queue held reason]
  {:pre [(: queue RequestQueue) (: held tuple) (: reason str)] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "止まった・落ちた coordinator が返事をしていない要求(取った物と並んでいた物)に接続の失敗を返すため(送り手を待たせたままにしない)。"
  (val open (+ (list held) (list queue.pending)))
  (setattr queue "pending" [])
  (for [request open]
    (<- (CompletePromise request.slot #(None {"error" reason}))))
  (len open))


(defk coordinator-life [plan parts]
  {:pre [(: plan SimPlan) (: parts SimParts)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod の一生 1 つ: 置き場から読み直して(無ければ新しい状態で)本物の調停ループを emulated-handlers の上で回し、止まる
   (止めの合図)か落ちる(Persist の失敗)まで。答え = 止まり方。"
  (<- now int (now-epoch-ms))
  (val state (if (.exists parts.store)
                 (load-state NO-STATE-FILE parts.store now)
                 (ClusterState :started-ms now :task-prefix (fresh-task-prefix now))))
  (<- (CoordinatorStarted now))
  (setattr parts.queue "up" True)
  (try
    (<- (with-handlers (+ (emulated-handlers parts.queue parts.store parts.stop parts.kube) [observe-requests])
          (run-coordinator state plan.timing plan.naming)))
    "stopped"
    (except [error OSError]
      (str error))))


(defk coordinator-pod []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod の代役: 一生を回し、止まる・落ちるたびに返事の無い要求へ接続の失敗を返し、筋書きの止まっている秒の後に作り直す
   (止めの合図で止まり、止まっている秒が無ければ終わる)。答え = 起きた回数。"
  (<- plan SimPlan (PlanOf))
  (<- parts SimParts (PartsOf))
  (var lives 0)
  (var going True)
  (while going
    (<- outcome str (coordinator-life plan parts))
    (setattr parts.queue "up" False)
    (<- held tuple (TakeHeldRequests))
    (<- (fail-open-requests parts.queue held "coordinator が止まった・落ちた(返事なし)"))
    (<- ended int (now-epoch-ms))
    (<- (CoordinatorEnded ended outcome))
    (:= lives (+ lives 1))
    (<- down (| float None) (DowntimeOf))
    (if (is down None)
        (:= going False)
        (<- (Delay down))))
  lives)


(defk await-coordinator [queue]
  {:pre [(: queue RequestQueue)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod が受け付けを始めるまで待つため(宣言を先に送って接続の失敗にしない)。"
  (while (not queue.up)
    (<- (Delay QUEUE-WAIT-SECONDS)))
  None)


(defk first-beats-missing [names]
  {:pre [(: names tuple)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "動いている worker のうち、まだ 1 度も coordinator に名乗れていない(heartbeat の返事を受けていない)物が在るかを見るため(止まったまま
   始まる worker は待たない)。"
  (var missing False)
  (for [name names]
    (<- truth HostTruth (HostTruthOf name))
    (when (and (not truth.down) (= truth.beats 0))
      (:= missing True)))
  missing)


(defk await-workers [names]
  {:pre [(: names tuple)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "筋書きを始める前に、worker が 1 度ずつ coordinator に名乗るのを待つため(筋書きの最初の送りや名簿の読みが、起動の順に左右されない —
   本番の系は worker が名乗ってから使われる)。名乗れない worker が在っても WORKER-WAIT-SECONDS で待つのをやめる。"
  (<- started int (now-epoch-ms))
  (var waiting True)
  (while waiting
    (<- missing bool (first-beats-missing names))
    (<- now int (now-epoch-ms))
    (if (and missing (< (- now started) (* 1000 WORKER-WAIT-SECONDS)))
        (<- (Delay QUEUE-WAIT-SECONDS))
        (:= waiting False)))
  None)


(defk apply-declaration [link declaration]
  {:pre [(: link SimLink) (: declaration Declaration)] :post [(: % tuple)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言を本番の declare(declare.apply-declaration)と同じ順と本文で coordinator へ書くため: 詰めた Program を PUT /programs/<sha> で
   置いてから、Service ごとに無ければ POST(create-body)・在れば読んだ版を付けて PUT(spec-for-update)。答え = 書いた Service の名。"
  (for [#(sha blob) (sorted (.items declaration.programs))]
    (<- put tuple (send-request link "PUT" (+ "/programs/" sha) {}
                                {"blob" blob "versions" (get (get (get declaration.rows 0) "run") "versions")}))
    (answered-body put (+ "program " sha)))
  (for [row declaration.rows]
    (val path (+ "/resources/Service/" (url-quote (get row "name") :safe "")))
    (<- current tuple (send-request link "GET" path {} None))
    (<- written tuple (if (= (get current 0) 404)
                          (send-request link "POST" "/resources/Service" {} (create-body row None))
                          (send-request link "PUT" path {}
                                        (let [body (answered-object current (+ "Service " (get row "name")))]
                                          {"resourceVersion" (get body "resourceVersion") "spec" (spec-for-update row (get body "spec") None)}))))
    (answered-body written (+ "Service " (get row "name"))))
  (tuple (gfor row declaration.rows (get row "name"))))


;; --- process の終わりの待ち(AwaitProcessEnded — 世界が書きで起こす)---------------------------------------------

(defrecord DueWaiters
  "process の終わりを書いた後の待ち手の分け方(due-end-waiters の答え)。remaining = まだ待つ job → Promise の tuple・due = 起こす
   #(Promise 答え) の tuple(掛けた順)。"
  (#^ dict remaining)
  (#^ tuple due))


(defk ended-process [log job]
  {:pre [(: log tuple) (: job str)] :post [(: % (| SimProcess None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "AwaitProcessEnded の待つ相手(job の今の最後の process)が終わっていればその記録を、動いている・まだ無ければ None を返すため。"
  (val mine (lfor r log :if (= r.job job) r))
  (if (and mine (is-not (. (get mine -1) ended-ms) None)) (get mine -1) None))


(defk first-process [log job]
  {:pre [(: log tuple) (: job str)] :post [(: % (| SimProcess None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "AwaitProcessStarted の待つ相手(job の最初の process)が起きていればその記録を、まだなら None を返すため。"
  (next (gfor r log :if (= r.job job) r) None))


(defk ended-answer [process]
  {:pre [(: process SimProcess)] :post [(: % ProcessEnded)] :tags {:context "doeff-cluster" :role "judgment"}}
  "終わった process の記録を AwaitProcessEnded の答えにするため。"
  (ProcessEnded :job process.job :instance process.instance :worker process.worker))


(defk due-end-waiters [log waiters]
  {:pre [(: log tuple) (: waiters dict)] :post [(: % DueWaiters)] :tags {:context "doeff-cluster" :role "judgment"}}
  "process の終わりを記録に書いた直後に、待ち手のうち待つ相手の終わった物(起こす)と、まだ待つ物を分けるため。"
  (var remaining {})
  (var due #())
  (for [#(job promises) (.items waiters)]
    (<- ended (| SimProcess None) (ended-process log job))
    (if (is ended None)
        (:= remaining (| remaining {job promises}))
        (do (<- answer ProcessEnded (ended-answer ended))
            (:= due (+ due (tuple (gfor p promises #(p answer))))))))
  (DueWaiters :remaining remaining :due due))


;; --- 世界 -----------------------------------------------------------------------------------------------

(defhandler sim-world [#^ SimPlan plan]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 筋(系・worker・environ の上書き)は検ごとに違う値で、世界の外側に Ask へ答える物を置くと検の筋書きからも読めて
  ;; しまう(世界の答えは sim の中の仕組みだけが読む)。
  ;; sim の世界 1 つ(coordinator の部品と一生・worker の宿の真実と世代・process の把手と記録・process の中の task・届いた報告・準備・
  ;; 網の切断・止まれの合図)を session に持ち、仕組みの effect と検の effect に答える。session の値の置き場(doeff_core_effects の
  ;; state)はこの handler の外側に要る(sim-cluster が置く)。真実(本当に動いている process・届いた報告)はここが持つ — coordinator の
  ;; 信念(状態の中の報告)ではない。節の session の書きは scheduler の切り替わる effect より前に済ませる(頭の註)。
  (session val parts !(parts-of plan))
  (session var hosts !(fresh-hosts plan))
  (session var generations (dfor w plan.workers w.name 1))
  (session var handles {})
  (session var children {})
  (session var finished (frozenset))
  (session var kills {})
  (session var next-pid 0)
  (session var log #())
  (session var reports #())
  (session var preparations #())
  (session var stopping False)
  (session var stop-waiters {})
  (session var revivals {})
  (session var cuts {})
  (session var failing {})
  (session var held #())
  (session var pauses #())
  (session var downtime None)
  (session var runs #())
  (session var end-waiters {})
  (session var watch-failures #())
  (session var start-waiters {})
  (PlanOf []
    (resume plan))
  (PartsOf []
    (resume parts))
  (HostTruthOf [name]
    (resume (get hosts name)))
  (PutHostTruth [name truth]
    (:= hosts (| hosts {name truth}))
    (resume None))
  (NextPid []
    (:= next-pid (+ next-pid 1))
    (resume next-pid))
  (KeepHandle [pid task]
    (:= handles (| handles {pid task}))
    (when (in pid kills)
      (<- (Cancel task)))
    (resume None))
  (HandleOf [pid]
    (resume (.get handles pid)))
  (KeepChild [pid task]
    (if (in pid finished)
        (do (<- (Cancel task))
            (resume None))
        (do (:= children (| children {pid (+ (.get children pid #()) #(task))}))
            (resume None))))
  (NoteProcess [process]
    (:= log (+ log #(process)))
    ;; その job の最初の process を待つ AwaitProcessStarted の待ち手を起こす(読み直さない — 書きで起こす)。
    (val starting (.get start-waiters process.job #()))
    (:= start-waiters (dfor #(k v) (.items start-waiters) :if (!= k process.job) k v))
    (<- first (| SimProcess None) (first-process log process.job))
    (for [promise starting]
      (<- (CompletePromise promise first)))
    (resume None))
  (EndProcess [worker pid ended]
    (<- now int (now-epoch-ms))
    (val truth (get hosts worker))
    (val view (next (gfor p truth.processes :if (= p.pid pid) p) None))
    (val task-id (if (and (is-not view None) view.spec.once) (cut view.spec.name 5 None) None))
    (val spawned (.get children pid #()))
    (:= hosts (| hosts {worker (replace truth
                                        :processes (tuple (gfor p truth.processes (if (= p.pid pid) (replace p :exit-code ended.code) p)))
                                        :results (if (and task-id (is-not ended.result None))
                                                     (| truth.results {task-id ended.result})
                                                     truth.results))}))
    (:= log (tuple (gfor r log (if (= r.pid pid) (replace r :ended-ms now :exit-code ended.code :detail ended.detail :value ended.value) r))))
    (:= handles (dfor #(k v) (.items handles) :if (!= k pid) k v))
    (:= children (dfor #(k v) (.items children) :if (!= k pid) k v))
    (:= finished (| finished (frozenset [pid])))
    ;; 待つ相手の process が終わった AwaitProcessEnded の待ち手を起こす(読み直さない — 書きで起こす)。
    (<- woken DueWaiters (due-end-waiters log end-waiters))
    (:= end-waiters woken.remaining)
    ;; process が終われば、中で Spawn した task も止まる(本番は子 process ごと消える)。
    (for [task spawned]
      (<- (Cancel task)))
    (for [#(promise answer) woken.due]
      (<- (CompletePromise promise answer)))
    (resume None))
  (NoteWatchFailure [failure]
    (:= watch-failures (+ watch-failures #(failure)))
    (val truth (get hosts failure.worker))
    (when (= truth.boot failure.boot)
      (:= hosts (| hosts {failure.worker (replace truth :watch-failure failure.reason)})))
    (resume None))
  (WatchFailuresOf [name]
    (resume (tuple (gfor f watch-failures :if (= f.worker name) f))))
  (KillOf [pid]
    (resume (.get kills pid)))
  (NoteReports [batch]
    (:= reports (+ reports batch))
    (resume None))
  (NotePreparation [preparation]
    (:= preparations (+ preparations #(preparation)))
    (resume None))
  (WorkersStopping []
    (resume stopping))
  (StopWorkers []
    (val waiting (list (.values revivals)))
    (:= stopping True)
    (:= revivals {})
    (for [promise waiting]
      (<- (CompletePromise promise None)))
    (resume None))
  (WorkerEnded [name boot]
    (val truth (get hosts name))
    (val waiting (.get stop-waiters name #()))
    (when (= truth.boot boot)
      (:= hosts (| hosts {name (replace truth :down True :stopping False)})))
    (:= stop-waiters (dfor #(k v) (.items stop-waiters) :if (!= k name) k v))
    (for [promise waiting]
      (<- (CompletePromise promise None)))
    (resume None))
  (RevivalOf [name]
    ;; 全 worker が止まる時は待たせない(完了させる者が居ない)。
    (if (and (. (get hosts name) down) (not stopping))
        (do (<- promise Promise (CreatePromise))
            (:= revivals (| revivals {name promise}))
            (resume promise))
        (resume None)))
  (CutPeers []
    (<- now int (now-epoch-ms))
    (resume (frozenset (gfor #(name until) (.items cuts) :if (> until now) name))))
  (FailedRoutes []
    (<- now int (now-epoch-ms))
    (resume (dfor #(route #(status until)) (.items failing) :if (> until now) route status)))
  (HoldRequests [batch]
    (:= held (+ held batch))
    (resume None))
  (ReleaseRequest [request]
    (:= held (tuple (gfor r held :if (is-not r request) r)))
    (resume None))
  (TakeHeldRequests []
    (val taken held)
    (:= held #())
    (resume taken))
  (PauseDue [kind]
    (val due (next (gfor p pauses :if (= (get p 0) kind) p) None))
    (if (is due None)
        (resume False)
        (do (:= pauses (tuple (gfor p pauses :if (is-not p due) p)))
            (:= downtime (get due 1))
            (resume True))))
  (DowntimeOf []
    (val taken downtime)
    (:= downtime None)
    (resume taken))
  (CoordinatorStarted [ms]
    (:= runs (+ runs #((SimCoordinatorRun :started-ms ms))))
    (resume None))
  (CoordinatorEnded [ms outcome]
    (:= runs (tuple (gfor #(i run) (enumerate runs) (if (= i (- (len runs) 1)) (replace run :ended-ms ms :outcome outcome) run))))
    (resume None))
  ;; --- 検の effect ---
  (Crash [name]
    (val victims (lfor r log :if (and (= r.job name) (is r.ended-ms None) (in r.pid handles)) r.pid))
    (:= kills (| kills (dfor pid victims pid (SimExit :code 1 :result None :detail "Crash"))))
    (for [pid victims]
      (<- (Cancel (get handles pid))))
    (resume (len victims)))
  (KillWorker [name]
    (<- now int (now-epoch-ms))
    (val truth (get hosts name))
    (val victims (if truth.down [] (lfor r log :if (and (= r.worker name) (is r.ended-ms None)) r.pid)))
    (val killed (SimExit :code KILLED-CODE :result None :detail "worker が死んだ(node ごと止まった)"))
    (:= kills (| kills (dfor pid victims pid killed)))
    (:= hosts (| hosts {name (replace truth :down True)}))
    ;; 記録の終わりはここで書く(走り出す前に取り消された process は EndProcess を書かない — 動いているように見せない)。
    (:= log (tuple (gfor r log (if (in r.pid victims) (replace r :ended-ms now :exit-code killed.code :detail killed.detail) r))))
    (<- woken DueWaiters (due-end-waiters log end-waiters))
    (:= end-waiters woken.remaining)
    ;; 把手がまだ無い process(StartJob の Spawn と KeepHandle の間)は KeepHandle がその場で取り消す。
    (for [pid victims]
      (when (in pid handles)
        (<- (Cancel (get handles pid)))))
    (for [#(promise answer) woken.due]
      (<- (CompletePromise promise answer)))
    (resume (len victims)))
  (StopWorker [name]
    (val truth (get hosts name))
    (if truth.down
        (resume None)
        (do (<- promise Promise (CreatePromise))
            (:= hosts (| hosts {name (replace truth :stopping True)}))
            (:= stop-waiters (| stop-waiters {name (+ (.get stop-waiters name #()) #(promise))}))
            (<- (Wait promise.future))
            (resume None))))
  (StartWorker [name]
    (val truth (get hosts name))
    (if (not truth.down)
        (resume False)
        (do (<- now int (now-epoch-ms))
            (val generation (+ (get generations name) 1))
            (<- fresh HostTruth (fresh-truth name generation now plan.timing.fence-ms))
            (val revival (.get revivals name))
            (:= generations (| generations {name generation}))
            (:= hosts (| hosts {name fresh}))
            (:= revivals (dfor #(k v) (.items revivals) :if (!= k name) k v))
            (when (is-not revival None)
              (<- (CompletePromise revival None)))
            (resume True))))
  (CutWorker [name seconds]
    (<- now int (now-epoch-ms))
    (:= cuts (| cuts {name (+ now (int (* 1000 seconds)))}))
    (resume None))
  (FailRoute [method path status seconds]
    (<- now int (now-epoch-ms))
    (:= failing (| failing {#(method path) #(status (+ now (int (* 1000 seconds))))}))
    (resume None))
  (DrainWorker [name ttl-seconds]
    (val request (drain-request name ttl-seconds (. (get hosts name) boot)))
    (<- link SimLink (control-link parts.queue plan.revision))
    (<- answer tuple (send-shaped link request))
    (resume (if (is (get answer 0) None)
                {"error" (unreached-reason answer)}
                {"status" (get answer 0) "body" (get answer 1)})))
  (PreparationsOf [name]
    (resume (tuple (gfor p preparations :if (= p.worker name) p))))
  (StopCoordinator [seconds]
    (:= pauses (+ pauses #(#(PAUSE-STOP (float seconds)))))
    ;; 眠っている coordinator に知らせる(要求の無い拍を飛ばす列は、本番の 1 秒の拍が止めに気づく刻まで眠り直す — nudge-takers)。
    (<- (nudge-takers parts.queue))
    (resume None))
  (CrashCoordinator [seconds]
    (:= pauses (+ pauses #(#(PAUSE-CRASH (float seconds)))))
    (resume None))
  (CoordinatorRuns []
    (resume runs))
  (ClientLink []
    (resume (SimLink :queue parts.queue :actor CLIENT-NAME :revision plan.revision :peer CLIENT-NAME)))
  (Redeclare [system]
    (<- declaration Declaration (declaration-of system plan.revision plan.environ plan.runtime-env))
    (<- link SimLink (control-link parts.queue plan.revision))
    (<- names tuple (apply-declaration link declaration))
    (resume names))
  (DeclareRollout [name spec]
    (<- link SimLink (control-link parts.queue plan.revision))
    (<- answer tuple (send-request link "POST" "/resources/Rollout" {} {"name" name "spec" (deepcopy spec)}))
    (resume (answered-body answer (+ "Rollout を作れない: " name))))
  (KubeCalls []
    (resume (deepcopy (tuple parts.kube.calls))))
  (SettleDeployment [namespace name ready]
    (.settle parts.kube (+ namespace "/" name) ready)
    (resume None))
  (ReportsOf [name]
    (resume (tuple (gfor r reports :if (= r.job name) r))))
  (ProcessesOf [name]
    (resume (tuple (gfor r log :if (= r.job name) r))))
  (AwaitProcessStarted [name]
    (<- first (| SimProcess None) (first-process log name))
    (if (is-not first None)
        (resume first)
        (do (<- promise Promise (CreatePromise))
            (:= start-waiters (| start-waiters {name (+ (.get start-waiters name #()) #(promise))}))
            (<- started SimProcess (Wait promise.future))
            (resume started))))
  (AwaitProcessEnded [job timeout-seconds]
    ;; 待つ相手(job の今の最後の process — まだ無ければ最初に起きる process)が終わっていればすぐ答え、それ以外は Promise を掛けて、
    ;; 世界が process の終わりを書いた時(EndProcess・KillWorker)に起きる。読み直さない(proboscis/doeff#631)。
    (<- last-process (| SimProcess None) (ended-process log job))
    (cond
      (is-not last-process None)
        (do (<- ended-reply ProcessEnded (ended-answer last-process))
            (resume ended-reply))
      (and (is-not timeout-seconds None) (<= timeout-seconds 0))
        (resume (ProcessWaitExpired :job job :waited-seconds 0.0))
      True
        (do (<- promise Promise (CreatePromise))
            (:= end-waiters (| end-waiters {job (+ (.get end-waiters job #()) #(promise))}))
            ;; 上限なしの待ちは時間切れの答えを持たない(ProcessWaitExpired の秒は期限つきの待ちにだけ在る)。
            (if (is timeout-seconds None)
                (do (<- ended-later ProcessEnded (Wait promise.future))
                    (resume ended-later))
                (do (<- answer (promise-or-timeout promise.future timeout-seconds))
                    (resume (if (is answer None) (ProcessWaitExpired :job job :waited-seconds (float timeout-seconds)) answer)))))))
  (ReadinessOf [name]
    (<- link SimLink (control-link parts.queue plan.revision))
    (<- answer tuple (send-request link "GET" (+ "/resources/Service/" (url-quote name :safe "")) {} None))
    (resume (match (get answer 0)
              200 (SimReadiness :state (get (get answer 1) "status" "ready")
                                :reason (str (.get (get (get answer 1) "status") "readyReason" "")))
              _ (SimReadiness :state "Missing" :reason (str (get answer 1))))))
  (SharedRows [prefix]
    (<- link SimLink (control-link parts.queue plan.revision))
    (<- answer tuple (send-shaped link (board-read-request prefix)))
    (resume (answered-body answer "盤を読めない")))
  (ReadCoordinator [path]
    (<- link SimLink (control-link parts.queue plan.revision))
    (<- answer tuple (send-request link "GET" path {} None))
    (resume (answered-body answer (+ "GET " path)))))


;; --- 入口 -----------------------------------------------------------------------------------------------

(defk sim-main [scenario]
  {:pre [(: scenario (| Program EffectBase))] :post [(: % "scenario の答え(型は筋書きごと)")] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod を起こし、宣言を書き、worker を並べてから scenario を(筋書きの送り手の口の下で)走らせ、終われば worker・
   coordinator の順に止めるため。"
  (<- plan SimPlan (PlanOf))
  (<- parts SimParts (PartsOf))
  (<- pod Task (Spawn (coordinator-pod)))
  (<- (await-coordinator parts.queue))
  (<- control SimLink (control-link parts.queue plan.revision))
  (<- (apply-declaration control plan.declaration))
  (var keepers [])
  (for [w plan.workers]
    (<- t Task (Spawn (worker-keeper w plan.policy)))
    (:= keepers (+ keepers [t])))
  (<- (await-workers (tuple (gfor w plan.workers w.name))))
  (<- client SimLink (ClientLink))
  (try
    (<- answer (with-handlers [(coordinator-answers client)] scenario))
    answer
    (finally
      (<- (StopWorkers))
      (<- (Gather #* keepers))
      (setattr parts.stop "requested" True)
      (<- (nudge-takers parts.queue))
      (<- (Wait pod)))))


(defk sim-under-clock [system scenario workers environ revision timing policy outside store [deployments None] [runtime-env None]
                       [skip-idle False]]
  {:pre [(: system System) (: scenario (| Program EffectBase)) (: workers (| tuple None)) (: environ (| dict None)) (: revision str)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None)) (: store (| Callable None))
         (: deployments (| dict None)) (: runtime-env (| RuntimeEnv None)) (: skip-idle bool)]
   :post [(: % "scenario の答え(型は筋書きごと)")]
   :tags {:context "doeff-cluster" :role "program"}}
  "入口(sim-cluster・wall-sim-cluster)が選んだ時計の内側で、時計の今を起点に筋を作り(引数を検めて断る)、session の値の置き場・sim の
   外の世界・sim の世界を並べて sim-main を走らせるため。時計の違いは入口が並べる handler だけで、ここから内側は同じ。"
  (<- start-ms int (now-epoch-ms))
  (<- plan SimPlan (sim-plan system workers environ revision start-ms timing policy outside store deployments runtime-env skip-idle))
  (<- answer (with-handlers [(session-store) #* (if (is outside None) [] outside.handlers) (sim-world plan)]
               (sim-main scenario)))
  answer)


(defk sim-cluster [system scenario * [workers None] [environ None] [revision "sim"] [start-ms SIM-START-MS] [timing None] [policy None]
                  [outside None] [store None] [deployments None] [runtime-env None] [skip-idle True]]
  {:pre [(: system System) (: scenario (| Program EffectBase)) (: workers (| tuple None)) (: environ (| dict None)) (: revision str) (: start-ms int)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None)) (: store (| Callable None))
         (: deployments (| dict None)) (: runtime-env (| RuntimeEnv None)) (: skip-idle bool)]
   :post [(: % "scenario の答え(型は筋書きごと)")]
   :tags {:context "doeff-cluster" :role "entry"}}
  "系 system(sim の土台で作った System の値)を本物の coordinator と worker の上で走らせ、scenario(検の筋書きの Program — 同じ
   scheduler・同じ仮想の時計で並んで走る)の答えを返す。workers = SimWorker の tuple(既定 = 全 job の needs の和を提供する 1 台)・
   environ = job 名 → 宣言の :environ に重ねる環境変数(宣言に無い名は断る)・revision = 宣言の版・start-ms = 仮想の時計の起点・
   timing / policy = coordinator と worker の時間の設定(既定 = 本番の既定)・outside = sim の外の世界(SimOutside — 業務の外の系の模擬の
   handler と、それが答える effect の型。時計の内側・sim の世界の外側に置き、柵はその型も通す)・store = coordinator の置き場を作る
   関数(引数なし → MemoryWalStore の値 — 既定 = MemoryWalStore。反例の壊れた置き場 — 書いたふり・読み直せない・欄を落とす — を
   派生の class で渡す。1 回の走りに 1 回だけ呼び、作り直した coordinator も同じ置き場から読み直す)。自分で scheduler を持つ(外に
   scheduler が在っても無くても走る)。deployments = 「namespace/名」→ KubeMemory の観測の dict(specReplicas・replicas・readyReplicas 等)。
   走りごとに深い写しを作り、初期値を変えない。既定は空。Pod の進行は SettleDeployment。runtime-env = 宣言の実行環境の宣言(本番の
   declare の --runtime-env と同じ — 本物の worker が準備し、子の run-context の runtime-env になる。既定 None)。壁の時計で回すなら
   wall-sim-cluster。skip-idle = coordinator が要求の無い間、本番の判断で何も変えない拍を一度に眠る(既定 = 真。判断の刻は 1 秒ごとの
   拍と同じ — 同値の検が偽と真を比べる・2026-09-30)。"
  (<- answer (scheduled (with-handlers [(sim-time-handler :start-time (datetime-of-epoch-ms start-ms))]
                          (sim-under-clock system scenario workers environ revision timing policy outside store deployments runtime-env
                                           :skip-idle skip-idle))))
  answer)


(defk wall-sim-cluster [system scenario * [workers None] [environ None] [revision "sim"] [timing None] [policy None] [outside None]
                       [store None] [deployments None] [runtime-env None]]
  {:pre [(: system System) (: scenario (| Program EffectBase)) (: workers (| tuple None)) (: environ (| dict None)) (: revision str)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None)) (: store (| Callable None))
         (: deployments (| dict None)) (: runtime-env (| RuntimeEnv None))]
   :post [(: % "scenario の答え(型は筋書きごと)")]
   :tags {:context "doeff-cluster" :role "entry"}}
  "sim-cluster と同じ系・同じ本物の coordinator と worker・同じ偽の宿と柵を、壁の時計で走らせ、scenario の答えを返す(引数の意味は
   sim-cluster と同じ — 起点は無く、今の時刻から始まる)。時計 = doeff-time の async-time-handler(Delay は実時間で待つ・GetTime は今の
   時刻)と、その待ちと Await に答える await-handler(process で共有の event loop — 外の thread と本物の socket の I/O もそこで走る)。
   筋書きは Await を出してよい(ここの await-handler が答える)。job の Await は柵を通らない(SIM-PASSABLE — 本番の子と同じく土台が
   await-handler を並べる)ので、本物の待ち受けを持つ job は土台に await-handler を置くか、その I/O を outside の handler に置く
   (時計の内側なので、ここの await-handler が答える)。自分で scheduler を持つ。"
  (<- answer (scheduled (with-handlers [(await-handler) (async-time-handler)]
                          (sim-under-clock system scenario workers environ revision timing policy outside store deployments runtime-env))))
  answer)
