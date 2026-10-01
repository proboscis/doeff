;;; coordinator に話す effect の族の契約テストの解釈器(composition root)— 同じ契約の Program を、handler だけ替えて走らせる。
;;;
;;;   shared-fake            fake: 本物と同じ shared-http を、HTTP の層の fake の盤(board_fake.hy — 同じ process の dict に coordinator と
;;;                          同じ純粋な判断で答える)の上で
;;;   shared-http            本物: shared-http(宛先の部品の HttpRequest → coordinator の /board・/leases)
;;;   metrics-memory         fake: metrics-memory(list に記録)
;;;   metrics-http           本物: metrics-http(宛先の部品の HttpRequest → POST /resources/Service/<名>/metrics)
;;;   readiness-memory       fake: readiness-memory(list に記録)
;;;   readiness-http         本物: readiness-http(宛先の部品の HttpRequest → POST /resources/Service/<名>/readiness)
;;;   remote-cluster         本物: remote-cluster(TaskClient → coordinator の /programs・/tasks)と担い手(RigWorker)
;;;   remote-cluster-env     同じ・送り手が実行環境を宣言する(TaskClient の runtime-env = CONTRACT-ENV)
;;;   warm-cluster           本物: warm-cluster(WarmClient → coordinator の /warm)
;;;   sim-cluster            fake: 手元の runner sim-cluster(筋書きの送り手の口 coordinator-answers が RemoteJob・WarmRuntimeEnv・
;;;                          ReadWarmState に答える — 本物の coordinator の調停ループと本物の run-worker・偽の宿)
;;;   sim-cluster-env        同じ・送り手の口が実行環境を宣言する(SimLink の runtime-env = CONTRACT-ENV)
;;;   scheduled              semaphore: scheduled だけ(手元の Semaphore — 名前を見る handler が無い)
;;;   named-semaphore-local  semaphore: 1 つの VM の名前の表
;;;   cluster-semaphore      semaphore: cluster-semaphore(LeaseOp)を fake の盤(board_fake.hy)の上で
;;;   cluster-semaphore-http semaphore: cluster-semaphore を shared-http(本物の coordinator の /leases)の上で
;;;
;;; 本物の側の相手は実の coordinator の process ではなく、test_detached.hy の coordinator の組と同じ MemoryCoordinator(本物の
;;; api_policy.respond / tick を httpx の MockTransport の後ろに置く)。時刻は仮想の時計(SimClock)で、MemoryCoordinator は同じ時計を
;;; 直に読む(契約の Program の Delay が両方の時刻を進める)。RemoteJob の担い手は detached_rig.RigWorker(本物の CoordinatorLink で
;;; heartbeat を送り、割り当てられた task を同じ VM で走らせる — 子 process の入口そのものは test_remote.hy が通す)。
;;;
;;; 契約の Program が coordinator の側の真実を読む口は検の effect だけ(読む手段だけを解釈器ごとに替える):
;;;   BoardSeen          → 盤の行 {鍵: 値}(fake = dict・本物 = MemoryCoordinator の状態の盤)
;;;   ReportSeen kind    → 最後に残った報告(metrics = 計器の dict・readiness = {ready reason role})か None
;;;   TasksSeen          → coordinator の task の行(TaskSeen の tuple — id の順)
;;;   WarmsSeen          → coordinator の温める表の行(WarmSeen の tuple — key の順)
;;;   SetReachable up    → coordinator へ届くか(本物 = transport が ConnectError を上げる・sim-cluster = coordinator の Pod を止める
;;;                        (切るだけ — 戻さない)・memory の fake は網を持たないので何もしない)
;;; TasksSeen と WarmsSeen は、本物 = MemoryCoordinator の状態・sim-cluster = 模擬の coordinator が永続化した置き場から読んだ状態
;;; (durable_kv.state-from-kv — 本物の coordinator が起き直す時と同じ読み)を、同じ関数(tasks-seen-of・warms-seen-of)で行にする。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk deff defhandler <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import functools [partial])
(import pathlib [Path])
(import shutil)
(import tempfile)
(import httpx)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects.scheduler [Spawn Cancel Task])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.core.durable_kv [state-from-kv])
(import doeff_cluster.shared_handlers [shared-http])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed HttpFailureKind])
(import doeff_cluster.shared.protocol.metrics_handlers [metrics-memory metrics-http])
(import doeff_cluster.shared.protocol.readiness_handlers [readiness-memory readiness-http])
(import doeff_cluster.shared.protocol.service_report [ServiceReport])
(import doeff_core_effects.handlers [slog-handler])
(import doeff_cluster.shared.protocol.remote [remote-cluster TaskClient])
(import doeff_cluster.foundation.process_versions [current-versions])
(import doeff_cluster.shared.protocol.detached [warm-cluster WarmClient])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimLink ClientLink PartsOf SimParts StopCoordinator coordinator-answers])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.semaphore_handlers [named-semaphore-local cluster-semaphore SemaphoreSession])
(import doeff_cluster.shared.intent.service_model [system-of])
(import doeff_time [Delay])
(import tests.detached_rig [MemoryCoordinator RigWorker worker-tick worker-loop RIG-PROVIDES])
(import tests.env_fixtures [LOCK env-of])
(import tests.program_rows [SAMPLE-RUN])
(import tests.board_fake [board-handlers])

