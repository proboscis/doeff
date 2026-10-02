;;; 手元の 1 台の cluster(#3031・#3032・ADR-DOE-CLUSTER-001 R8 と追補): coordinator 1 つと worker を、この機体の子 process として
;;; 起こし、筋書き(scenario の Program)を走らせ、終わりに全部止める。
;;;
;;;   (local-machine-cluster scenario :machine (LocalMachine :work-dir "/tmp/x" :port 18080 :workers #((SimWorker :name "w1" :provides #{"a"}))))
;;;
;;; sim-cluster(local.hy・1 process の中の本物の coordinator と、偽の機体の上の本物の worker)と同じ入口の形で、違いは土台の handler の組
;;; だけ: ここでは本物の process・本物の HTTP・本物の時計。起こし方は配備と同じ deploy/boot.sh を、配備と同じ環境変数(ROLE・LISTEN_PORT・
;;; WORK_DIR・COORDINATOR_URL・WORKER_NAME・WORKER_PROVIDES ほか)で起こす — 2 つ目の起こし方を作らない。worker の顔ぶれは sim と同じ値
;;; (SimWorker の name・provides・exclusive・capacity・node)で渡し、命令の引数を足さない。
;;;
;;; 筋書きが出せる effect(machine-answers が答える):
;;;   ReadCoordinator path        coordinator の口の GET の本文(sim と同じ)。
;;;   ReadinessOf 名              GET /resources/Service/<名> の status の ready(無ければ Missing — sim と同じ答え)。
;;;   KillWorker 名               worker の process を SIGKILL で落とす(答え = 落とした数 — もう居なければ 0)。
;;;   StopWorker 名               worker を優雅に止める(SIGTERM → stop-grace 秒 → SIGKILL・抜けるまで待つ)。
;;;   StopCoordinator 秒          coordinator を優雅に止め、秒の間止めてから同じ置き場で作り直し、起き上がるまで待って答える。
;;;   CrashCoordinator 秒         coordinator を SIGKILL で落とし(返事をせずに落ちる)、秒の後に同じ置き場で作り直して答える。
;;;   CutWorker・StallWorker・FailRoute は答えない — MachineCannotAnswer で、その effect の名と訳を出して止める(網を切る・固める・5xx を
;;;   返させるのは sim だけ・ADR の追補 (3) の残り)。系の宣言(Redeclare)と job を落とす(Crash)は、手元の worker が job の code を
;;;   手に入れる道と一緒に #3032 の子で足す。
;;; sim との違い: sim の StopCoordinator / CrashCoordinator は次の拍で止まり、筋書きと並んで秒の後に作り直す。ここでは作り直して起き上がる
;;; まで答えを返さない(止まっている間の要求を筋書きが出すことは無い)。
;;;
;;; 止め方: 筋書きが値で終わっても例外で終わっても、worker を先に、coordinator を後に StopProcess で止める(SIGTERM → stop-grace 秒 →
;;; SIGKILL・process の group ごと)。止めるのは今の顔ぶれ(作り直した coordinator を含む — MachineCell)。この module はテストの環境で、
;;; 本番の code は読まない(sim の dir の決まり — architecture.hy)。
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "entry"})
(import json)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import pathlib [Path])
(import urllib.parse [quote :as url-quote])
(import doeff [with-handlers Program EffectBase])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.file_effects [MakeDirectory FileFailed])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.process_effects [StartProcess StopProcess PollProcess SignalProcess ProcessSignal ProcessSignalled ProcessStarted
                                            ProcessNotStarted ProcessRunning ProcessExited ProcessNotChild EnvMode EnvEntry])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [Delay async-time-handler])
(import doeff_cluster.shared.intent.protocol [PlainText])
(import doeff_cluster.shared.intent.cluster_control [ServiceReadiness ReadinessOf KillWorker StopWorker StopCoordinator
                                                     CrashCoordinator])
(import doeff_cluster.sim.local [SimWorker ReadCoordinator CutWorker StallWorker FailRoute])

;; 配備と同じ起動の script(packages/doeff-cluster/deploy/boot.sh — この file は src/doeff_cluster/sim/ に在る)。
(val BOOT-SCRIPT (str (/ (get (. (Path __file__) parents) 3) "deploy" "boot.sh")))
;; 起き上がりを問い直す間隔・SIGKILL の後に回収を問い直す間隔(秒)。
(val PROBE-SECONDS 0.2)


(defclass MachineCannotAnswer [Exception]
  "手元の 1 台の cluster が答えない effect(網を切る・固める・5xx を返させる — sim だけが答える)を筋書きが出した。")


