;;; 手元の runner sim-cluster — 系(defsystem の関数を sim の土台で呼んだ System の値)を、本物の coordinator と本物の worker の上で、
;;; 1 process・仮想の時計で走らせる(ADR-DOE-CLUSTER-001・計画 2.6・10.2・段 5)。
;;;
;;;   (<- answer (sim-cluster (lab sim-foundation) (scenario) :workers #((SimWorker :name "w1" :provides #{"net"})) :environ {"tally" {"STEP" "3"}}))
;;;
;;; 中身(写しを作らない — 起こし直しの間隔・readiness の窓・handoff の期限・needs ⊆ provides の置き方は本物が決める):
;;;   - coordinator の Pod = 本物の run-coordinator を coordinator_handler_sets.emulated-handlers(要求の列 RequestQueue・memory の
;;;     置き場・偽の k8s)の上で回す。一番内側の見張り(observe-requests)が、届いた ReportReady / ReportMetrics を世界へ記録する。
;;;   - worker = 本物の run-worker を、worker ごとの偽の宿(sim-host)の上で回す。heartbeat・状態の報告・lease を返す要求は本番の
;;;     CoordinatorLink と同じ形(handlers.heartbeat-body・status-report・desired-when-unreachable)で要求の列へ送る。コードと実行環境の
;;;     準備・入口の検めは即座に揃う・通ると答える(準備の層は env_fake の検が持つ)。
;;;   - 偽の宿の StartJob は、coordinator の /programs/<sha> から取った詰めた文字列を decode し(検の物と object を共有しない・運べる値か
;;;     を検める)、「柵(fence)→ 宿の答え(host-answers)→ Program」の順に包んで、宿の handler の節の中から Spawn する。Spawn は節の
;;;     外側の handler(世界・時計)だけを持ち運ぶので、run-worker の中の handler も、他の job の handler も混ざらない(service ごとの別の
;;;     スコープ)。終わり(値・例外・取り消し)は世界へ書き、ObserveWorld が exit-code として返す。
;;;   - 宿の答え(host-answers — process ごと)= host_contract.HOST-CONTRACT の 3 つ(run-context・Program の path・宣言の environ の名の
;;;     Ask)と、本番では土台の HTTP の handler が coordinator へ送るクラスタの約束の effect(ReportReady・ReportMetrics・ReadShared /
;;;     WriteShared・LeaseOp・RemoteJob)。要求の形は本番の送り手と同じ関数(report_client.report-request・shared_handlers.board-*-request /
;;;     lease-request・remote.task-submit-body / outcome-of / settled-value)。宿の答えは柵の内側に在るので、世界の effect を出さず、
;;;     要求の列を値で受けて scheduler の effect だけで coordinator と話す。
;;;   - 柵(fence)= host_contract.SIM-PASSABLE(scheduler と doeff-time の時計の effect)だけを外へ通し、それ以外を本番の子と同じ
;;;     doeff.UnhandledEffect で Program へ投げ返す — sim の外側(検の handler・sim の世界)が本番には無い答えを黙って返さない。
;;;
;;; 検の effect(sim の世界が答える — scenario の中で出す。service の Program が出すと柵で落ちる):
;;;   Crash 名        動いている process を exit 1 で落とす(答え = 落とした数)。worker が本物の判断で起こし直す。
;;;   Redeclare 系    宣言し直す(本番の declare と同じ順で Program を置いてから Service の行を書く — update に従い recreate / handoff)。
;;;   ReportsOf 名    coordinator に届いた ReportReady / ReportMetrics の列(SimReport)。
;;;   ReadinessOf 名  coordinator の Service の status の ready(SimReadiness — Ready / NotReady / Unknown / Missing)。
;;;   ProcessesOf 名  その job の process の列(SimProcess — 世代・worker・始まり・終わり・exit-code)。
;;;   SharedRows 頭   coordinator の盤の行(鍵が頭で始まる物)。
;;; 時間を進めるのは scenario の Delay(doeff-time)。scenario が終われば worker を止め(全 job を止めの手順で回収)、coordinator を止める。
;;;
;;; 本番との既知の差(検めない):
;;;   - 1 process なので、import した module の大域の状態は job の間で共有されうる(改訂 1 の Q)。effect 以外の共有は機械で全部は断れない。
;;;   - environ は宿が宣言の :environ の名の Ask にだけ答える(本番の os.environ を読む handler は PATH などの宣言の外の名にも答える)。
;;;   - process の中で Spawn した task は、process を取り消しても一緒には止まらない(本番は process ごと消える)。
;;;   - 土台の関数の :needs が中の handler の :needs を漏らしていても見つからない(計画 9 の P — doeff-linter の照合は別便)。
;;;   - sim の土台は scheduler と時計を含まないので、本番の土台に scheduler を入れ忘れてもここでは見つからない(計画 7)。
;;;   - SubmitDetached・WarmRuntimeEnv には答えない(柵で落ちる)— 段 5b で足す。
;;;
;;; 状態の置き場(ADR-DOE-HY-007): 世界の状態は世界の handler(sim-world)の session var に置き、値は defrecord、変化は effect で書く。
;;; 要求の列・memory の置き場・停止の合図・偽の k8s は coordinator_handler_sets の既存の資源(世界の session val が 1 回だけ作る)。
(require doeff-hy.macros [defk deff defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
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
(import .cluster_model [ClusterState ClusterTiming ClusterNaming Request NextRequests])
(import .cluster_policy [fresh-task-prefix])
(import .coordinator [run-coordinator load-state])
(import .coordinator_inbox [StopState])
(import .coordinator_handler_sets [RequestQueue MemoryWalStore emulated-handlers])
(import .kube_handlers [KubeMemory])
(import .declare [create-body spec-for-update])
(import .handlers [declared-job-spec task-spec heartbeat-body status-report desired-when-unreachable])
(import .host_contract [HOST-CONTRACT SIM-PASSABLE])
(import .job_context [RunContext])
(import .job_entry [decoded-program])
(import .metrics_model [ReportMetrics])
(import .readiness_model [ReportReady])
(import .remote [task-submit-body outcome-of settled-value])
(import .remote_model [RemoteJob RemoteJobFailed TaskSucceeded TaskFailed encode-program encode-outcome failed-from program-sha
                       current-versions])
(import .report_client [report-request])
(import .semaphore_model [LeaseOp SEMAPHORE-PREFIX drop-holders])
(import .service_model [System Declaration system-declaration])
(import .shared_handlers [board-read-request board-write-request lease-request])
(import .shared_model [ReadShared WriteShared])
(import .worker [run-worker])
(import .worker_model [JobSpec WorkerPolicy WorkerState WorldView CodeView CodeState ProcessView ProbeView ProbeState
                       DesiredJobs DesiredUnreadable ReadDesired ObserveWorld WorkerStopRequested PublishStatus
                       PrepareCode PrepareEnv SweepEnvs StartJob SignalJob ReapJob RetireJob ProbeEntry ForgetProbes
                       ReleaseLeases spec-hash])

;; load-state は置き場がまだ無い時だけ以前の形の file を探す。sim は置き場(MemoryWalStore)が在る時だけ load-state を呼ぶので読まれない。
(val NO-STATE-FILE "/nonexistent/doeff-sim/coordinator/state.json")
(val SIM-URL "sim://coordinator")            ; 宿の契約の run-context の coordinator の URL(sim の宿は URL で話さない — 表示だけ)
(val SIM-START-MS 1767225600000)             ; 仮想の時計の起点(2026-01-01T00:00:00Z)
(val DECLARE-ACTOR "sim-declare")            ; 宣言の送り手(資源の書きの X-Actor)
(val TASK-POLL-SECONDS 1.0)                  ; RemoteJob の問い合わせの間隔(本番の remote-cluster の既定と同じ)
(val TASK-LEASE-SECONDS 15.0)                ; RemoteJob の lease(同じ)
(val QUEUE-WAIT-SECONDS 0.01)                ; coordinator が受け付けを始めるのを待つ間隔
(val REPORT-KINDS #("readiness" "metrics"))


;; --- 公開の値 --------------------------------------------------------------------------------------------

(defrecord SimWorker
  "sim の worker 1 台(本番の worker の --provides・--exclusive・--capacity・node に当たる)。provides = 提供する能力の名・
   exclusive = 専用の能力(この能力を needs に持つ job だけを受ける)・node = 置かれた k8s の node の名(空 = k8s の外)。"
  (#^ str name)
  (#^ frozenset provides)
  (setv #^ frozenset exclusive (frozenset))
  (setv #^ int capacity 10)
  (setv #^ str node ""))


(defrecord SimProcess
  "sim の宿が起こした process 1 つ(ProcessesOf の答えの要素)。job = 起こした時の job の名(task は task/<id>)・instance = 世代の名・
   attempt = worker の試行の番号・pid = sim の中の番号・spec-hash = 起こした spec の指紋・ended-ms / exit-code = 終わった時だけ
   (exit-code: 0 = 値で終わった / task は結果を書いて終わった・1 = 例外か Crash・3 = Program を解けない・-15 = 止めの合図)・
   detail = 終わった理由の 1 行(例外の型と文 — 本番の子の log の最後の行に当たる。値で終わった時は空)。"
  (#^ str job)
  (#^ str worker)
  (#^ str instance)
  (#^ int attempt)
  (#^ int pid)
  (#^ str spec-hash)
  (#^ int started-ms)
  (setv #^ (| int None) ended-ms None)
  (setv #^ (| int None) exit-code None)
  (setv #^ str detail ""))


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


;; --- sim の中の値 ---------------------------------------------------------------------------------------

(defrecord SimPlan
  "sim の 1 回の走りの筋(sim-cluster が引数から作る)。declaration = 最初の宣言(environ の上書きを重ねた行)・environ = job 名 →
   上書きの環境変数(Redeclare にも重ねる)。"
  (#^ System system)
  (#^ Declaration declaration)
  (#^ tuple workers)
  (#^ dict environ)
  (#^ str revision)
  (#^ int start-ms)
  (#^ ClusterTiming timing)
  (#^ ClusterNaming naming)
  (#^ WorkerPolicy policy))


(defrecord SimParts
  "coordinator の Pod の部品(1 回の走りに 1 組 — 世界の session val が 1 回だけ作る)。emulated-handlers が受ける要求の列・置き場・
   停止の合図・偽の k8s。"
  (#^ RequestQueue queue)
  (#^ MemoryWalStore store)
  (#^ StopState stop)
  (#^ KubeMemory kube))


(defrecord SimExit
  "sim の子 process の終わり方(本番の job_entry の入口の終わり方と同じ): code = exit-code・result = task の詰めた結果(service は None)・
   detail = 終わった理由の 1 行(例外の型と文・値で終わった時は空)。"
  (#^ int code)
  (#^ (| str None) result)
  (setv #^ str detail ""))


(defrecord HostTruth
  "worker の宿 1 つの真実(世界の session に在る)。processes = 子 process の観測・codes = 用意した版 → 時刻・probes = 入口の検め・
   statuses = 最後に出した状態の報告(heartbeat の本文)・last-ok-ms = coordinator が最後に返事をした時刻・fence-ms = 自己停止の閾値・
   last-desired = 最後に読めた job と task・programs = 取った詰めた Program(sha → 文字列)・results = 終わった task の結果(id →
   詰めた結果)・task-echo = 切り離した task の返事の行(id → 行)。"
  (#^ str boot)
  (#^ int boot-at)
  (#^ tuple processes)
  (#^ dict codes)
  (#^ dict probes)
  (#^ list statuses)
  (#^ int last-ok-ms)
  (#^ int fence-ms)
  (#^ tuple last-desired)
  (#^ dict programs)
  (#^ dict results)
  (#^ dict task-echo))


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
  "process pid の task の把手を覚える(止めの合図と Crash が取り消す相手)。"
  {:fields [(: pid int) (: task Task)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect HandleOf
  "process pid の task の把手(終わっていれば None)。"
  {:fields [(: pid int)] :answer (| Task None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NoteProcess
  "起こした process を記録する。"
  {:fields [(: process SimProcess)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect EndProcess
  "process の終わり(SimExit — exit-code・task の結果・理由)を書く(worker の宿の観測の exit-code・task の結果・記録の終わり)。"
  {:fields [(: worker str) (: pid int) (: ended SimExit)] :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CrashMarked
  "process pid の取り消しが Crash か(真なら exit 1・偽なら止めの合図の -15)。"
  {:fields [(: pid int)] :answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NoteReports
  "coordinator に届いた報告を記録する。"
  {:fields [(: batch tuple)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect WorkersStopping
  "worker が止まる時か(scenario が終わった後に真)。"
  {:answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StopWorkers
  "worker に止まれと合図する。"
  {:answer None :tags {:context "doeff-cluster" :role "intent"}})


;; --- 筋の組み立て(純粋)--------------------------------------------------------------------------------

(defk default-workers [system]
  {:pre [(: system System)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker を名指さない時の既定 = 系の全 job の needs の和を提供する 1 台(どの job も置ける)。"
  (val needs (frozenset (gfor j system.jobs n j.needs n)))
  #((SimWorker :name "sim-worker" :provides needs)))


(defk declaration-of [system revision environ]
  {:pre [(: system System) (: revision str) (: environ dict)] :post [(: % Declaration)] :tags {:context "doeff-cluster" :role "judgment"}}
  "系 → coordinator へ渡す宣言(本番の declare と同じ system-declaration)に、job ごとの environ の上書きを重ねるため(計画 2.7 の H・
   改訂 1 の M — whole.hy の overrides の置き換え先)。系に無い job・宣言の :environ に無い名・文字列でない値は断る(黙って足さない)。"
  (val names (sfor j system.jobs j.name))
  (for [#(job given) (.items environ)]
    (when (not-in job names)
      (raise (ValueError (.format "environ の上書きの job {!r} は系 {} に無い(在るのは {})" job system.name (sorted names)))))
    (when (not (isinstance given dict))
      (raise (TypeError (.format "environ の上書き {!r} は名 → 文字列の dict: {!r}" job given)))))
  (val declared (system-declaration system revision))
  (var rows [])
  (for [row declared.rows]
    (val overlay (.get environ (get row "name") {}))
    (val unknown (sorted (gfor k overlay :if (not-in k (get row "environ")) k)))
    (when unknown
      (raise (ValueError (.format "job {} の environ の上書き {} は宣言の :environ に無い名 — 宣言に書いた名だけを上書きする"
                                  (get row "name") unknown))))
    (val wrong (sorted (gfor #(k v) (.items overlay) :if (not (isinstance v str)) k)))
    (when wrong
      (raise (TypeError (.format "job {} の environ の上書き {} の値は文字列(本番の環境変数と同じ型)" (get row "name") wrong))))
    (:= rows (+ rows [(| row {"environ" (| (get row "environ") overlay)})])))
  (Declaration :rows rows :programs declared.programs))


(defk sim-plan [system workers environ revision start-ms timing policy]
  {:pre [(: system System) (: workers (| tuple None)) (: environ (| dict None)) (: revision str) (: start-ms int)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None))]
   :post [(: % SimPlan)] :tags {:context "doeff-cluster" :role "judgment"}}
  "sim-cluster の引数を検めて筋にするため(走らせる前に断る — environ の上書きの誤り・名の重なる worker)。"
  (<- fallback tuple (default-workers system))
  (val chosen (if (is workers None) fallback workers))
  (val names (lfor w chosen w.name))
  (when (or (not chosen) (!= (len names) (len (set names))) (not (all (gfor w chosen (isinstance w SimWorker)))))
    (raise (ValueError (.format "workers は名の重ならない SimWorker の 1 つ以上の tuple: {!r}" chosen))))
  (<- declaration Declaration (declaration-of system revision (or environ {})))
  (SimPlan :system system :declaration declaration :workers chosen :environ (or environ {}) :revision revision
           :start-ms start-ms :timing (or timing (ClusterTiming)) :naming (ClusterNaming) :policy (or policy (WorkerPolicy))))


(defk parts-of []
  {:pre [] :post [(: % SimParts)] :tags {:context "doeff-cluster" :role "foundation"}}
  "coordinator の Pod の部品を作るため(世界の handler が session で 1 回だけ呼ぶ)。"
  (SimParts :queue (RequestQueue) :store (MemoryWalStore) :stop (StopState) :kube (KubeMemory {})))


(defk fresh-hosts [plan]
  {:pre [(: plan SimPlan)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker ごとの起きた時の宿の真実を作るため(起きた時刻を最後の連絡とみなす — 本番の CoordinatorLink と同じ)。"
  (dfor w plan.workers
        w.name (HostTruth :boot (+ w.name "-boot") :boot-at plan.start-ms :processes #() :codes {} :probes {} :statuses []
                          :last-ok-ms plan.start-ms :fence-ms plan.timing.fence-ms :last-desired #() :programs {} :results {}
                          :task-echo {})))


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


(defk view-of [truth]
  {:pre [(: truth HostTruth)] :post [(: % WorldView)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宿の真実 → worker の観測(用意した木はすぐ揃う・子 process・入口の検め)にするため。"
  (WorldView (tuple (gfor k truth.codes (CodeView k CodeState.READY (+ "/sim/code/" k))))
             truth.processes
             (tuple (.values truth.probes))))


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


;; --- coordinator との話し方(scheduler の effect だけ — 柵の内側の宿の答えも使う)--------------------------------

(defk send-request [queue method path query body actor]
  {:pre [(: queue RequestQueue) (: method str) (: path str) (: query dict) (: body (| dict None)) (: actor (| str None))]
   :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の受け口(要求の列)へ 1 件送り、返事 #(status 本文) を待つため。止まっている coordinator には接続の失敗
   #(None {\"error\" …}) を返す(本番の送り手の接続の失敗に当たる)。"
  (if (not queue.up)
      #(None {"error" "coordinator に接続できない(止まっている)"})
      (do (<- promise Promise (CreatePromise))
          (.append queue.pending (Request method path query body :slot promise :actor actor :peer "sim"))
          (<- answer tuple (Wait promise.future))
          answer)))


(defk send-shaped [queue shape actor]
  {:pre [(: queue RequestQueue) (: shape tuple) (: actor (| str None))] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の送り手と同じ関数が作った要求 #(method path query 本文) を送るため。"
  (<- answer tuple (send-request queue (get shape 0) (get shape 1) (get shape 2) (get shape 3) actor))
  answer)


(deff answered-body [#^ tuple answer #^ str what]  ; defk にできない: 宿の答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % "返事の本文(形は口ごと)")] :tags {:context "doeff-cluster" :role "judgment"}}
  "返事 #(status 本文) の本文を返すため(300 以上・届かないなら理由つきの RemoteJobFailed — 本番の raise-for-status に当たる)。"
  (when (or (is (get answer 0) None) (>= (get answer 0) 300))
    (raise (RemoteJobFailed (.format "{}: coordinator の返事 {} {}" what (get answer 0) (get answer 1)))))
  (get answer 1))


(deff board-written [#^ tuple answer]  ; defk にできない: 宿の答えの節が返事を Program への答えに変える純粋な判断
  {:pre [(: answer tuple)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "盤の compare-and-set の返事を WriteShared の答えにするため(409 = 合わなかった = 偽 — 本番の SharedClient.write と同じ読み)。"
  (if (= (get answer 0) 409) False (do (answered-body answer "盤に書けない") True)))


(defk send-report [child kind payload]
  {:pre [(: child SimChild) (: kind str) (: payload dict)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "service の報告を本番の ServiceReportClient と同じ形で送るため。届かなくても業務を止めない(報告は観測)。"
  (val ctx child.ctx)
  (<- (send-shaped child.queue
                           (report-request ctx.job (| {"worker" ctx.worker "pid" child.pid "revision" ctx.revision} (.identity ctx))
                                           kind payload)
                           ctx.job))
  None)


(defk remote-outcome [queue ctx program needs name]
  {:pre [(: queue RequestQueue) (: ctx RunContext) (: program (| Program EffectBase)) (: needs frozenset) (: name str)]
   :post [(: % (| TaskSucceeded TaskFailed))] :tags {:context "doeff-cluster" :role "protocol"}}
  "RemoteJob を本番の remote-cluster と同じ手順で coordinator へ出し、結果を待つため: 詰めた Program を PUT /programs/<sha> で置き、
   POST /tasks(task-submit-body)で出し、問い合わせ(lease を延ばす)を終わるまで続け、抜ける時は task を落とす。送れない値は送る前に
   断る(encode-program の UnsendableProgram)。版は sim の宿の版(送り手の revision = この process の版)。"
  (val blob (encode-program program))
  (val sha (program-sha blob))
  (<- put tuple (send-request queue "PUT" (+ "/programs/" sha) {} {"blob" blob "versions" (current-versions)} ctx.job))
  (answered-body put "task の Program を置けない")
  (<- sent tuple (send-request queue "POST" "/tasks" {} (task-submit-body sha ctx.revision needs name TASK-LEASE-SECONDS None)
                               ctx.job))
  (val id (get (answered-body sent "task を出せない") "task"))
  (var outcome None)
  (try
    (while (is outcome None)
      (<- (Delay TASK-POLL-SECONDS))
      (<- polled tuple (send-request queue "GET" (+ "/tasks/" id) {} None ctx.job))
      ;; 届かない問い合わせは次の拍で送り直す(本番の send-idempotent と同じく、読みは何度送っても同じ意味)。
      (when (= (get polled 0) 200)
        (:= outcome (outcome-of (get polled 1) id ctx.revision))))
    (finally
      (<- (send-request queue "DELETE" (+ "/tasks/" id) {} None ctx.job))))
  outcome)


;; --- 柵と宿の答え(process ごと・Program のすぐ外)----------------------------------------------------------

(defhandler fence
  {:tags {:context "doeff-cluster" :role "protocol"}}
  ;; 宿の答えより外で、SIM-PASSABLE(scheduler と時計)の外の effect を本番の子と同じ未処理の例外で Program へ投げ返す(改訂 1 の B)。
  ;; これが無いと、sim の外側(検の handler・sim の世界)が本番には無い答えを黙って返し、本番で答えの無い effect が sim だけで通る。
  (EffectBase []
    :when (not (isinstance effect SIM-PASSABLE))
    (raise (UnhandledEffect (.format "sim の柵: 答えの無い effect {} ({!r}) — 本番の子 process でも答える handler が無い"
                                     (. (type effect) __name__) effect)))))


(defrecord SimChild
  "sim の子 process 1 つに宿が答える物(宿の答え host-answers の引数)。ctx = 宿の契約の run-context・program-path = Program の path・
   environ = 宣言の :environ(上書きを重ねた物 — 名 → 値)・queue = coordinator の受け口(要求の列)・pid = sim の中の process の番号。"
  (#^ RunContext ctx)
  (#^ str program-path)
  (#^ dict environ)
  (#^ RequestQueue queue)
  (#^ int pid))


(defhandler host-answers [#^ SimChild child]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 答えは process ごとに違い(世代・Program の path・environ)、宿の契約の Ask の鍵は本番の宿と同じなので、Ask で
  ;; 区別できない。柵の内側に在るので世界の effect で読むこともできない。
  ;; 本番の宿と土台の HTTP の handler が答える物に、同じ本文で答える(宿の契約 HOST-CONTRACT の 3 つ・クラスタの約束の effect)。
  ;; 柵の内側に在るので世界の effect を出さない — coordinator とは要求の列(値)と scheduler の effect だけで話す。
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
    (resume None))
  (ReadShared [prefix]
    (<- answer tuple (send-shaped child.queue (board-read-request prefix) child.ctx.job))
    (resume (answered-body answer "盤を読めない")))
  (WriteShared [key value expect ttl-seconds]
    (<- answer tuple (send-shaped child.queue (board-write-request key value expect ttl-seconds) child.ctx.job))
    (resume (board-written answer)))
  (LeaseOp [name op token permits ttl-ms]
    (<- answer tuple (send-shaped child.queue (lease-request name op token permits ttl-ms) child.ctx.job))
    (resume (answered-body answer (.format "lease {} の {}" name op))))
  (RemoteJob [program needs name]
    (<- outcome (remote-outcome child.queue child.ctx program needs name))
    (resume (settled-value outcome))))


(defk run-fenced [program child once]
  {:pre [(: program DoExpr) (: child SimChild) (: once bool)] :post [(: % SimExit)] :tags {:context "doeff-cluster" :role "program"}}
  "Program を柵と宿の答えの中で走らせ、終わり方を決めるため(本番の job_entry の service / task の入口の終わり方と同じ: service は
   値 = 0・例外 = 1、task は結果を書いて 0。止めの合図 = -15・Crash = 1)。"
  (try
    (<- value (with-handlers [fence (host-answers child)] program))
    (SimExit :code 0 :result (if once (encode-outcome (TaskSucceeded value)) None))
    (except [TaskCancelledError]
      (<- crashed bool (CrashMarked child.pid))
      (SimExit :code (if crashed 1 -15) :result None :detail (if crashed "Crash" "止めの合図")))
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
   走らせ、終わりを世界へ書く。"
  (val decoded (if (is blob None)
                   #(None (RemoteJobFailed (.format "Program {} を coordinator の置き場から取れていない" spec.program)))
                   (decoded-program blob)))
  (<- ended SimExit (match decoded
                      #(None refusal) (refused-exit refusal spec.once)
                      #(program None) (run-fenced program child spec.once)))
  (<- (EndProcess worker child.pid ended))
  None)


;; --- worker の偽の宿 ------------------------------------------------------------------------------------

(defk accepted-programs [queue worker wanted known]
  {:pre [(: queue RequestQueue) (: worker str) (: wanted list) (: known dict)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言の job と task のうち、まだ取っていない詰めた Program を coordinator の /programs/<sha> から取るため(本番の
   CoordinatorLink.accept-programs と同じ — 中身の sha256 がキーと合わない物は取らない・取れなければ次の拍で試し直す)。"
  (var fetched {})
  (for [sha wanted]
    (when (not-in sha known)
      (<- answer tuple (send-request queue "GET" (+ "/programs/" sha) {} None worker))
      (when (and (= (get answer 0) 200) (= (program-sha (get (get answer 1) "blob")) sha))
        (:= fetched (| fetched {sha (get (get answer 1) "blob")})))))
  fetched)


(defk heartbeat [worker]
  {:pre [(: worker SimWorker)] :post [(: % (| DesiredJobs DesiredUnreadable))] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の CoordinatorLink.poll の代役: 生存・能力・版・状態を同じ本文(heartbeat-body)で送り、返事の job と task を宣言として返す。
   届かなければ desired-when-unreachable(本番と同じ判断)。"
  (<- parts SimParts (PartsOf))
  (<- before HostTruth (HostTruthOf worker.name))
  (val body (heartbeat-body :name worker.name :provides (tuple (sorted worker.provides)) :exclusive (tuple (sorted worker.exclusive))
                            :node worker.node :capacity worker.capacity :versions (current-versions) :statuses before.statuses
                            :endpoint (+ "sim://" worker.name) :boot before.boot :boot-at before.boot-at :tools {}))
  (<- answer tuple (send-request parts.queue "POST" "/heartbeat" {} body worker.name))
  (<- now int (now-epoch-ms))
  (if (= (get answer 0) 200)
      (do (val reply (get answer 1))
          (val jobs (tuple (gfor j (get reply "jobs") (declared-job-spec j))))
          (val tasks (tuple (gfor t (.get reply "tasks" []) (task-spec t (Path "/sim/tasks" worker.name)))))
          (val ids (sfor t (.get reply "tasks" []) (get t "id")))
          (<- fetched dict (accepted-programs parts.queue worker.name (sorted (sfor s (+ jobs tasks) :if s.program s.program))
                                              before.programs))
          ;; 返事を待つ間に他の task が宿の真実を書く — 書く直前に読み直す。
          (<- truth HostTruth (HostTruthOf worker.name))
          (val timing (.get reply "timing"))
          (<- (PutHostTruth worker.name
                            (replace truth :last-ok-ms now :last-desired (+ jobs tasks)
                                     :fence-ms (if (and timing (in "fence_ms" timing)) (int (get timing "fence_ms")) truth.fence-ms)
                                     :programs (| truth.programs fetched)
                                     ;; 返事から外れた task の結果は落とす(本番の accept-tasks が結果の file を消すのと同じ)。
                                     :results (dfor #(k v) (.items truth.results) :if (in k ids) k v)
                                     :task-echo (dfor t (.get reply "tasks" []) :if (.get t "detached") (get t "id") (dict t)))))
          (DesiredJobs (+ jobs tasks)))
      (do (<- truth HostTruth (HostTruthOf worker.name))
          (desired-when-unreachable (- now truth.last-ok-ms) truth.fence-ms truth.last-desired #()
                                    (str (.get (get answer 1) "error" (get answer 0)))))))


(defk release-leases [queue worker instance]
  {:pre [(: queue RequestQueue) (: worker str) (: instance str)] :post [(: % int)] :tags {:context "doeff-cluster" :role "protocol"}}
  "終わった process(世代の名 instance)が持っていた lease を返すため(本番の handlers.release-leases と同じ要求 — token の頭
   「<worker>/<世代の名>/」の担い手を POST /leases/<名> の drop で外す)。答え = 返した数。届かなければ期限で切れる。"
  (val prefix (.format "{}/{}/" worker instance))
  (<- rows tuple (send-shaped queue (board-read-request SEMAPHORE-PREFIX) worker))
  (var dropped 0)
  (when (= (get rows 0) 200)
    (for [#(key row) (.items (get rows 1))]
      (when (is-not (drop-holders row prefix) None)
        (<- answer tuple (send-request queue "POST" (+ "/leases/" (url-quote (cut key (len SEMAPHORE-PREFIX) None) :safe "")) {}
                                       {"op" "drop" "token" prefix} worker))
        (when (and (is-not (get answer 0) None) (< (get answer 0) 300))
          (:= dropped (+ dropped 1))))))
  dropped)


(defhandler sim-host [#^ SimWorker worker]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 同じ組の中で worker ごとに別の宿を並べる(run-worker は自分の名を effect で問わない)ので Ask で区別できない。
  ;; 本物の run-worker の effect に偽の宿で答える(本番の組 = handlers.hy の local-host・coordinator-desired・status-to-coordinator・
  ;; lease-release-coordinator・stop-flag)。宿の真実は世界の session に在り、HostTruthOf / PutHostTruth で読み書きする。
  (ReadDesired []
    (<- desired (| DesiredJobs DesiredUnreadable) (heartbeat worker))
    (resume desired))
  (ObserveWorld []
    (<- truth HostTruth (HostTruthOf worker.name))
    (<- view WorldView (view-of truth))
    (resume view))
  (PrepareCode [revision]
    (<- now int (now-epoch-ms))
    (<- truth HostTruth (HostTruthOf worker.name))
    (<- (PutHostTruth worker.name (replace truth :codes (| {revision now} truth.codes))))
    (resume None))
  (PrepareEnv [key runtime-env warm]
    (<- now int (now-epoch-ms))
    (<- truth HostTruth (HostTruthOf worker.name))
    (<- (PutHostTruth worker.name (replace truth :codes (| {key now} truth.codes))))
    (resume None))
  (SweepEnvs [pinned]
    (resume None))
  (ProbeEntry [spec code-path]
    (<- truth HostTruth (HostTruthOf worker.name))
    (val key (spec-hash spec))
    (<- (PutHostTruth worker.name (replace truth :probes (| truth.probes {key (ProbeView key ProbeState.PASSED)}))))
    (resume None))
  (ForgetProbes [keep]
    (<- truth HostTruth (HostTruthOf worker.name))
    (<- (PutHostTruth worker.name (replace truth :probes (dfor #(k v) (.items truth.probes) :if (in k keep) k v))))
    (resume None))
  (StartJob [spec attempt code-path]
    (<- parts SimParts (PartsOf))
    (<- now int (now-epoch-ms))
    (<- pid int (NextPid))
    (val instance (.format "{}-p{}" worker.name pid))
    (<- ctx RunContext (run-context-of worker.name spec attempt instance))
    ;; 観測と記録を Spawn の前に書く(Spawn の直後に新しい task が先に走って終わっても、終わりを書く相手が在る)。
    (<- truth HostTruth (HostTruthOf worker.name))
    (<- (PutHostTruth worker.name
                      (replace truth :processes (+ truth.processes #((ProcessView spec.name spec attempt pid now :instance instance))))))
    (<- (NoteProcess (SimProcess :job spec.name :worker worker.name :instance instance :attempt attempt :pid pid
                                 :spec-hash (spec-hash spec) :started-ms now)))
    ;; 節の中から Spawn する — 新しい task は節の外側の handler(世界・時計)だけを持ち、run-worker の中の handler を持たない。
    (<- program-path str (program-path-of spec.program))
    (val child (SimChild :ctx ctx :program-path program-path :environ (dict spec.environ) :queue parts.queue :pid pid))
    (<- task Task (Spawn (sim-process worker.name spec child (.get truth.programs spec.program))))
    (<- (KeepHandle pid task))
    (resume None))
  (SignalJob [name pid stage]
    (<- handle (| Task None) (HandleOf pid))
    (when (is-not handle None)
      (<- (Cancel handle)))
    (resume None))
  (ReapJob [name pid outcome exit-code]
    (<- truth HostTruth (HostTruthOf worker.name))
    (<- (PutHostTruth worker.name (replace truth :processes (tuple (gfor p truth.processes :if (!= p.pid pid) p)))))
    (resume None))
  (RetireJob [name pid new-name]
    (<- truth HostTruth (HostTruthOf worker.name))
    (<- (PutHostTruth worker.name
                      (replace truth :processes (tuple (gfor p truth.processes
                                                             (if (= p.pid pid) (replace p :name new-name :retired-from name) p))))))
    (resume None))
  (ReleaseLeases [instance]
    (<- parts SimParts (PartsOf))
    (<- (release-leases parts.queue worker.name instance))
    (resume None))
  (PublishStatus [statuses note]
    (<- truth HostTruth (HostTruthOf worker.name))
    (<- (PutHostTruth worker.name (replace truth :statuses (status-report statuses truth.task-echo truth.results))))
    (resume None))
  (WorkerStopRequested []
    (<- stopping bool (WorkersStopping))
    (resume stopping)))


(defk run-sim-worker [worker policy]
  {:pre [(: worker SimWorker) (: policy WorkerPolicy)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "worker 1 台: 本物の run-worker を偽の宿の上で回す(止まれの合図で全 job を止めの手順で回収して終わる)。"
  (<- (with-handlers [(sim-host worker)] (run-worker policy)))
  worker.name)


;; --- coordinator の Pod ---------------------------------------------------------------------------------

(defhandler observe-requests
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 調停ループの一番内側: 取った要求のうち service の報告(ReportReady・ReportMetrics)を世界へ記録する(ReportsOf の材料)。
  ;; 効果はそのまま外側(本物の組)へ出し直す。
  (NextRequests [timeout-seconds limit]
    (<- batch list effect)
    (<- now int (now-epoch-ms))
    (<- reports tuple (reports-in batch now))
    (when reports
      (<- (NoteReports reports)))
    (resume batch)))


(defk fail-open-requests [queue reason]
  {:pre [(: queue RequestQueue) (: reason str)] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "止まった coordinator が返事をしていない要求(並んでいた物)に接続の失敗を返すため(送り手を待たせたままにしない)。"
  (val open (list queue.pending))
  (setattr queue "pending" [])
  (for [request open]
    (<- (CompletePromise request.slot #(None {"error" reason}))))
  (len open))


(defk coordinator-pod []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod の代役: 置き場から読み直して(無ければ新しい状態で)本物の調停ループを emulated-handlers の上で回し、止まれの
   合図で止まる。答え = 止まった時の状態の版。"
  (<- plan SimPlan (PlanOf))
  (<- parts SimParts (PartsOf))
  (<- now int (now-epoch-ms))
  (val state (if (.exists parts.store)
                 (load-state NO-STATE-FILE parts.store now)
                 (ClusterState :started-ms now :task-prefix (fresh-task-prefix now))))
  (setattr parts.queue "up" True)
  (<- ended ClusterState (with-handlers (+ (emulated-handlers parts.queue parts.store parts.stop parts.kube {}) [observe-requests])
                           (run-coordinator state plan.timing plan.naming)))
  (setattr parts.queue "up" False)
  (<- (fail-open-requests parts.queue "coordinator が止まった(返事なし)"))
  ended.revision)


(defk await-coordinator [queue]
  {:pre [(: queue RequestQueue)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod が受け付けを始めるまで待つため(宣言を先に送って接続の失敗にしない)。"
  (while (not queue.up)
    (<- (Delay QUEUE-WAIT-SECONDS)))
  None)


(defk apply-declaration [queue declaration actor]
  {:pre [(: queue RequestQueue) (: declaration Declaration) (: actor str)] :post [(: % tuple)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言を本番の declare(declare.apply-declaration)と同じ順と本文で coordinator へ書くため: 詰めた Program を PUT /programs/<sha> で
   置いてから、Service ごとに無ければ POST(create-body)・在れば読んだ版を付けて PUT(spec-for-update)。答え = 書いた Service の名。"
  (for [#(sha blob) (sorted (.items declaration.programs))]
    (<- put tuple (send-request queue "PUT" (+ "/programs/" sha) {}
                                {"blob" blob "versions" (get (get (get declaration.rows 0) "run") "versions")} actor))
    (answered-body put (+ "program " sha)))
  (for [row declaration.rows]
    (val path (+ "/resources/Service/" (url-quote (get row "name") :safe "")))
    (<- current tuple (send-request queue "GET" path {} None actor))
    (<- written tuple (if (= (get current 0) 404)
                          (send-request queue "POST" "/resources/Service" {} (create-body row None) actor)
                          (send-request queue "PUT" path {}
                                        (let [body (answered-body current (+ "Service " (get row "name")))]
                                          {"resourceVersion" (get body "resourceVersion") "spec" (spec-for-update row (get body "spec") None)})
                                        actor)))
    (answered-body written (+ "Service " (get row "name"))))
  (tuple (gfor row declaration.rows (get row "name"))))


;; --- 世界 -----------------------------------------------------------------------------------------------

(defhandler sim-world [#^ SimPlan plan]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 筋(系・worker・environ の上書き)は検ごとに違う値で、世界の外側に Ask へ答える物を置くと検の筋書きからも読めて
  ;; しまう(世界の答えは sim の中の仕組みだけが読む)。
  ;; sim の世界 1 つ(coordinator の部品・worker の宿の真実・process の把手と記録・届いた報告・止まれの合図)を session に持ち、仕組みの
  ;; effect と検の effect に答える。session の値の置き場(doeff_core_effects の state)はこの handler の外側に要る(sim-cluster が置く)。
  ;; 真実(本当に動いている process・届いた報告)はここが持つ — coordinator の信念(状態の中の報告)ではない。
  (session val parts !(parts-of))
  (session var hosts !(fresh-hosts plan))
  (session var handles {})
  (session var next-pid 0)
  (session var crashing (frozenset))
  (session var log #())
  (session var reports #())
  (session var stopping False)
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
    (resume None))
  (HandleOf [pid]
    (resume (.get handles pid)))
  (NoteProcess [process]
    (:= log (+ log #(process)))
    (resume None))
  (EndProcess [worker pid ended]
    (<- now int (now-epoch-ms))
    (val truth (get hosts worker))
    (val view (next (gfor p truth.processes :if (= p.pid pid) p) None))
    (val task-id (if (and (is-not view None) view.spec.once) (cut view.spec.name 5 None) None))
    (:= hosts (| hosts {worker (replace truth
                                        :processes (tuple (gfor p truth.processes (if (= p.pid pid) (replace p :exit-code ended.code) p)))
                                        :results (if (and task-id (is-not ended.result None))
                                                     (| truth.results {task-id ended.result})
                                                     truth.results))}))
    (:= log (tuple (gfor r log (if (= r.pid pid) (replace r :ended-ms now :exit-code ended.code :detail ended.detail) r))))
    (:= handles (dfor #(k v) (.items handles) :if (!= k pid) k v))
    (resume None))
  (CrashMarked [pid]
    (resume (in pid crashing)))
  (NoteReports [batch]
    (:= reports (+ reports batch))
    (resume None))
  (WorkersStopping []
    (resume stopping))
  (StopWorkers []
    (:= stopping True)
    (resume None))
  ;; --- 検の effect ---
  (Crash [name]
    (val victims (lfor r log :if (and (= r.job name) (is r.ended-ms None) (in r.pid handles)) r.pid))
    (:= crashing (| crashing (frozenset victims)))
    (for [pid victims]
      (<- (Cancel (get handles pid))))
    (resume (len victims)))
  (Redeclare [system]
    (<- declaration Declaration (declaration-of system plan.revision plan.environ))
    (<- names tuple (apply-declaration parts.queue declaration DECLARE-ACTOR))
    (resume names))
  (ReportsOf [name]
    (resume (tuple (gfor r reports :if (= r.job name) r))))
  (ProcessesOf [name]
    (resume (tuple (gfor r log :if (= r.job name) r))))
  (ReadinessOf [name]
    (<- answer tuple (send-request parts.queue "GET" (+ "/resources/Service/" (url-quote name :safe "")) {} None DECLARE-ACTOR))
    (resume (match (get answer 0)
              200 (SimReadiness :state (get (get answer 1) "status" "ready")
                                :reason (str (.get (get (get answer 1) "status") "readyReason" "")))
              _ (SimReadiness :state "Missing" :reason (str (get answer 1))))))
  (SharedRows [prefix]
    (<- answer tuple (send-shaped parts.queue (board-read-request prefix) DECLARE-ACTOR))
    (resume (answered-body answer "盤を読めない"))))


;; --- 入口 -----------------------------------------------------------------------------------------------

(defk sim-main [scenario]
  {:pre [(: scenario (| Program EffectBase))] :post [(: % "scenario の答え(型は筋書きごと)")] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod を起こし、宣言を書き、worker を並べてから scenario を走らせ、終われば worker・coordinator の順に止めるため。"
  (<- plan SimPlan (PlanOf))
  (<- parts SimParts (PartsOf))
  (<- pod Task (Spawn (coordinator-pod)))
  (<- (await-coordinator parts.queue))
  (<- (apply-declaration parts.queue plan.declaration DECLARE-ACTOR))
  (var workers [])
  (for [w plan.workers]
    (<- t Task (Spawn (run-sim-worker w plan.policy)))
    (:= workers (+ workers [t])))
  (try
    (<- answer scenario)
    answer
    (finally
      (<- (StopWorkers))
      (<- (Gather #* workers))
      (setattr parts.stop "requested" True)
      (<- (Wait pod)))))


(defk sim-cluster [system scenario * [workers None] [environ None] [revision "sim"] [start-ms SIM-START-MS] [timing None] [policy None]]
  {:pre [(: system System) (: scenario (| Program EffectBase)) (: workers (| tuple None)) (: environ (| dict None)) (: revision str) (: start-ms int)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None))]
   :post [(: % "scenario の答え(型は筋書きごと)")]
   :tags {:context "doeff-cluster" :role "entry"}}
  "系 system(sim の土台で作った System の値)を本物の coordinator と worker の上で走らせ、scenario(検の筋書きの Program — 同じ
   scheduler・同じ仮想の時計で並んで走る)の答えを返す。workers = SimWorker の tuple(既定 = 全 job の needs の和を提供する 1 台)・
   environ = job 名 → 宣言の :environ に重ねる環境変数(宣言に無い名は断る)・revision = 宣言の版・start-ms = 仮想の時計の起点・
   timing / policy = coordinator と worker の時間の設定(既定 = 本番の既定)。自分で scheduler を持つ(外に scheduler が在っても無くても走る)。"
  (<- plan SimPlan (sim-plan system workers environ revision start-ms timing policy))
  (<- answer (scheduled (with-handlers [(sim-time-handler :start-time (datetime-of-epoch-ms start-ms)) (session-store) (sim-world plan)]
                          (sim-main scenario))))
  answer)