(val COORDINATOR "http://coordinator")
;; 報告の送り手が名乗る Service(本物の側では coordinator に宣言してある — 無い Service の報告は coordinator が 404 で断る)。
(val SERVICE "contract-service")
(val SERVICE-SPEC {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN})
(val REFUSED "[Errno 111] Connection refused")
(val METRICS "metrics")
(val READINESS "readiness")
;; task の送り手の版(本物の TaskClient の revision — sim の送り手は sim の宣言の版)。
(val SENDER-REVISION "r1")
;; 担い手の問い合わせの間隔(仮想の秒)と、本物の温める頼みの送り直しの期限(実時間の秒 — 届かない時の答えを待たせない)。
(val WORKER-POLL-SECONDS 0.5)
(val WARM-DEADLINE-SECONDS 0.2)
;; sim-cluster の担い手: 本物の側の RigWorker と同じ名と能力(同じ needs の task が両方で置かれる)。
(val SIM-WORKERS #((SimWorker :name "w1" :provides (frozenset RIG-PROVIDES))))
(val NO-JOBS (system-of "contract-scenarios" #()))
;; sim-cluster で coordinator を切る時に止めておく秒(契約の Program の残りより十分長い — 切ったら戻さない)。
(val SIM-DOWN-SECONDS 3600.0)
;; cluster-semaphore の担い手の名・lease の期限・空き待ちの間隔。
(val SEMAPHORE-HOLDER "worker-a")
(val SEMAPHORE-TTL-SECONDS 15.0)
(val SEMAPHORE-POLL-SECONDS 0.5)


(defclass [(dataclass :frozen True)] BoardSeen [EffectBase]
  "coordinator の側の盤の行 {鍵: 値}(検の effect — 契約の Program が真実を読む口)。")

(defclass [(dataclass :frozen True)] ReportSeen [EffectBase]
  "coordinator の側に最後に残った kind(metrics | readiness)の報告(検の effect)。"
  (#^ str kind))

(defclass [(dataclass :frozen True)] SetReachable [EffectBase]
  "coordinator へ届くかを切り替える(検の effect)。"
  (#^ bool up))

(defclass [(dataclass :frozen True)] TasksSeen [EffectBase]
  "coordinator の側の task の行(TaskSeen の tuple — id の順。検の effect)。")

(defclass [(dataclass :frozen True)] WarmsSeen [EffectBase]
  "coordinator の側の温める表の行(WarmSeen の tuple — key の順。検の effect)。")


(defrecord TaskSeen
  "coordinator が持つ task の行のうち、送り手が決める欄: name・needs(名の順)・environ(名の順の #(名 値))・runtime-env(宣言の JSON か
   None)・detached(切り離した task か)。"
  (#^ str name)
  (#^ tuple needs)
  (#^ tuple environ)
  (#^ (| dict None) runtime-env)
  (#^ bool detached))


(defrecord WarmSeen
  "coordinator が持つ温める表の行: key・needs(名の順)・holder・runtime-env(宣言の JSON)・until-ms(coordinator の時計の期限)。"
  (#^ str key)
  (#^ tuple needs)
  (#^ str holder)
  (#^ dict runtime-env)
  (#^ int until-ms))


(defk tasks-seen-of [state]
  {:pre [(: state ClusterState)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の状態の task を、契約が比べる行にするため(本物と sim-cluster が同じ関数を通る)。"
  (tuple (gfor task (sorted (.values state.tasks) :key (fn [t] t.id))
               (TaskSeen :name task.name :needs (tuple task.needs) :environ (tuple task.environ)
                         :runtime-env task.runtime-env :detached task.detached))))


(defk warms-seen-of [state]
  {:pre [(: state ClusterState)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の状態の温める表を、契約が比べる行にするため(本物と sim-cluster が同じ関数を通る)。"
  (tuple (gfor #(key entry) (sorted (.items state.warms))
               (WarmSeen :key key :needs (tuple entry.needs) :holder entry.holder :runtime-env entry.runtime-env
                         :until-ms entry.until-ms))))


(defk contract-env []
  {:pre [] :post [(: % RuntimeEnv)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "実行環境を宣言する送り手(remote-cluster-env・sim-cluster-env)の宣言と、契約の Program が温める env(2 つの repo を並べる宣言)。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  env)


(defhandler memory-side [#^ dict store #^ list reports]
  ;; 引数に残す理由: 真実は fake の handler と同じ dict / list そのもの(組み立てが 1 つ作って両方へ渡す — Ask で運ぶ設定ではない)。
  ;; fake の側の真実: 共有の保存の dict と、報告の handler が積む list(1 つの解釈器の報告の族は 1 つ)。fake は網を持たない。
  (BoardSeen [] (resume (dict store)))
  (ReportSeen [kind] (resume (if reports (get reports -1) None)))
  (SetReachable [up] (resume None)))


(deff latest-report [#^ MemoryCoordinator coordinator #^ str kind]  ; defk にできない: handler の節が Program の外の状態(MemoryCoordinator)から組む純粋な読み
  {:pre [(: coordinator MemoryCoordinator) (: kind str) (in kind #(METRICS READINESS))] :post [(: % (| dict None))]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の状態に最後に残った kind の報告を、effect の側の形(metrics = 計器の dict・readiness = {ready reason role})にする。"
  (let [reports (.get (getattr coordinator.state kind) SERVICE #())]
    (cond
      (not reports) None
      (= kind METRICS) (get reports -1 "metrics")
      True (dfor field #("ready" "reason" "role") field (get reports -1 field)))))


(defhandler coordinator-side [#^ MemoryCoordinator coordinator #^ dict line]
  ;; 引数に残す理由: 真実は transport の後ろの MemoryCoordinator と線そのもの(組み立てが 1 つ作って transport と共有する)。
  ;; 本物の側の真実: MemoryCoordinator の状態。line = {"up": bool}(transport が読む)。
  (BoardSeen [] (resume (dict coordinator.state.board)))
  (ReportSeen [kind] (resume (latest-report coordinator kind)))
  (TasksSeen []
    (<- rows tuple (tasks-seen-of coordinator.state))
    (resume rows))
  (WarmsSeen []
    (<- rows tuple (warms-seen-of coordinator.state))
    (resume rows))
  (SetReachable [up] (.update line {"up" up}) (resume None)))


(defk sim-state []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "sim-cluster の模擬の coordinator が永続化した置き場から、今の状態を読むため(本物の coordinator が起き直す時と同じ読み — 置き場を
   書き換えない)。"
  (<- parts SimParts (PartsOf))
  (<- now int (now-epoch-ms))
  (state-from-kv (.load parts.store) now))


(defhandler sim-side []
  ;; sim-cluster の側の真実: 模擬の coordinator の置き場。届かなくするのは coordinator の Pod を止めること(戻さない)。
  (TasksSeen []
    (<- state ClusterState (sim-state))
    (<- rows tuple (tasks-seen-of state))
    (resume rows))
  (WarmsSeen []
    (<- state ClusterState (sim-state))
    (<- rows tuple (warms-seen-of state))
    (resume rows))
  (SetReachable [up]
    (when up
      (raise (ValueError "sim-cluster の契約の組は止めた coordinator を戻さない(SetReachable True は使えない)")))
    (<- (StopCoordinator SIM-DOWN-SECONDS))
    ;; 止めの合図は coordinator の次の拍で効く(test_env_warm の筋書きと同じく 1 仮想秒待つ)。
    (<- (Delay 1.0))
    (resume None)))


(deff line-answer [#^ MemoryCoordinator coordinator #^ dict line request]  ; defk にできない: httpx の MockTransport が要求ごとに同期で呼ぶ callback
  {:pre [(: coordinator MemoryCoordinator) (: line dict) (: request httpx.Request)] :post [(: % httpx.Response)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator への線: 届く間は MemoryCoordinator が答え、切れている間は接続できない(httpx.ConnectError)。"
  (if (get line "up")
      (.handle coordinator request)
      (raise (httpx.ConnectError REFUSED :request request))))


(defhandler coordinator-over-http [#^ MemoryCoordinator coordinator #^ dict line]
  ;; 引数に残す理由: 真実は MemoryCoordinator と線そのもの(組み立てが 1 つ作って coordinator-side と共有する)。
  ;; coordinator へ送る要求(shared-http が宛先の部品から出す HttpRequest)に、線(line-answer)の答えで同期に答える: 届く間は
  ;; MemoryCoordinator の返事・切れている間は接続できない失敗(HttpFailed の CONNECT-FAILED — 本物の答え手が ConnectError を写す形)。
  (HttpRequest [method url headers params body]
    (val request (httpx.Request method url :params params :json body :headers headers))
    (val answer (try (line-answer coordinator line request)
                     (except [refused httpx.ConnectError]
                       (HttpFailed :url url :detail (+ "ConnectError: " (str refused)) :kind HttpFailureKind.CONNECT-FAILED))))
    (resume (if (isinstance answer HttpFailed)
                answer
                (HttpResponse answer.status-code (dict answer.headers) answer.content answer.text url 0.0)))))


(val CONTRACT-ROUTE (RouteOptions :reply-seconds 15.0 :connect-seconds 2.0 :connect-retries 4 :recheck-ms 60000 :actor "c-contract"))


(deff declared-coordinator [#^ SimClock clock]  ; defk にできない: 組み立て(Program を走らせる前)が呼ぶ Program の外の準備
  {:pre [(: clock SimClock)] :post [(: % MemoryCoordinator)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "Service SERVICE を宣言した MemoryCoordinator(時計 = clock)。宣言は運用者と同じ口(POST /resources/Service)で送る。"
  (let [coordinator (MemoryCoordinator clock)
        response (.handle coordinator (httpx.Request "POST" (+ COORDINATOR "/resources/Service")
                                                     :json {"name" SERVICE "spec" SERVICE-SPEC}
                                                     :headers {"x-actor" "c-contract"}))]
    (assert (= response.status-code 201) response.text)
    coordinator))


(defk under-memory [make-handlers program]
  {:pre [(: make-handlers Callable) (: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "fake の下で program を走らせる。make-handlers = (fn [store reports] fake の handler の list — 外側が先)。外から順に: 仮想の時計・
   真実の口・fake。"
  (val store {})
  (val reports [])
  (<- answer (with_handlers [(sim-time-handler :clock (SimClock)) (memory-side store reports) #* (make-handlers store reports)] program))
  answer)


(defk under-coordinator [make-handlers program]
  {:pre [(: make-handlers Callable) (: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の http の handler の下で program を走らせる。make-handlers = (fn [transport] 本物の handler の list — 外側が先)。相手は Service を
   宣言した MemoryCoordinator(仮想の時計を契約の Program と共有する)。"
  (val clock (SimClock))
  (val coordinator (declared-coordinator clock))
  (val line {"up" True})
  (val transport (httpx.MockTransport (partial line-answer coordinator line)))
  (<- answer (with_handlers [(sim-time-handler :clock clock) (coordinator-side coordinator line) (coordinator-over-http coordinator line)
                             #* (make-handlers transport)] program))
  answer)


(defk beside-worker [worker program]
  {:pre [(: worker RigWorker) (: program Program)] :post [(: % "program の答え")] :tags {:context "doeff-cluster-test" :role "program"}}
  "担い手を 1 拍名乗らせてから(送った時に置ける worker が在るように)heartbeat のループを並べて program を走らせ、終われば担い手を
   止めるため(test_detached.hy の with-worker と同じ順)。"
  (<- (worker-tick worker))
  (<- loop Task (Spawn (worker-loop worker WORKER-POLL-SECONDS) :daemon True))
  (try
    (<- answer program)
    answer
    (finally
      (setattr worker "dead" True)
      (<- (Cancel loop)))))


(defk under-rig [with-env program]
  {:pre [(: with-env bool) (: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の remote-cluster(TaskClient — with-env なら実行環境を宣言する送り手)の下で、MemoryCoordinator と担い手 RigWorker(名 w1・能力
   local — sim-cluster の担い手と同じ)を並べて program を走らせる。担い手の task の file の置き場は一時の dir(終われば消す)。"
  (var env None)
  (when with-env
    (<- declared RuntimeEnv (contract-env))
    (:= env declared))
  (val clock (SimClock))
  (val coordinator (declared-coordinator clock))
  (val line {"up" True})
  (val transport (httpx.MockTransport (partial line-answer coordinator line)))
  (val task-dir (Path (tempfile.mkdtemp :prefix "remote-contract-")))
  (val worker (RigWorker COORDINATOR task-dir (current-versions) :transport transport))
  (val client (TaskClient COORDINATOR SENDER-REVISION (current-versions) :runtime-env env :transport transport))
  (try
    (<- answer (with_handlers [(sim-time-handler :clock clock) (coordinator-side coordinator line) (remote-cluster client)]
                 (beside-worker worker program)))
    answer
    (finally
      (shutil.rmtree task-dir :ignore-errors True))))


(defk sim-sender [with-env program]
  {:pre [(: with-env bool) (: program Program)] :post [(: % "program の答え")] :tags {:context "doeff-cluster-test" :role "program"}}
  "sim-cluster の筋書きとして program を走らせるため: 真実の口 sim-side の下で、with-env なら送り手の口を実行環境を宣言する口
   (本番の TaskClient の runtime-env と同じ — SimLink の runtime-env)に替える。"
  (<- link SimLink (ClientLink))
  (var sender link)
  (when with-env
    (<- declared RuntimeEnv (contract-env))
    (:= sender (replace link :runtime-env declared)))
  (<- answer (with_handlers [(sim-side) (coordinator-answers sender)] program))
  answer)


(defk under-sim [with-env program]
  {:pre [(: with-env bool) (: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "手元の runner sim-cluster(job を持たない系・担い手 w1)の筋書きとして program を走らせる。"
  (<- answer (sim-cluster NO-JOBS (sim-sender with-env program) :workers SIM-WORKERS))
  answer)


(defk under-clock [make-handlers program]
  {:pre [(: make-handlers Callable) (: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "仮想の時計だけを共にして program を走らせる(semaphore の組 — 保存を持たない組と、fake の保存を持つ組)。make-handlers =
   (fn [] handler の list — 外側が先)。"
  (<- answer (with_handlers [(sim-time-handler :clock (SimClock)) #* (make-handlers)] program))
  answer)


(deff contract-route []  ; defk にできない: 組み立ての表(INTERPRETERS)が本物の handler を作る時に呼ぶ Program の外の準備
  {:pre [] :post [(: % RouteCell)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の handler が要求から要求へ持ち越す宛先の状態の入れ物(宛先 = MemoryCoordinator の 1 つ — 解釈器を開くたびに新しく作る)。"
  (RouteCell (CoordinatorRoute :urls #(COORDINATOR) :active 0 :switched-at-ms 0)))


(deff contract-report []  ; defk にできない: 組み立ての表(INTERPRETERS)が本物の handler を作る時に呼ぶ Program の外の準備
  {:pre [] :post [(: % ServiceReport)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "SERVICE の報告の送り手(送り手の worker と版は固定)。"
  (ServiceReport SERVICE {"worker" "w1" "pid" 1 "revision" "r1"}))


(deff semaphore-session []  ; defk にできない: 組み立ての表(INTERPRETERS)が handler を作る時に呼ぶ Program の外の準備
  {:pre [] :post [(: % SemaphoreSession)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "cluster-semaphore の担い手 1 つの手元の記憶(解釈器を開くたびに新しく作る)。"
  (SemaphoreSession SEMAPHORE-HOLDER :ttl-seconds SEMAPHORE-TTL-SECONDS :poll-seconds SEMAPHORE-POLL-SECONDS))


(val INTERPRETERS
  {"shared-fake" (partial under-memory (fn [store reports] (board-handlers store)))
   "shared-http" (partial under-coordinator (fn [transport] [(shared-http (contract-route) CONTRACT-ROUTE)]))
   "metrics-memory" (partial under-memory (fn [store reports] [(metrics-memory reports)]))
   "metrics-http" (partial under-coordinator (fn [transport] [slog-handler (metrics-http (contract-route) CONTRACT-ROUTE (contract-report))]))
   "readiness-memory" (partial under-memory (fn [store reports] [(readiness-memory reports)]))
   "readiness-http" (partial under-coordinator (fn [transport] [slog-handler (readiness-http (contract-route) CONTRACT-ROUTE (contract-report))]))
   "remote-cluster" (partial under-rig False)
   "remote-cluster-env" (partial under-rig True)
   "warm-cluster" (partial under-coordinator
                           (fn [transport] [(warm-cluster (WarmClient COORDINATOR :transport transport
                                                                      :deadline-seconds WARM-DEADLINE-SECONDS))]))
   "sim-cluster" (partial under-sim False)
   "sim-cluster-env" (partial under-sim True)
   "scheduled" (partial under-clock (fn [] []))
   "named-semaphore-local" (partial under-clock (fn [] [(named-semaphore-local {})]))
   "cluster-semaphore" (partial under-clock (fn [] [#* (board-handlers {}) (cluster-semaphore (semaphore-session))]))
   "cluster-semaphore-http" (partial under-coordinator
                                     (fn [transport] [(shared-http (contract-route) CONTRACT-ROUTE)
                                                      (cluster-semaphore (semaphore-session))]))})