(defrecord LocalMachine
  "手元の 1 台の cluster の置き方。work-dir = 作業の dir の親(coordinator は coordinator/・worker は workers/<名>/ — 出力の log も
   その下)・port = coordinator の受け口(127.0.0.1)・workers = worker の顔ぶれ(sim と同じ SimWorker — name・provides・exclusive・
   capacity・node を使う)・boot-seconds = coordinator と worker の起き上がりを待つ上限の秒・stop-grace = 止める時に SIGTERM から
   SIGKILL までの猶予の秒。"
  (#^ str work-dir)
  (#^ int port)
  (#^ (get tuple #(SimWorker ...)) workers)
  (setv #^ float boot-seconds 120.0)
  (setv #^ float stop-grace 30.0))


(defrecord MachineProcess
  "起こした役 1 つ(name = coordinator か worker の名・pid = StartProcess の答え・log = 出力の file・home = 作業の dir・env = boot.sh に
   渡した環境変数 — 作り直す時に同じ物で起こす)。"
  (#^ str name)
  (#^ int pid)
  (#^ str log)
  (#^ str home)
  (#^ (get tuple #(EnvEntry ...)) env))


(defclass MachineCell []
  "起こした役の今の顔ぶれの入れ物(作り直した coordinator で入れ替わる — 終わりの止めはこの今の顔ぶれを止める)。宛先の部品の
   RouteCell と同じく、組み立て(local-machine-cluster)が作って handler と本体に渡し、書き換えるのは本体と handler の節だけ。"
  (defn #^ None __init__ [self]
    (setv self.roles #())))


(defk coordinator-url [machine]
  {:pre [(: machine LocalMachine)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator の受け口の URL を組むため。"
  (+ "http://127.0.0.1:" (str machine.port)))


(defk coordinator-env [machine]
  {:pre [(: machine LocalMachine)] :post [(: % (get tuple #(EnvEntry ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "boot.sh の ROLE=coordinator に渡す環境変数を組むため(配備の coordinator と同じ名)。"
  #((EnvEntry :name "ROLE" :value "coordinator")
    (EnvEntry :name "LISTEN_PORT" :value (str machine.port))
    (EnvEntry :name "WORK_DIR" :value (str (/ (Path machine.work-dir) "coordinator")))))


(defk worker-env [machine worker url]
  {:pre [(: machine LocalMachine) (: worker SimWorker) (: url str)] :post [(: % (get tuple #(EnvEntry ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "boot.sh の ROLE=worker に渡す環境変数を組むため(配備の worker と同じ名。世代と準備の file は worker ごとの dir に置く — 既定の
   /tmp の file は同じ機体の worker どうしで重なる)。"
  (val home (/ (Path machine.work-dir) "workers" worker.name))
  #((EnvEntry :name "ROLE" :value "worker")
    (EnvEntry :name "COORDINATOR_URL" :value url)
    (EnvEntry :name "WORKER_NAME" :value worker.name)
    (EnvEntry :name "WORKER_PROVIDES" :value (.join "," (sorted worker.provides)))
    (EnvEntry :name "WORKER_EXCLUSIVE" :value (.join "," (sorted worker.exclusive)))
    (EnvEntry :name "WORKER_CAPACITY" :value (str worker.capacity))
    (EnvEntry :name "NODE_NAME" :value worker.node)
    (EnvEntry :name "WORK_DIR" :value (str home))
    (EnvEntry :name "DOEFF_WORKER_BOOT_FILE" :value (str (/ home "boot")))
    (EnvEntry :name "DOEFF_WORKER_READY_FILE" :value (str (/ home "ready")))))


(defk started-role [name home env]
  {:pre [(: name str) (: home str) (: env (get tuple #(EnvEntry ...)))] :post [(: % MachineProcess)]
   :tags {:context "doeff-cluster" :role "program"}}
  "boot.sh を 1 つの役で、この機体の子 process として起こすため(出力は home の下の log・process の group を持たせて止める時に子孫ごと)。"
  (<- made (MakeDirectory home))
  (when (isinstance made FileFailed)
    (raise (RuntimeError (+ name " の作業の dir を作れない: " (repr made)))))
  (val log (str (/ (Path home) (+ name ".log"))))
  (<- started (StartProcess :argv #("sh" BOOT-SCRIPT) :env env :env-mode EnvMode.EXTEND :stdout-path log :stderr-path log
                            :process-group True :reap-group True))
  (match started
    (ProcessStarted :pid pid) (MachineProcess :name name :pid pid :log log :home home :env env)
    (ProcessNotStarted :detail detail) (raise (RuntimeError (+ name " を起こせない: " detail)))))


(defk state-of [url]
  {:pre [(: url str)] :post [(: % (| dict None))] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の GET /state を 1 度読むため(届かない・200 でなければ None — 起き上がりの途中)。HTTP の答えを読む境界なので、
   JSON の object を dict のまま返す(読む所は await-up の ready? だけ)。"
  (<- answer (HttpRequest "GET" (+ url "/state") :timeout-seconds 5.0 :max-retries 0 :failures-as-values True))
  (if (and (isinstance answer HttpResponse) (= answer.status 200))
      (json.loads answer.text)
      None))


(defk still-running [role]
  {:pre [(: role MachineProcess)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "起き上がりを待つ間に役の process が終わっていたら、log の path を添えて止めるため(待ち続けない)。"
  (<- polled (PollProcess role.pid))
  (when (isinstance polled ProcessExited)
    (raise (RuntimeError (+ role.name " が起き上がる前に終わった(exit " (str polled.exit-code) ")— " role.log)))))


(defk await-up [url role ready? seconds]
  {:pre [(: url str) (: role MachineProcess) (: ready? Callable) (: seconds float)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の /state が ready? を満たすまで待つため(役の process が終わるか seconds を過ぎたら止める)。"
  (var waited 0.0)
  (var up False)
  (while (not up)
    (<- (still-running role))
    (<- state (state-of url))
    (if (and (is-not state None) (ready? state))
        (:= up True)
        (do (when (> waited seconds)
              (raise (RuntimeError (+ role.name " が " (str seconds) " 秒で起き上がらない — " role.log))))
            (<- (Delay PROBE-SECONDS))
            (:= waited (+ waited PROBE-SECONDS)))))
  None)


(defk stopped [roles stop-grace]
  {:pre [(: roles (get tuple #(MachineProcess ...))) (: stop-grace float)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "起こした役を並べた順に止めて回収するため(SIGTERM → stop-grace 秒 → SIGKILL・group ごと — もう回収した役は ProcessNotChild で何もしない)。"
  (for [role roles]
    (<- (StopProcess :pid role.pid :stop-grace stop-grace)))
  None)


(defk killed [role]
  {:pre [(: role MachineProcess)] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "役の process を SIGKILL で落として回収するため(答え = 落とした数 — もう終わっていた・回収済みなら 0)。"
  (<- sent (SignalProcess :pid role.pid :signal ProcessSignal.KILL))
  (if (and (isinstance sent ProcessSignalled) sent.delivered)
      (do (var gone False)
          (while (not gone)
            (<- polled (PollProcess role.pid))
            (if (isinstance polled ProcessRunning)
                (<- (Delay PROBE-SECONDS))
                (:= gone True)))
          1)
      0))


(defk role-named [cell name]
  {:pre [(: cell MachineCell) (: name str)] :post [(: % MachineProcess)] :tags {:context "doeff-cluster" :role "judgment"}}
  "今の顔ぶれから名前の役を引くため(居なければ名前を出して止める — 筋書きの名前の誤り)。"
  (val found (lfor role cell.roles :if (= role.name name) role))
  (when (not found)
    (raise (KeyError (+ "手元の 1 台に " name " という役は居ない(居るのは "
                        (.join "・" (lfor role cell.roles role.name)) ")"))))
  (get found 0))


(defk coordinator-remade [url cell machine down-seconds]
  {:pre [(: url str) (: cell MachineCell) (: machine LocalMachine) (: down-seconds float)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "program"}}
  "止めた(落とした)coordinator を down-seconds の後に同じ置き場・同じ環境変数で作り直し、起き上がるまで待って今の顔ぶれを入れ替えるため。"
  (<- old MachineProcess (role-named cell "coordinator"))
  (<- (Delay down-seconds))
  (<- fresh MachineProcess (started-role old.name old.home old.env))
  (setv cell.roles (tuple (lfor role cell.roles (if (= role.name old.name) fresh role))))
  (<- (await-up url fresh (fn [_state] True) machine.boot-seconds))
  None)


;; 引数に残す理由: 宛先の URL・役の入れ物・置き方は、この handler を積む組み立て(local-machine-cluster)が LocalMachine から作る
;; 値で、読む Ask の鍵が無い(本番の宛先の部品は RouteCell を引数で受ける — detached-cluster と同じ)。
(defhandler machine-answers [#^ str url #^ MachineCell cell #^ LocalMachine machine]
  (ReadCoordinator [path]
    (<- answer (HttpRequest "GET" (+ url path) :timeout-seconds 10.0 :max-retries 0 :failures-as-values True))
    (when (not (isinstance answer HttpResponse))
      (raise (RuntimeError (+ "coordinator に届かない: " path " — " (repr answer)))))
    (resume (if (.startswith (.get answer.headers "content-type" "") "application/json")
                (json.loads answer.text)
                (PlainText answer.text))))
  (ReadinessOf [name]
    (<- answer (HttpRequest "GET" (+ url "/resources/Service/" (url-quote name :safe "")) :timeout-seconds 10.0 :max-retries 0
                            :failures-as-values True))
    (when (not (isinstance answer HttpResponse))
      (raise (RuntimeError (+ "coordinator に届かない: Service " name " — " (repr answer)))))
    (resume (if (= answer.status 200)
                (do (val status (get (json.loads answer.text) "status"))
                    (ServiceReadiness :state (get status "ready") :reason (str (.get status "readyReason" ""))))
                (ServiceReadiness :state "Missing" :reason answer.text))))
  (KillWorker [name]
    (<- role MachineProcess (role-named cell name))
    (<- count int (killed role))
    (resume count))
  (StopWorker [name]
    (<- role MachineProcess (role-named cell name))
    (<- (StopProcess :pid role.pid :stop-grace machine.stop-grace))
    (resume None))
  (StopCoordinator [seconds]
    (<- role MachineProcess (role-named cell "coordinator"))
    (<- (StopProcess :pid role.pid :stop-grace machine.stop-grace))
    (<- (coordinator-remade url cell machine (float seconds)))
    (resume None))
  (CrashCoordinator [seconds]
    (<- role MachineProcess (role-named cell "coordinator"))
    (<- _count int (killed role))
    (<- (coordinator-remade url cell machine (float seconds)))
    (resume None))
  (CutWorker [name seconds]
    (raise (MachineCannotAnswer (+ "CutWorker(" name ")— 手元の 1 台は網を切らない(sim だけが答える)"))))
  (StallWorker [name seconds]
    (raise (MachineCannotAnswer (+ "StallWorker(" name ")— 手元の 1 台は worker を固めない(sim だけが答える)"))))
  (FailRoute [method path status seconds]
    (raise (MachineCannotAnswer (+ "FailRoute(" method " " path ")— 手元の 1 台は 5xx を返させない(sim だけが答える)")))))


(defk machine-run [scenario cell machine]
  {:pre [(: scenario (| Program EffectBase)) (: cell MachineCell) (: machine LocalMachine)]
   :post [(: % "scenario の答え(型は筋書きごと)")]
   :tags {:context "doeff-cluster" :role "program"}}
  "coordinator と worker を起こし、全部が名乗るまで待ってから筋書きを走らせ、終わりに今の顔ぶれを worker・coordinator の順で止めるため。"
  (<- url str (coordinator-url machine))
  (<- env tuple (coordinator-env machine))
  (<- coordinator MachineProcess (started-role "coordinator" (str (/ (Path machine.work-dir) "coordinator")) env))
  (setv cell.roles #(coordinator))
  (try
    (<- (await-up url coordinator (fn [_state] True) machine.boot-seconds))
    (for [worker machine.workers]
      (<- worker-vars tuple (worker-env machine worker url))
      (<- role MachineProcess (started-role worker.name (str (/ (Path machine.work-dir) "workers" worker.name)) worker-vars))
      (setv cell.roles (+ cell.roles #(role))))
    (for [role (cut cell.roles 1 None)]
      (<- (await-up url role (fn [state] (in role.name (.get state "workers" {}))) machine.boot-seconds)))
    (<- answer scenario)
    answer
    (finally
      (<- (stopped (tuple (lfor role cell.roles :if (!= role.name "coordinator") role)) machine.stop-grace))
      (<- (stopped (tuple (lfor role cell.roles :if (= role.name "coordinator") role)) machine.stop-grace)))))


(defk local-machine-cluster [scenario * machine]
  {:pre [(: scenario (| Program EffectBase)) (: machine LocalMachine)] :post [(: % "scenario の答え(型は筋書きごと)")]
   :tags {:context "doeff-cluster" :role "entry"}}
  "手元の 1 台の cluster の上で筋書きを走らせる入口(sim-cluster と同じ形 — 違いは土台の handler の組だけ)。答え = 筋書きの答え。"
  (<- url str (coordinator-url machine))
  (val cell (MachineCell))
  (<- answer (scheduled (with-handlers [(await-handler) (async-time-handler) (http-production-handler) subprocess-handler
                                        os-file-handler (machine-answers url cell machine)]
                          (machine-run scenario cell machine))))
  answer)
