;;; 手元の runner sim-cluster — 系(defsystem の関数を sim の土台で呼んだ System の値)を、本物の coordinator と本物の worker の上で、
;;; 1 process・仮想の時計で走らせる(ADR-DOE-CLUSTER-001・計画 2.6・10.2・段 5・5b)。
;;;
;;;   (<- answer (sim-cluster (lab sim-foundation) (scenario) :workers #((SimWorker :name "w1" :provides #{"net"})) :environ {"tally" {"STEP" "3"}}))
;;;
;;; 中身(写しを作らない — 起こし直しの間隔・readiness の窓・handoff の期限・needs ⊆ provides の置き方・lease・fence は本物が決める):
;;;   - coordinator の Pod = 本物の run-coordinator を coordinator_handler_sets.emulated-handlers(要求の列 RequestQueue・memory の
;;;     置き場・偽の k8s)の上で回す。止まれば(止めの合図・Persist の失敗)返事の無い要求に接続の失敗を返し、止まっている秒の後に同じ
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
;;;     消える)。
;;;   - 宿の答え(host-answers — process ごと)= host_contract.HOST-CONTRACT の 3 つ(run-context・Program の path・宣言の environ の名の
;;;     Ask)と ReportReady・ReportMetrics。クラスタの約束の答え(coordinator-answers — 送り手の口 SimLink ごと)= ReadShared / WriteShared・
;;;     LeaseOp・RemoteJob・SubmitDetached / AwaitDetached / CancelDetached / ReleaseDetached / ReadRunners・WarmRuntimeEnv / ReadWarmState。
;;;     本番では土台の HTTP の handler が coordinator へ送る物で、要求の形は本番の送り手と同じ関数(report_client.report-request・
;;;     shared_handlers.board-*-request / lease-request・remote.task-submit-body / outcome-of / settled-value・detached.detached-path /
;;;     detached-submit-body / detached-refusal / awaited-answer / warm-request-body)。何度送っても同じ意味の要求(読み・lease の claim と
;;;     renew・切り離した task の口・温める表)は、本番の send-idempotent と同じ期限と間で、仮想の時計で送り直す。どちらの答えも柵の内側に
;;;     在るので世界の effect を出さず、要求の列を値で受けて scheduler と時計の effect だけで coordinator と話す。
;;;   - 柵(fence)= host_contract.SIM-PASSABLE(scheduler と doeff-time の時計の effect)だけを外へ通し、それ以外を本番の子と同じ
;;;     doeff.UnhandledEffect で Program へ投げ返す — sim の外側(検の handler・sim の世界)が本番には無い答えを黙って返さない。
;;;   - 筋書き(scenario)は検の側の呼び手(本番の DetachedClient などを持つ機体の外の process)として、同じ coordinator-answers(送り手 sim-client)
;;;     の下で走る。別の送り手(実行環境の宣言・版の違う呼び手)が要る筋書きは ClientLink の値を置き換えて coordinator-answers を自分で被せる。
;;;
;;; 検の effect(sim の世界が答える — scenario の中で出す。service の Program が出すと柵で落ちる):
;;;   Crash 名                  動いている process を exit 1 で落とす(答え = 落とした数)。worker が本物の判断で起こし直す。
;;;   Redeclare 系              宣言し直す(本番の declare と同じ順で Program を置いてから Service の行を書く — update に従い recreate / handoff)。
;;;   ReportsOf 名              coordinator に届いた ReportReady / ReportMetrics の列(SimReport)。
;;;   ReadinessOf 名            coordinator の Service の status の ready(SimReadiness — Ready / NotReady / Unknown / Missing)。
;;;   ProcessesOf 名            その job の process の列(SimProcess — 世代・worker・始まり・終わり・exit-code)。task は task/<id>。
;;;   SharedRows 頭             coordinator の盤の行(鍵が頭で始まる物)。
;;;   ReadCoordinator path      coordinator の口の GET の本文(/state・/workers/<名>・/metrics など)。
;;;   StopCoordinator 秒        coordinator の Pod を優雅に止め(次の拍の止めの合図)、秒の間止めてから作り直す(置き場から読み直す)。
;;;   CrashCoordinator 秒       次の Persist を失敗させる(返事をせずに落ちる — 取った要求の送り手には接続の失敗)。秒の後に作り直す。
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
;;;   ClientLink                筋書きの送り手の口(SimLink — 置き換えて coordinator-answers を被せれば別の送り手になる)。
;;; 時間を進めるのは scenario の Delay(doeff-time)。scenario が終われば worker を止め(全 job を止めの手順で回収)、coordinator を止める。
;;;
;;; 本番との既知の差(検めない):
;;;   - 1 process なので、import した module の大域の状態は job の間で共有されうる(改訂 1 の Q)。effect 以外の共有は機械で全部は断れない。
;;;   - environ は宿が宣言の :environ の名の Ask にだけ答える(本番の os.environ を読む handler は PATH などの宣言の外の名にも答える)。
;;;   - 土台の関数の :needs が中の handler の :needs を漏らしていても見つからない(計画 9 の P — doeff-linter の照合は別便)。
;;;   - sim の土台は scheduler と時計を含まないので、本番の土台に scheduler を入れ忘れてもここでは見つからない(計画 7)。
;;;   - coordinator に届かない・断られた時の例外の型は RemoteJobFailed(本番は httpx の例外)。書きの要求は 1 回だけ送る(本番の
;;;     CoordinatorEndpoint は接続の段の失敗だけを間を置いて 4 回まで送り直す)。
;;;   - 実行環境の root は準備の中身(git・uv・disk)を模擬しない(prepare-seconds の後に揃うか env-failure で終わる)。disk は常に ok。
;;;   - process の中で Spawn した task は 1 段の包み(tracked-child)の task として起きる(Program が受ける把手は包みの物 — 取り消し・待ち・
;;;     答えは同じ)。
;;;
;;; 状態の置き場(ADR-DOE-HY-007): 世界の状態は世界の handler(sim-world)の session var に置き、値は defrecord、変化は effect で書く。
;;; 要求の列・memory の置き場・停止の合図・偽の k8s は coordinator_handler_sets の既存の資源(世界の session val が 1 回だけ作る)。
;;; 世界の節は session の書きを scheduler の切り替わる effect(Spawn・Wait・CompletePromise)より前に済ませる(切り替わりの間に他の task
;;; が書いた値を、節の古い写しで上書きしない)。
(require doeff-hy.macros [defk deff defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import pathlib [Path])
(import urllib.parse [quote :as url-quote unquote :as url-unquote])
(import doeff [with-handlers EffectBase UnhandledEffect DoExpr Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [state :as session-store])
(import doeff_core_effects.scheduler [scheduled CreatePromise CompletePromise Wait Spawn Gather Cancel Promise Task
                                      TaskCancelledError])
(import doeff_time [Delay sim-time-handler])
(import doeff_cluster.clock [now-epoch-ms datetime-of-epoch-ms])
(import .cluster_model [ClusterState ClusterTiming ClusterNaming Request NextRequests Reply Persist CoordinatorStopRequested
                        PlainText])
(import .cluster_policy [fresh-task-prefix])
(import .coordinator [run-coordinator load-state])
(import .coordinator_http [IDEMPOTENT-DEADLINE-SECONDS RESEND-PAUSE-SECONDS])
(import .coordinator_inbox [StopState])
(import .coordinator_handler_sets [RequestQueue MemoryWalStore emulated-handlers])
(import .kube_handlers [KubeMemory])
(import .declare [create-body spec-for-update])
(import .detached [detached-path detached-submit-body detached-refusal submit-unreachable awaited-answer runner-facts-of-view
                   runners-unreachable warm-request-body warm-path absent-warm-state])
(import .detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached ReadRunners DetachedSubmitted
                         DetachedSubmitAnswer DetachedAwaited RunnersUnreachable])
(import .drain_client [drain-request DRAIN-DEADLINE-SECONDS DRAIN-TTL-MARGIN-SECONDS])
(import .handlers [declared-job-spec task-spec heartbeat-body status-report desired-when-unreachable env-report env-heartbeat-part
                   warm-env-of-row])
(import .host_contract [HOST-CONTRACT SIM-PASSABLE])
(import .job_context [RunContext])
(import .job_entry [decoded-program])
(import .metrics_model [ReportMetrics])
(import .readiness_model [ReportReady])
(import .remote [task-submit-body outcome-of settled-value])
(import .remote_model [RemoteJob RemoteJobFailed TaskSucceeded TaskFailed encode-program encode-outcome failed-from program-sha
                       current-versions])
(import .report_client [report-request])
(import .runtime_env_model [RuntimeEnv EnvFailure runtime-env->json current-platform])
(import .semaphore_model [LeaseOp SEMAPHORE-PREFIX drop-holders])
(import .service_model [System Declaration system-declaration])
(import .shared_handlers [board-read-request board-write-request lease-request])
(import .shared_model [ReadShared WriteShared])
(import .warm_model [WarmRuntimeEnv ReadWarmState WarmState warm-state-of-json])
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


;; --- 公開の値 --------------------------------------------------------------------------------------------

(defrecord SimWorker
  "sim の worker 1 台(本番の worker の --provides・--exclusive・--capacity・node に当たる)。provides = 提供する能力の名・
   exclusive = 専用の能力(この能力を needs に持つ job だけを受ける)・node = 置かれた k8s の node の名(空 = k8s の外)・
   versions = 名乗る版(None = 送り手と同じ current-versions — 違えば版の合わない task は置かれない)・prepare-seconds = コードの木と
   実行環境の root の準備にかかる仮想の秒・env-failure = 実行環境の root の準備がこの失敗で終わる worker(None = 揃う)・
   starts-down = 止まったまま始まる(StartWorker で起きる — 後から加わる node)・ignores-fence = 反例の世界だけの壊れた worker
   (coordinator に届かない間 fence を越えても job を止めない — 本番の worker_policy の判断を使わない)。"
  (#^ str name)
  (#^ frozenset provides)
  (setv #^ frozenset exclusive (frozenset))
  (setv #^ int capacity 10)
  (setv #^ str node "")
  (setv #^ (| dict None) versions None)
  (setv #^ float prepare-seconds 0.0)
  (setv #^ (| EnvFailure None) env-failure None)
  (setv #^ bool starts-down False)
  (setv #^ bool ignores-fence False))


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


(defrecord SimCoordinatorRun
  "coordinator の Pod の一生 1 つ(CoordinatorRuns の答えの要素)。outcome = 止まり方(stopped = 止めの合図・それ以外は落ちた理由の
   1 行)— まだ動いていれば ended-ms は None で outcome は空。"
  (#^ int started-ms)
  (setv #^ (| int None) ended-ms None)
  (setv #^ str outcome ""))


(defrecord SimLink
  "coordinator へ話す送り手の口 1 つ(クラスタの約束の答え coordinator-answers の引数)。queue = coordinator の受け口(要求の列)・
   actor = 書きの送り手(X-Actor)・revision = 送り手の版(task の revision)・peer = 送り手の居る所(網の切断は worker の名で数える)・
   runtime-env = 切り離した task の実行環境の宣言(本番の DetachedClient の runtime-env — None = 送り手の版のコードだけ)。"
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

(defeffect ClientLink
  "検の effect: 筋書きの送り手の口(SimLink — 送り手 sim-client)。値を置き換えて coordinator-answers を被せれば、実行環境の宣言や版の
   違う送り手として話せる。"
  {:answer SimLink
   :tags {:context "doeff-cluster" :role "intent"}})


;; --- sim の中の値 ---------------------------------------------------------------------------------------

(defrecord SimOutside
  "sim の外の世界(本番では job の土台の handler が外の系 — 業務の store・外部の API — と話して答える effect に、sim では系の外側で
   答える物)。handlers = sim の全部の job と筋書きの外側に置く handler の組(外側が先 — 仮想の時計の内側)・effects = それが答える effect の型
   (柵が外へ通す — isinstance で数えるので基底の型でよい)。job は effect を通してだけ外の世界を共有する(object を共有しない)。
   per-process = process ごとの外の世界を作る関数 (job の名 worker の名) → ProcessOutside(handler と、その process の柵だけが通す型 —
   None = 無し)。宿が process を起こす
   時に 1 回呼び、柵の外側・sim の世界の内側に並べる — 本番で job ごと・機体ごとに違う外の口(記録の service の身元の token・預かり所の
   借り手・機体の session の置き場)を、共有の外の世界(handlers)の手前で答えるため(agora-redesign #833 の条件「sim-cluster は担い手ごとに
   handler の組を持つ」・#834)。作る handler も effects に載った型にだけ答える(柵がそれ以外を通さない)。"
  (#^ list handlers)
  (#^ tuple effects)
  (setv #^ (| Callable None) per-process None))


(defrecord SimPlan
  "sim の 1 回の走りの筋(sim-cluster が引数から作る)。declaration = 最初の宣言(environ の上書きを重ねた行)・environ = job 名 →
   上書きの環境変数(Redeclare にも重ねる)・passable = 柵が外へ通す effect の型(SIM-PASSABLE と外の世界の effects)・
   per-process = process ごとの外の handler の組を作る関数(SimOutside.per-process — None = 無し)。"
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
  (setv #^ (| Callable None) per-process None))


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
   返事の行(id → 行)・beats = coordinator が返事をした heartbeat の数・down = 死んだか止まった・stopping = 優雅な停止を頼まれた。"
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
  (setv #^ bool stopping False))


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


(defk declaration-of [system revision environ]
  {:pre [(: system System) (: revision str) (: environ dict)] :post [(: % Declaration)] :tags {:context "doeff-cluster" :role "judgment"}}
  "系 → coordinator へ渡す宣言(本番の declare と同じ system-declaration)に、job ごとの environ の上書きを重ねるため(計画 2.7 の H・
   改訂 1 の M — whole.hy の overrides の置き換え先)。上書きの規則(系に無い job・宣言の :environ に無い名・文字列でない値は断る)は
   本番の declare と同じ 1 つ(service_model.environ-overlay-refusal)。"
  (system-declaration system revision :environ environ))


(defk sim-plan [system workers environ revision start-ms timing policy outside]
  {:pre [(: system System) (: workers (| tuple None)) (: environ (| dict None)) (: revision str) (: start-ms int)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None))]
   :post [(: % SimPlan)] :tags {:context "doeff-cluster" :role "judgment"}}
  "sim-cluster の引数を検めて筋にするため(走らせる前に断る — environ の上書きの誤り・名の重なる worker)。"
  (<- fallback tuple (default-workers system))
  (val chosen (if (is workers None) fallback workers))
  (val names (lfor w chosen w.name))
  (when (or (not chosen) (!= (len names) (len (set names))) (not (all (gfor w chosen (isinstance w SimWorker)))))
    (raise (ValueError (.format "workers は名の重ならない SimWorker の 1 つ以上の tuple: {!r}" chosen))))
  (<- declaration Declaration (declaration-of system revision (or environ {})))
  (SimPlan :system system :declaration declaration :workers chosen :environ (or environ {}) :revision revision
           :per-process (if (is outside None) None outside.per-process)
           :start-ms start-ms :timing (or timing (ClusterTiming)) :naming (ClusterNaming) :policy (or policy (WorkerPolicy))
           :passable (+ SIM-PASSABLE (if (is outside None) #() outside.effects))))


(defk parts-of []
  {:pre [] :post [(: % SimParts)] :tags {:context "doeff-cluster" :role "foundation"}}
  "coordinator の Pod の部品を作るため(世界の handler が session で 1 回だけ呼ぶ)。"
  (SimParts :queue (RequestQueue) :store (MemoryWalStore) :stop (StopState) :kube (KubeMemory {})))


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
               :setv parts (lfor p (.split (.strip r.path "/") "/") (url-unquote p))
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
  "起こす process の宿の契約の run-context を作るため(本番の worker が子へ環境変数で渡す欄と同じ値 — 報告の世代が coordinator の
   report-matches と合う)。"
  (RunContext SIM-URL worker spec.revision spec.name
              :instance instance :attempt (str attempt) :spec-hash (spec-hash spec)
              :placement (if (is spec.placement None) "" (str spec.placement))))


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
          (.append link.queue.pending (Request method path query body :slot promise :actor link.actor :peer link.peer))
          (<- answer tuple (Wait promise.future))
          answer)))


(defk send-resent [link method path query body]
  {:pre [(: link SimLink) (: method str) (: path str) (: query dict) (: body (| dict None))]
   :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "何度送っても同じ意味の要求を、届かなければ本番の send-idempotent と同じ期限(IDEMPOTENT-DEADLINE-SECONDS)と間(RESEND-PAUSE-SECONDS)で
   送り直すため(仮想の時計で眠る)。答え = 最後の返事(期限を過ぎても届かなければ接続の失敗)。"
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
  {:pre [(: answer tuple) (: what str)] :post [(: % "返事の本文(形は口ごと)")] :tags {:context "doeff-cluster" :role "judgment"}}
  "返事 #(status 本文) の本文を返すため(300 以上・届かないなら理由つきの RemoteJobFailed — 本番の raise-for-status に当たる)。"
  (when (or (is (get answer 0) None) (>= (get answer 0) 300))
    (raise (RemoteJobFailed (.format "{}: coordinator の返事 {} {}" what (get answer 0) (get answer 1)))))
  (get answer 1))


(deff refused-or-body [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % "返事の本文(形は口ごと)")] :tags {:context "doeff-cluster" :role "judgment"}}
  "切り離した task と温める表の口の返事を読むため: 呼び手の誤り(400・409・413・429)は本番の client と同じ DetachedRefused
   (detached.detached-refusal)、それ以外は answered-body。"
  (let [refusal (detached-refusal (get answer 0) (if (isinstance (get answer 1) dict) (get answer 1) None))]
    (when refusal (raise refusal))
    (answered-body answer what)))


(deff unreached-reason [#^ tuple answer]  ; defk にできない: 答えの節が返事の理由を読む純粋な判断
  {:pre [(: answer tuple)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "届かなかった返事 #(None {\"error\" 理由}) の理由の 1 行を読むため。"
  (if (isinstance (get answer 1) dict) (str (.get (get answer 1) "error" "")) (str (get answer 1))))


(deff board-written [#^ tuple answer]  ; defk にできない: 答えの節が返事を Program への答えに変える純粋な判断
  {:pre [(: answer tuple)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "盤の compare-and-set の返事を WriteShared の答えにするため(409 = 合わなかった = 偽 — 本番の SharedClient.write と同じ読み)。"
  (if (= (get answer 0) 409) False (do (answered-body answer "盤に書けない") True)))


(defk remote-outcome [link program needs name]
  {:pre [(: link SimLink) (: program (| Program EffectBase)) (: needs frozenset) (: name str)]
   :post [(: % (| TaskSucceeded TaskFailed))] :tags {:context "doeff-cluster" :role "protocol"}}
  "RemoteJob を本番の remote-cluster と同じ手順で coordinator へ出し、結果を待つため: 詰めた Program を PUT /programs/<sha> で置き、
   POST /tasks(task-submit-body)で出し、問い合わせ(lease を延ばす)を終わるまで続け、抜ける時は task を落とす。送れない値は送る前に
   断る(encode-program の UnsendableProgram)。版は送り手の版(link.revision)。"
  (val blob (encode-program program))
  (val sha (program-sha blob))
  (<- put tuple (send-resent link "PUT" (+ "/programs/" sha) {} {"blob" blob "versions" (current-versions)}))
  (answered-body put "task の Program を置けない")
  (<- sent tuple (send-request link "POST" "/tasks" {} (task-submit-body sha link.revision needs name TASK-LEASE-SECONDS None)))
  (val id (get (answered-body sent "task を出せない") "task"))
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


(defk submit-detached [link program key needs name lease-seconds retain-seconds]
  {:pre [(: link SimLink) (: program (| Program EffectBase)) (: key str) (: needs frozenset) (: name str) (: lease-seconds float)
         (: retain-seconds float)]
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
                                      (detached-submit-body sha link.revision needs name lease-seconds retain-seconds declared)))
          (if (is (get sent 0) None)
              (submit-unreachable (unreached-reason sent))
              (DetachedSubmitted key (get (refused-or-body sent "task を出せない") "created"))))))


(defk await-detached [link key timeout-seconds]
  {:pre [(: link SimLink) (: key str) (: timeout-seconds (| float int None))] :post [(: % DetachedAwaited)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitDetached を本番の await-cluster と同じ手順で待つため: GET /detached/<key>(期限まで送り直す・503 = 起きた直後の warming は本文)を
   DETACHED-POLL-SECONDS ごとに読み、1 拍の読みは本番と同じ判断(detached.awaited-answer)で答えか待ち続けるかを決める。"
  (var waited 0.0)
  (var answer None)
  (while (is answer None)
    (<- read tuple (send-resent link "GET" (detached-path key "") {} None))
    (val view (cond (is (get read 0) None) None
                    (= (get read 0) 503) (get read 1)
                    True (refused-or-body read "task を読めない")))
    (:= answer (awaited-answer view (unreached-reason read) key waited timeout-seconds))
    (when (is answer None)
      (<- (Delay DETACHED-POLL-SECONDS))
      (:= waited (+ waited DETACHED-POLL-SECONDS))))
  answer)


(defk read-runners [link]
  {:pre [(: link SimLink)] :post [(: % (| tuple RunnersUnreachable))] :tags {:context "doeff-cluster" :role "protocol"}}
  "ReadRunners を本番の DetachedClient.runners と同じく GET /state の workers から読むため(届かなければ RunnersUnreachable)。"
  (<- read tuple (send-resent link "GET" "/state" {} None))
  (if (is (get read 0) None)
      (runners-unreachable (unreached-reason read))
      (runner-facts-of-view (get (answered-body read "名簿を読めない") "workers"))))


(defk warm-write [link env needs ttl-seconds holder]
  {:pre [(: link SimLink) (: env RuntimeEnv) (: needs frozenset) (: ttl-seconds float) (: holder str)] :post [(: % WarmState)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "WarmRuntimeEnv を本番の WarmClient.write と同じ本文(warm-request-body)で POST /warm に書き、今の姿を読むため。"
  (<- declared dict (runtime-env->json env))
  (<- written tuple (send-resent link "POST" "/warm" {} (warm-request-body declared needs ttl-seconds holder)))
  (warm-state-of-json (refused-or-body written "温める表に書けない")))


(defk warm-read [link key]
  {:pre [(: link SimLink) (: key str)] :post [(: % WarmState)] :tags {:context "doeff-cluster" :role "protocol"}}
  "ReadWarmState を本番の WarmClient.read と同じく GET /warm/<キー> で読むため(表に無い行 = 404 は空の姿)。"
  (<- read tuple (send-resent link "GET" (warm-path key) {} None))
  (if (= (get read 0) 404)
      (absent-warm-state key)
      (warm-state-of-json (answered-body read "温める表を読めない"))))


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
  (EffectBase []
    :when (not (isinstance effect passable))
    (raise (UnhandledEffect (.format "sim の柵: 答えの無い effect {} ({!r}) — 本番の子 process でも答える handler が無い"
                                     (. (type effect) __name__) effect)))))


(defrecord SimChild
  "sim の子 process 1 つに宿が答える物(宿の答え host-answers の引数)。ctx = 宿の契約の run-context・program-path = Program の path・
   environ = 宣言の :environ(上書きを重ねた物 — 名 → 値)・link = coordinator へ話す口(送り手 = job の名・居る所 = worker)・
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
  ;; 本番の宿と土台の HTTP の handler が process に答える物(宿の契約 HOST-CONTRACT の 3 つ・service の報告)に、同じ本文で答える。
  (Ask [key]
    :when (or (in key #(HOST-CONTRACT.run-context-key HOST-CONTRACT.program-key)) (in key child.environ))
    (resume (cond (= key HOST-CONTRACT.run-context-key) child.ctx
                  (= key HOST-CONTRACT.program-key) child.program-path
                  True (get child.environ key))))
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
  (RemoteJob [program needs name]
    (<- outcome (remote-outcome link program needs name))
    (resume (settled-value outcome)))
  (SubmitDetached [program key needs name lease-seconds retain-seconds]
    (<- submitted (submit-detached link program key needs name (float lease-seconds) (float retain-seconds)))
    (resume submitted))
  (AwaitDetached [key timeout-seconds]
    (<- awaited (await-detached link key timeout-seconds))
    (resume awaited))
  (CancelDetached [key]
    (<- answer tuple (send-resent link "POST" (detached-path key "/cancel") {} None))
    (resume (get (refused-or-body answer "取り消せない") "cancelled")))
  (ReleaseDetached [key]
    (<- answer tuple (send-resent link "DELETE" (detached-path key "") {} None))
    (resume (get (refused-or-body answer "保持を解けない") "released")))
  (ReadRunners []
    (<- runners (read-runners link))
    (resume runners))
  (WarmRuntimeEnv [env needs ttl-seconds holder]
    (<- warmed WarmState (warm-write link env needs (float ttl-seconds) holder))
    (resume warmed))
  (ReadWarmState [key]
    (<- warm WarmState (warm-read link key))
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
    (<- value (with-handlers [#* child.outside (fence child.pid child.passable) (coordinator-answers child.link) (host-answers child)] program))
    (SimExit :code 0 :result (if once (encode-outcome (TaskSucceeded value)) None) :value (if once None value))
    (except [TaskCancelledError]
      (<- killed (| SimExit None) (KillOf child.pid))
      (if (is killed None) (SimExit :code -15 :result None :detail "止めの合図") killed))
    (except [error Exception]
      (SimExit :code (if once 0 1) :result (if once (encode-outcome (failed-from error)) None)
               :detail (.format "{}: {}" (. (type error) __name__) error)))))


(defk refused-exit [refusal once]
  {:pre [(: refusal RemoteJobFailed) (: once bool)] :post [(: % SimExit)] :tags {:context "doeff-cluster" :role "judgment"}}
  "Program を解けなかった process の終わり方を決めるため(本番の job_entry と同じ: service は理由を出して 3・task は失敗の結果を書いて 0)。"
  (SimExit :code (if once 0 3)
           :result (if once (encode-outcome (failed-from refusal)) None)
           :detail (.format "{}: {}" (. (type refusal) __name__) refusal)))


(defk sim-process [worker spec child blob]
  {:pre [(: worker str) (: spec JobSpec) (: child SimChild) (: blob (| str None))]
   :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "sim の子 process 1 つの一生: 詰めた Program を解き(解けなければ本番の入口と同じく service は 3・task は失敗の結果)、柵の中で
   走らせ、終わりを世界へ書く(中で Spawn した task も一緒に止まる)。"
  (val decoded (if (is blob None)
                   #(None (RemoteJobFailed (.format "Program {} を coordinator の置き場から取れていない" spec.program)))
                   (decoded-program blob)))
  (<- ended SimExit (match decoded
                      #(None refusal) (refused-exit refusal spec.once)
                      #(program None) (run-fenced program child spec.once)))
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
          (<- (PutHostTruth worker.name
                            (replace truth :last-ok-ms now :last-desired (+ jobs tasks) :last-warm warm :beats (+ truth.beats 1)
                                     :fence-ms (if (and timing (in "fence_ms" timing)) (int (get timing "fence_ms")) truth.fence-ms)
                                     :programs (| truth.programs fetched)
                                     ;; 返事から外れた task の結果は落とす(本番の accept-tasks が結果の file を消すのと同じ)。
                                     :results (dfor #(k v) (.items truth.results) :if (in k ids) k v)
                                     :task-echo (dfor t (.get reply "tasks" []) :if (.get t "detached") (get t "id") (dict t)))))
          (DesiredJobs (+ jobs tasks) :warm warm))
      (do (<- truth HostTruth (live-truth worker.name boot))
          (if worker.ignores-fence
              (DesiredJobs truth.last-desired :warm truth.last-warm)
              (desired-when-unreachable (- now truth.last-ok-ms) truth.fence-ms truth.last-desired truth.last-warm
                                        (unreached-reason answer))))))


(defk release-leases [link worker instance]
  {:pre [(: link SimLink) (: worker str) (: instance str)] :post [(: % int)] :tags {:context "doeff-cluster" :role "protocol"}}
  "終わった process(世代の名 instance)が持っていた lease を返すため(本番の handlers.release-leases と同じ要求 — token の頭
   「<worker>/<世代の名>/」の担い手を POST /leases/<名> の drop で外す)。答え = 返した数。届かなければ期限で切れる。"
  (val prefix (.format "{}/{}/" worker instance))
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
    (<- desired (| DesiredJobs DesiredUnreadable) (heartbeat worker boot))
    (resume desired))
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
    (val link (SimLink :queue parts.queue :actor spec.name :revision spec.revision :peer worker.name))
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
    (resume None))
  (ReleaseLeases [instance]
    (<- (live-truth worker.name boot))
    (<- parts SimParts (PartsOf))
    (<- plan SimPlan (PlanOf))
    (<- (release-leases (SimLink :queue parts.queue :actor worker.name :revision plan.revision :peer worker.name) worker.name instance))
    (resume None))
  (PublishStatus [statuses note]
    (<- truth HostTruth (live-truth worker.name boot))
    (<- (PutHostTruth worker.name (replace truth :statuses (status-report statuses truth.task-echo truth.results))))
    (resume None))
  (WorkerStopRequested []
    (<- truth HostTruth (live-truth worker.name boot))
    (<- stopping bool (WorkersStopping))
    (resume (or stopping truth.stopping))))


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
            (<- (generation-end loop))
            (<- (WorkerEnded worker.name truth.boot))
            (<- again bool (WorkersStopping))
            (:= going (not again)))))
  worker.name)


;; --- coordinator の Pod ---------------------------------------------------------------------------------

(defhandler observe-requests
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 調停ループの一番内側: 取った要求のうち網の切れた worker から届いた物を落とし(送り手には接続の失敗 — 本番では届かない)、service の
  ;; 報告(ReportReady・ReportMetrics)を世界へ記録し、返事の前に落ちた時に接続の失敗を返す相手として取った要求を覚える。筋書きの
  ;; 止まり(止めの合図)と落ち(Persist の失敗 — 返事をせずに落ちる)を注入する。効果はそのまま外側(本物の組)へ出し直す。
  (NextRequests [timeout-seconds limit]
    (<- batch list effect)
    (<- cut frozenset (CutPeers))
    (val kept (lfor r batch :if (not-in r.peer cut) r))
    (<- now int (now-epoch-ms))
    (<- reports tuple (reports-in kept now))
    (when reports
      (<- (NoteReports reports)))
    (<- (HoldRequests (tuple kept)))
    (for [r batch]
      (when (in r.peer cut)
        (<- (CompletePromise r.slot #(None {"error" CUT-REASON})))))
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
                                        (let [body (answered-body current (+ "Service " (get row "name")))]
                                          {"resourceVersion" (get body "resourceVersion") "spec" (spec-for-update row (get body "spec") None)}))))
    (answered-body written (+ "Service " (get row "name"))))
  (tuple (gfor row declaration.rows (get row "name"))))


;; --- 世界 -----------------------------------------------------------------------------------------------

(defhandler sim-world [#^ SimPlan plan]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 筋(系・worker・environ の上書き)は検ごとに違う値で、世界の外側に Ask へ答える物を置くと検の筋書きからも読めて
  ;; しまう(世界の答えは sim の中の仕組みだけが読む)。
  ;; sim の世界 1 つ(coordinator の部品と一生・worker の宿の真実と世代・process の把手と記録・process の中の task・届いた報告・準備・
  ;; 網の切断・止まれの合図)を session に持ち、仕組みの effect と検の effect に答える。session の値の置き場(doeff_core_effects の
  ;; state)はこの handler の外側に要る(sim-cluster が置く)。真実(本当に動いている process・届いた報告)はここが持つ — coordinator の
  ;; 信念(状態の中の報告)ではない。節の session の書きは scheduler の切り替わる effect より前に済ませる(頭の註)。
  (session val parts !(parts-of))
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
  (session var held #())
  (session var pauses #())
  (session var downtime None)
  (session var runs #())
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
    ;; process が終われば、中で Spawn した task も止まる(本番は子 process ごと消える)。
    (for [task spawned]
      (<- (Cancel task)))
    (resume None))
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
    ;; 把手がまだ無い process(StartJob の Spawn と KeepHandle の間)は KeepHandle がその場で取り消す。
    (for [pid victims]
      (when (in pid handles)
        (<- (Cancel (get handles pid)))))
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
    (resume None))
  (CrashCoordinator [seconds]
    (:= pauses (+ pauses #(#(PAUSE-CRASH (float seconds)))))
    (resume None))
  (CoordinatorRuns []
    (resume runs))
  (ClientLink []
    (resume (SimLink :queue parts.queue :actor CLIENT-NAME :revision plan.revision :peer CLIENT-NAME)))
  (Redeclare [system]
    (<- declaration Declaration (declaration-of system plan.revision plan.environ))
    (<- link SimLink (control-link parts.queue plan.revision))
    (<- names tuple (apply-declaration link declaration))
    (resume names))
  (ReportsOf [name]
    (resume (tuple (gfor r reports :if (= r.job name) r))))
  (ProcessesOf [name]
    (resume (tuple (gfor r log :if (= r.job name) r))))
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
      (<- (Wait pod)))))


(defk sim-cluster [system scenario * [workers None] [environ None] [revision "sim"] [start-ms SIM-START-MS] [timing None] [policy None]
                  [outside None]]
  {:pre [(: system System) (: scenario (| Program EffectBase)) (: workers (| tuple None)) (: environ (| dict None)) (: revision str) (: start-ms int)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None))]
   :post [(: % "scenario の答え(型は筋書きごと)")]
   :tags {:context "doeff-cluster" :role "entry"}}
  "系 system(sim の土台で作った System の値)を本物の coordinator と worker の上で走らせ、scenario(検の筋書きの Program — 同じ
   scheduler・同じ仮想の時計で並んで走る)の答えを返す。workers = SimWorker の tuple(既定 = 全 job の needs の和を提供する 1 台)・
   environ = job 名 → 宣言の :environ に重ねる環境変数(宣言に無い名は断る)・revision = 宣言の版・start-ms = 仮想の時計の起点・
   timing / policy = coordinator と worker の時間の設定(既定 = 本番の既定)・outside = sim の外の世界(SimOutside — 業務の外の系の模擬の
   handler と、それが答える effect の型。仮想の時計の内側・sim の世界の外側に置き、柵はその型も通す)。自分で scheduler を持つ(外に
   scheduler が在っても無くても走る)。"
  (<- plan SimPlan (sim-plan system workers environ revision start-ms timing policy outside))
  (<- answer (scheduled (with-handlers [(sim-time-handler :start-time (datetime-of-epoch-ms start-ms)) (session-store)
                                        #* (if (is outside None) [] outside.handlers) (sim-world plan)]
                          (sim-main scenario))))
  answer)
