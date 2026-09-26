;; 切り離した task の検が共有する組の部品(検の module ではない — 検どうしで import すると pytest の assert の書き換えが .hy の検を
;; Python として読もうとするので、共有する物はここに置く)。使い手 = test_detached.hy・test_detached_runners.hy。
;;   ENV / slow-add          … 送る Program(base = 100 の reader の上で n を足す)
;;   RigWorker ほか           … 担い手: 本物の CoordinatorLink で heartbeat を送り、割り当てられた task を同じ VM で走らせる
;;   MemoryCoordinator       … 本物の coordinator の判断(api_policy.respond / tick)を httpx.MockTransport の後ろに置く
(require doeff-hy.macros [defk <-])
(import json)
(import urllib.parse [urlsplit parse-qsl])
(import pathlib [Path])
(import httpx)
(import doeff [with_handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn Cancel TaskCancelledError])
(import doeff_time [Delay SimClock])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.cluster_model [ClusterState ClusterTiming Request])
(import doeff_cluster.api_policy [respond tick])
(import doeff_cluster.handlers [CoordinatorLink])
(import doeff_cluster.job_entry [RunContext env-handlers])
(import doeff_cluster.worker_model [DesiredJobs JobStatus JobPhase])
(import doeff_cluster.remote_model [TaskSucceeded decode-program encode-outcome failed-from])

(setv ENV "tests.fixtures.envs:plain_env")                        ; base = 100 の reader


;; --- 送る Program -------------------------------------------------------------------------------

(defk slow-add [seconds n]
  {:pre [(: seconds float) (: n int)] :post [(: % int)]}
  (<- (Delay seconds))
  (<- base int (Ask "base"))
  (+ base n))


;; --- 担い手(coordinator の組・served の組が共有する)--------------------------------------------------------
;; 本物の CoordinatorLink で heartbeat を送り、割り当てられた task の file(blob)を受け、同じ VM の scheduler の task として
;; env の handler の組の下で走らせ、結果の file を書いて報告する(子 process の入口 job_entry task と同じ手順 — 子 process そのものは
;; test_remote.hy が通す)。

(defclass RigWorker []
  (defn __init__ [self #^ str url #^ Path task-dir #^ dict versions [transport None] #^ str [name "w1"] #^ (| dict None) [labels None]]
    ;; name / labels = worker の名乗り(既定 = label の無い w1 — 担い手を 2 つ以上並べる検 test_detached_runners.hy が名指す)。
    (setv self.link (CoordinatorLink url name (or labels {}) 10 20000 :task-dir (str task-dir) :versions versions :transport transport)
          self.handles {} self.done #{} self.dead False self.loop None)))


(defn #^ dict task-args [#^ tuple args]
  "task の job の引数(\"task\" \"--blob\" P \"--result\" P \"--env\" E \"--versions\" V)→ {欄: 値}。"
  (dfor i (range 1 (len args) 2) (cut (get args i) 2 None) (get args (+ i 1))))


(defk run-rig-task [worker name args revision]
  {:pre [(: worker RigWorker) (: name str) (: args dict) (: revision str)] :post [(: % bool)]}
  (setv program (decode-program (.read-text (Path (get args "blob")) :encoding "ascii"))
        handlers (env-handlers (get args "env") {} (RunContext "" "w1" revision name)))
  (try
    (<- value (with-handlers handlers program))
    (setv outcome (TaskSucceeded value))
    (except [error TaskCancelledError]
      (raise))
    (except [error Exception]
      (setv outcome (failed-from error))))
  (.write-text (Path (get args "result")) (encode-outcome outcome) :encoding "ascii")
  (.add worker.done name)
  True)


(defk worker-tick [worker]
  {:pre [(: worker RigWorker)] :post [(: % bool)]}
  (setv desired (.poll worker.link))
  (when (isinstance desired DesiredJobs)
    (setv specs (dfor s desired.jobs :if s.once s.name s))
    (for [#(name spec) (.items specs)]
      (when (not-in name worker.handles)
        (<- handle (Spawn (run-rig-task worker name (task-args spec.args) spec.revision)))
        (setv (get worker.handles name) handle)))
    ;; 宣言から外れた task(取り消し・lost・結果を受け取り終えた)は止める。
    (for [name (list worker.handles)]
      (when (not-in name specs)
        (<- (Cancel (.pop worker.handles name)))
        (.discard worker.done name))))
  (setv worker.link.statuses
        (.report worker.link (tuple (gfor name worker.handles
                                          (JobStatus name (if (in name worker.done) JobPhase.FINISHED JobPhase.RUNNING)
                                                     "r" "r" None 1)))))
  True)


(defk worker-loop [worker seconds]
  {:pre [(: worker RigWorker) (: seconds float)] :post [(: % bool)]}
  (while (not worker.dead)
    (<- (Delay seconds))
    (<- (worker-tick worker)))
  True)


;; --- coordinator(本物の判断を memory の上で)------------------------------------------------------------

(defclass MemoryCoordinator []
  "本物の api_policy.respond / tick を httpx の MockTransport の後ろに置く。時刻は仮想の時計。要求の前に tick する(本物の調停
   ループは要求の無い拍に tick する)。"
  (defn __init__ [self #^ SimClock clock]
    (setv self.clock clock self.state (ClusterState) self.timing (ClusterTiming)))

  (defn handle [self request]
    (setv now (clock-ms self.clock)
          split (urlsplit (str request.url))
          body (if request.content (json.loads request.content) None))
    (setv self.state (tick self.state now self.timing))
    (setv #(state status reply) (respond self.state (Request request.method split.path (dict (parse-qsl split.query)) body
                                                             :actor (.get request.headers "x-actor"))
                                         now self.timing))
    (setv self.state state)
    (httpx.Response status :json reply)))


