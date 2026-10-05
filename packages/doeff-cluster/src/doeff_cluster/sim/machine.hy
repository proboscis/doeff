;;; 手元の 1 台の cluster(#3031・#3032・ADR-DOE-CLUSTER-001 R8 と追補): coordinator 1 つと worker を、この機体の子 process として
;;; 起こし、筋書き(scenario の Program)を走らせ、終わりに全部止める。
;;;
;;;   (local-machine-cluster scenario :machine (LocalMachine :work-dir "/tmp/x" :port 18080 :workers #((SimWorker :name "w1" :provides #{"a"} :task-reserve 0))))
;;;
;;; sim-cluster(local.hy・1 process の中の本物の coordinator と、偽の機体の上の本物の worker)と同じ入口の形で、違いは土台の handler の組
;;; だけ: ここでは本物の process・本物の HTTP・本物の時計。起こし方は配備と同じ deploy/boot.sh を、配備と同じ環境変数(ROLE・LISTEN_PORT・
;;; WORK_DIR・COORDINATOR_URL・WORKER_NAME・WORKER_PROVIDES ほか)で起こす — 2 つ目の起こし方を作らない。worker の顔ぶれは sim と同じ値
;;; (SimWorker の name・provides・exclusive・capacity・task-reserve・node)で渡し、命令の引数を足さない。
;;;
;;; 筋書きが出せる effect(machine-answers が答える):
;;;   ReadCoordinator path        coordinator の口の GET の本文(sim と同じ)。
;;;   ReadinessOf 名              GET /resources/Service/<名> の status の ready(無ければ Missing — sim と同じ答え)。
;;;   AwaitReadiness 名 状態 秒    ReadinessOf と同じ読みを WAIT-PROBE-SECONDS ごとにして、状態になるか秒を過ぎるまで待つ(過ぎたら
;;;                              ReadinessWaitExpired — 本物の coordinator は長い待ちの読みを持たないので、detached-cluster の
;;;                              AwaitProcessEnded と同じく境界の handler が読む・#3053)。
;;;   AwaitJobProcess job 除く 秒  GET /state に起こした worker が名乗った job の pid のうち、除く pid の外の物が出るまで同じ間隔で待つ。
;;;   KillWorker 名              worker の process を SIGKILL で落とす(答え = 落とした数 — もう居なければ 0)。
;;;   StopWorker 名               worker を優雅に止める(SIGTERM → stop-grace 秒 → SIGKILL・抜けるまで待つ)。
;;;   StopCoordinator 秒          coordinator を優雅に止め、秒の間止めてから同じ置き場で作り直し、起き上がるまで待って答える。
;;;   CrashCoordinator 秒         coordinator を SIGKILL で落とし(返事をせずに落ちる)、秒の後に同じ置き場で作り直して答える。
;;;   Redeclare 系               宣言の CLI と同じ system-declaration と apply-declaration で、起こした coordinator へ宣言を書く(答え =
;;;                              宣言した Service の名 — sim と同じ)。版は LocalMachine の revision・job の code は worker が code-repo
;;;                              (配備と同じ CODE_REPO_URL — 版の木)からその版で取り出す(#3040)。LocalMachine に実行環境
;;;                              (runtime-env)が在れば宣言に載せ、worker は配備と同じ鍵の表(WORKER_REPOS)の道でその repo を取り込み、
;;;                              root を用意して job を動かす(#3042 — 入口の検めを通る本番の土台の job はこの道でだけ起きる)。
;;;   Crash 名                   worker が動かしている job の process を group ごと SIGKILL で落とす(答え = 落とした数)。worker は 0 以外の
;;;                              終わりとして本物の判断で起こし直す(sim の Crash は exit 1・ここは signal の終わり — worker の数え方は同じ)。
;;;   CutWorker・StallWorker・FailRoute は答えない — MachineCannotAnswer で、その effect の名と訳を出して止める(網を切る・固める・5xx を
;;;   返させるのは sim だけ・ADR の追補 (3) の残り)。
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
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.file_effects [MakeDirectory FileFailed])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.process_effects [StartProcess StopProcess PollProcess SignalProcess ProcessSignal ProcessSignalled ProcessStarted
                                            ProcessNotStarted ProcessRunning ProcessExited ProcessNotChild EnvMode EnvEntry
                                            RunProcess ProcessOutcome])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [Delay async-time-handler])
(import doeff_cluster.foundation.process_versions [this-process-versions])
(import doeff_cluster.shared.entry.declare [apply-declaration])
(import doeff_cluster.shared.entry.service_build [system-declaration])
(import doeff_cluster.shared.intent.protocol [PlainText])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.intent.cluster_control [ServiceReadiness ReadinessOf KillWorker StopWorker StopCoordinator
                                                     CrashCoordinator Redeclare Crash AwaitReadiness ServiceFailed ReadinessWaitExpired
                                                     AwaitJobProcess JobProcessSeen JobProcessWaitExpired])
(import doeff_cluster.sim.local [SimWorker ReadCoordinator CutWorker StallWorker FailRoute])

;; 配備と同じ起動の script(packages/doeff-cluster/deploy/boot.sh — この file は src/doeff_cluster/sim/ に在る)。
(val BOOT-SCRIPT (str (/ (get (. (Path __file__) parents) 3) "deploy" "boot.sh")))
;; 起き上がりを問い直す間隔・SIGKILL の後に回収を問い直す間隔(秒)。
(val PROBE-SECONDS 0.2)
;; coordinator の口の読み(準備の状態の読みと待ち・/state の読み)と、待ちの読み直しの間隔 WAIT-PROBE-SECONDS は、配備の cluster の handler と
;; 共有する部品(shared/protocol/coordinator_reads.hy — #3294 で移した)。job の process の待ちは、自分で起こした worker の行に絞るのでここに残す。
(import doeff_cluster.shared.protocol.coordinator_reads [WAIT-PROBE-SECONDS readiness-read readiness-awaited state-of])
;; Redeclare が宣言の書きに載せる送り手の名(宣言の CLI の --actor と同じ役 — 出来事の記録に残る名)。
(val MACHINE-ACTOR "local-machine")


(defclass MachineCannotAnswer [Exception]
  "手元の 1 台の cluster が答えない effect(網を切る・固める・5xx を返させる — sim だけが答える)を筋書きが出した。")


(defrecord GitSource
  "宣言の repo の url(配備と同じ remote の綴り)を、この機体の checkout の path から読ませる組: remote = 宣言に書く url・path = 同じ
   commit を持つ手元の checkout(bare でも作業木でもよい)。"
  (#^ str remote)
  (#^ str path))


(defrecord LocalMachine
  "手元の 1 台の cluster の置き方。work-dir = 作業の dir の親(coordinator は coordinator/・worker は workers/<名>/ — 出力の log も
   その下)・port = coordinator の受け口(127.0.0.1)・workers = worker の顔ぶれ(sim と同じ SimWorker — name・provides・exclusive・
   capacity・node を使う)・boot-seconds = coordinator と worker の起き上がりを待つ上限の秒・stop-grace = 止める時に SIGTERM から
   SIGKILL までの猶予の秒。job の code の道(#3040): code-repo = 版の木の git(worker の CODE_REPO_URL — 配備と同じ名。空 = 版の木を
   持たない worker)・revision = Redeclare が宣言に書く版(code-repo の commit — sim-cluster の revision と同じ役)・runtime-env =
   Redeclare が宣言に載せる実行環境(repo と commit と uv の lock — sim-cluster の runtime-env と同じ役。None = 版の木の道。#3042):
   worker は配備と同じ鍵の表(WORKER_REPOS)の道でその repo を取り込み、root を用意して job を動かす。git-sources = 宣言の remote の url を
   手元の checkout から読ませる組(GitSource の列 — 空 = 宣言の url をそのまま読む): 宣言は配備と同じ remote の綴りのまま置き(送り手の
   宣言の組み立てを手元用に分けない)、この機体の worker の git にだけ url.<path>.insteadOf を環境変数で渡す — 手元の 1 台の worker は
   配備の鍵を持たないので、remote へは取りに行かない。"
  (#^ str work-dir)
  (#^ int port)
  (#^ (get tuple #(SimWorker ...)) workers)
  (setv #^ float boot-seconds 120.0)
  (setv #^ float stop-grace 30.0)
  (setv #^ str code-repo "")
  (setv #^ str revision "")
  (setv #^ (| RuntimeEnv None) runtime-env None)
  (setv #^ (get tuple #(GitSource ...)) git-sources #()))


(defrecord MachineProcess
  "起こした役 1 つ(name = coordinator か worker の名・pid = StartProcess の答え・log = 出力の file・home = 作業の dir・env = boot.sh に
   渡した環境変数 — 作り直す時に同じ物で起こす)。"
  (#^ str name)
  (#^ int pid)
  (#^ str log)
  (#^ str home)
  (#^ (get tuple #(EnvEntry ...)) env))


(defrecord JobProcess
  "worker が動かしている job の process 1 つ(worker = 動かしている worker の役・pid = /state の statuses に worker が名乗った pid —
   shim の下で自分の process group の先頭に起こした子)。"
  (#^ MachineProcess worker)
  (#^ int pid))


(defclass MachineCell []
  "起こした役の今の顔ぶれの入れ物(作り直した coordinator で入れ替わる — 終わりの止めはこの今の顔ぶれを止める)。宛先の部品の
   RouteCell と同じく、組み立て(local-machine-cluster)が作って handler と本体に渡し、書き換えるのは本体と handler の節だけ。"
  (defn #^ None __init__ [self]
    (setv #^ (get tuple #(MachineProcess ...)) self.roles #())))


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


(defk repo-key-table [machine]
  {:pre [(: machine LocalMachine)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker の鍵の表(boot.sh の WORKER_REPOS — 空白で並べた「<url>=<鍵の名>」)を、実行環境の repo の url を鍵なしで並べて組むため
   (手元の repo は鍵なしで読む。表は鍵を結ぶだけで url を断らない — 載せるのは配備と同じ表の置き場の道(WORKER_ACCESS_DIR)を
   通すため。実行環境が無ければ空)。"
  (if (is machine.runtime-env None)
      ""
      (.join " " (lfor repo machine.runtime-env.repos (+ repo.url "=")))))


(defk git-source-env [sources]
  {:pre [(: sources (get tuple #(GitSource ...)))] :post [(: % (get tuple #(EnvEntry ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "宣言の remote の url を手元の checkout から読ませるため、git の環境変数の設定(GIT_CONFIG_COUNT と KEY_n・VALUE_n — git の設定の file より
   強い)で url.<path>.insteadOf = <remote> を並べる(空なら何も足さない)。boot.sh が鍵つきの repo に書く insteadOf と同じ仕組みで、
   worker の git の子は環境を継ぐ。"
  (if (not sources)
      #()
      (+ #((EnvEntry :name "GIT_CONFIG_COUNT" :value (str (len sources))))
         (tuple (gfor [i source] (enumerate sources)
                      entry #((EnvEntry :name (.format "GIT_CONFIG_KEY_{}" i) :value (.format "url.{}.insteadOf" source.path))
                              (EnvEntry :name (.format "GIT_CONFIG_VALUE_{}" i) :value source.remote))
                      entry)))))


(defk worker-env [machine worker url]
  {:pre [(: machine LocalMachine) (: worker SimWorker) (: url str)] :post [(: % (get tuple #(EnvEntry ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "boot.sh の ROLE=worker に渡す環境変数を組むため(配備の worker と同じ名。世代と準備の file は worker ごとの dir に置く — 既定の
   /tmp の file は同じ機体の worker どうしで重なる)。鍵の表と git の設定の置き場 WORKER_ACCESS_DIR も worker ごとの dir に置く — 既定の
   $HOME/.doeff-worker-repos は、同じ機体で同じ HOME の本物の worker の鍵の表と重なり、上書きする(#3042)。"
  (val home (/ (Path machine.work-dir) "workers" worker.name))
  (<- repos str (repo-key-table machine))
  (<- sources (get tuple #(EnvEntry ...)) (git-source-env machine.git-sources))
  (<- boot (get tuple #(EnvEntry ...)) (worker-boot-env machine worker url home repos))
  (+ boot sources))


(defk worker-boot-env [machine worker url home repos]
  {:pre [(: machine LocalMachine) (: worker SimWorker) (: url str) (: home Path) (: repos str)] :post [(: % (get tuple #(EnvEntry ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "boot.sh の ROLE=worker に渡す、配備の worker と同じ名の環境変数を並べるため(worker-env の本体)。"
  #((EnvEntry :name "ROLE" :value "worker")
    (EnvEntry :name "COORDINATOR_URL" :value url)
    (EnvEntry :name "WORKER_NAME" :value worker.name)
    (EnvEntry :name "WORKER_PROVIDES" :value (.join "," (sorted worker.provides)))
    (EnvEntry :name "WORKER_EXCLUSIVE" :value (.join "," (sorted worker.exclusive)))
    (EnvEntry :name "WORKER_CAPACITY" :value (str worker.capacity))
    (EnvEntry :name "WORKER_TASK_RESERVE" :value (str worker.task-reserve))
    (EnvEntry :name "NODE_NAME" :value worker.node)
    (EnvEntry :name "WORK_DIR" :value (str home))
    (EnvEntry :name "CODE_REPO_URL" :value machine.code-repo)
    (EnvEntry :name "WORKER_REPOS" :value repos)
    (EnvEntry :name "WORKER_ACCESS_DIR" :value (str (/ home "access")))
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


(defk running-jobs [state name roles]
  {:pre [(: state dict) (: name str) (: roles (get tuple #(MachineProcess ...)))] :post [(: % (get tuple #(JobProcess ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator の /state の statuses(worker の名 → 名乗った job の行)から、起こした worker が動かしている job name の process を引くため
   (pid の無い行 — 起こす前・終わった後 — は外す)。state は HTTP の答えの JSON の object。"
  (val statuses (.get state "statuses" {}))
  (tuple (gfor role roles
               row (.get (.get statuses role.name {}) "jobs" [])
               :if (and (= (.get row "name") name) (isinstance (.get row "pid") int))
               (JobProcess :worker role :pid (get row "pid")))))


(defk job-crashed [job]
  {:pre [(: job JobProcess)] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "job の process を、その process group ごと SIGKILL で落とすため(答え = 落とした数)。SignalProcess は自分が起こした子にしか送らない
   (他人の process に送らない)— job は worker の子なので、ps で親が当の worker であることを確かめてから kill を走らせる。/state の pid が
   終わった後に別の process へ使い回されていたら落とさない(多くの会話が使う機体で他人の process を落とさない)。"
  (<- parent ProcessOutcome (RunProcess :argv #("ps" "-o" "ppid=" "-p" (str job.pid)) :env-mode EnvMode.EXTEND :timeout 10.0))
  (if (and (= parent.exit-code 0) (= (.strip parent.stdout) (str job.worker.pid)))
      (do (<- sent ProcessOutcome (RunProcess :argv #("kill" "-KILL" "--" (+ "-" (str job.pid))) :env-mode EnvMode.EXTEND
                                              :timeout 10.0))
          (if (= sent.exit-code 0) 1 0))
      0))


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


(defk job-process-awaited [url cell job excluding seconds]
  {:pre [(: url str) (: cell MachineCell) (: job str) (: excluding (get tuple #(int ...))) (: seconds float)]
   :post [(: % (| JobProcessSeen JobProcessWaitExpired))] :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitJobProcess に答えるため: coordinator の GET /state に起こした worker が名乗った job の pid のうち、excluding の外の物(小さい順の
   最初)が出るまで WAIT-PROBE-SECONDS ごとに読む(seconds を過ぎたら JobProcessWaitExpired・届かない読みは名乗り無しと数える)。"
  (var found None)
  (var waited 0.0)
  (while (and (is found None) (<= waited seconds))
    (<- state (| dict None) (state-of url))
    (when (is-not state None)
      (<- jobs tuple (running-jobs state job (tuple (lfor role cell.roles :if (!= role.name "coordinator") role))))
      (val fresh (sorted (gfor one jobs :if (not-in one.pid excluding) one.pid)))
      (when fresh
        (:= found (get fresh 0))))
    (when (is found None)
      (<- (Delay WAIT-PROBE-SECONDS))
      (:= waited (+ waited WAIT-PROBE-SECONDS))))
  (if (is found None)
      (JobProcessWaitExpired :job job :excluding excluding :waited-seconds waited)
      (JobProcessSeen :job job :pid found)))


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
    (<- readiness ServiceReadiness (readiness-read url name))
    (resume readiness))
  (AwaitReadiness [name state timeout-seconds]
    (<- awaited (| ServiceReadiness ServiceFailed ReadinessWaitExpired) (readiness-awaited url name state (float timeout-seconds)))
    (resume awaited))
  (AwaitJobProcess [job excluding timeout-seconds]
    (<- seen (| JobProcessSeen JobProcessWaitExpired) (job-process-awaited url cell job excluding (float timeout-seconds)))
    (resume seen))
  (Redeclare [system environ]
    (<- versions dict (this-process-versions))
    ;; その宣言し直しの上書き(渡されなければ上書き無し — 本番の declare と同じく宣言ごとの上書き・#3131)。台数は系の値の各 job の
    ;; :replicas が行に載って書かれる(0 = 取り下げ — #3487)。
    (val declaration (system-declaration system machine.revision :runtime-env machine.runtime-env :versions versions
                                         :environ (if (is environ None) {} environ)))
    (<- placed bool (apply-declaration url declaration MACHINE-ACTOR))
    (when (not placed)
      (raise (RuntimeError (+ "宣言を書けない(上の slog の行に返事)— " (.join "・" (lfor row declaration.rows (get row "name")))))))
    (resume (tuple (lfor row declaration.rows (get row "name")))))
  (Crash [name]
    (<- state (| dict None) (state-of url))
    (when (is state None)
      (raise (RuntimeError (+ "coordinator の /state を読めない — Crash(" name ")"))))
    (<- jobs tuple (running-jobs state name (tuple (lfor role cell.roles :if (!= role.name "coordinator") role))))
    (var count 0)
    (for [job jobs]
      (<- one int (job-crashed job))
      (:= count (+ count one)))
    (resume count))
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
  (<- answer (scheduled (with-handlers [(await-handler) slog-handler (async-time-handler) (http-production-handler) subprocess-handler
                                        os-file-handler (machine-answers url cell machine)]
                          (machine-run scenario cell machine))))
  answer)
