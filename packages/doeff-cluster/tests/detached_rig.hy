;; 切り離した task の検が共有する組の部品(検の module ではない — 検どうしで import すると pytest の assert の書き換えが .hy の検を
;; Python として読もうとするので、共有する物はここに置く)。使い手 = test_detached.hy・test_detached_runners.hy。
;;   slow-add                … 送る Program(自分で並べた base = 100 の reader の上で n を足す — 実行先は handler を足さない)
;;   RigWorker ほか           … 担い手: 本物の coordinator への口 で heartbeat を送り、割り当てられた task を同じ VM で走らせる
;;   MemoryCoordinator       … 本物の coordinator の判断(api_policy.respond / tick)を httpx.MockTransport の後ろに置く
;;   RIG-PROVIDES            … 担い手の既定の能力(sim-cluster の組の worker も同じ能力を名乗る — 同じ needs の筋書きを回すため)
(require doeff-hy.macros [defk <- val var])
(import json)
(import urllib.parse [urlsplit parse-qsl])
(import pathlib [Path])
(import httpx)
(import doeff [with_handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader])
(import doeff_core_effects.scheduler [Spawn Cancel Task TaskCancelledError])
(import doeff_time [Delay SimClock])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.api_policy [tick])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import tests.link_rig [LinkRig])
(import doeff_cluster.worker.core.launch [program-file])
(import doeff_cluster.foundation.host_contract [environ-reader])
(import doeff_cluster.worker.entry.job_entry [read-program])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs JobStatus] doeff_cluster.shared.intent.job_model [JobPhase JobSpec])
(import doeff_cluster.shared.intent.remote_model [TaskSucceeded])
(import doeff_cluster.shared.protocol.program_codec [encode-outcome])
(import doeff_cluster.shared.core.remote_rules [failed-from])


;; 担い手の既定の能力(筋書きの task の needs — sim の組・coordinator の組・served の組の担い手が共に提供する)。
(val RIG-PROVIDES #("local"))


;; --- 送る Program -------------------------------------------------------------------------------

(defk slow-add-body [seconds n]
  {:pre [(: seconds float) (: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  (<- (Delay seconds))
  (<- base int (Ask "base"))
  (+ base n))

(defk slow-add [seconds n]
  {:pre [(: seconds float) (: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "送る Program: 自分の handler(base = 100 の reader)を本体の with-handlers で並べる(ADR-DOE-CLUSTER-001 R2 — 担い手は handler を
   足さない)。時計(Delay)と scheduler は担い手の外側の時計が答える(sim の柵の許可表と同じ扱い)。"
  (<- total int (with-handlers [(reader {"base" 100})] (slow-add-body seconds n)))
  total)


;; --- 担い手(coordinator の組・served の組が共有する)--------------------------------------------------------
;; 本物の coordinator への口 で heartbeat を送り、割り当てられた task の Program を置き場から cache へ受け、同じ VM の scheduler の task として
;; そのまま走らせ(handler を足さない — Program が自分で並べる)、結果の file を書いて報告する(子 process の入口 job_entry task と同じ手順 — 子 process そのものは
;; test_remote.hy が通す)。

(defclass RigWorker []
  (defn #^ None __init__ [self #^ str url #^ Path task-dir #^ dict versions #^ (| httpx.BaseTransport None) [transport None] #^ str [name "w1"]
                  #^ tuple [provides RIG-PROVIDES] #^ tuple [exclusive #()]]
    ;; name / provides / exclusive = worker の名乗り(既定 = sim の組の worker と同じ能力 local の w1 — sim と coordinator の組で同じ
    ;; needs の筋書きを回すため。担い手を 2 つ以上並べる検 test_detached_runners.hy が名指す)。
    (setv self.link (LinkRig url name provides 10 20000 :task-dir (str task-dir) :versions versions :transport transport
                                     :exclusive exclusive)
          self.handles {} self.dead False)
    ;; heartbeat のループの task(走らせるまでは None)。
    (setv #^ (| Task None) self.loop None)
    ;; 終えた job の名(型を書く — 空の #{} だけでは要素の型が決まらず、add が型検査で断られる)。
    (setv #^ (get set str) self.done #{})))


(defn #^ dict task-args [#^ tuple args]
  "task の job の引数(\"task\" \"--result\" P)→ {欄: 値}。詰めた Program は引数でなく spec.program(置き場のキー)で運ばれる。"
  (dfor i (range 1 (len args) 2) (cut (get args i) 2 None) (get args (+ i 1))))


(defk run-rig-task [worker spec]
  {:pre [(: worker RigWorker) (: spec JobSpec)] :post [(: % bool)]}
  "担い手の子 process の入口 job_entry task と同じ手順を同じ VM で: coordinator への口が /programs/<sha> から取った cache の file を
   job_entry と同じ read-program で読み(版 → 復元)、走らせ、結果の file を書く。task の :environ は、本番の worker が子の環境変数に
   置いて子の土台の (environ-reader) が読む物を、同じ読みの定義 environ-reader に spec.environ を渡して答える(他の名は外側へ)。"
  (assert (is-not spec.program None) f"task の job は Program の置き場のキーを持つ: {spec}")
  (val read (read-program (str (program-file (.program-dir worker.link) spec.program)) ""))
  (var outcome None)
  (if (is-not (get read 1) None)
      (:= outcome (failed-from (get read 1)))
      (try
        (<- value (with-handlers [(environ-reader (dict spec.environ))] (get read 0)))
        (:= outcome (TaskSucceeded value))
        (except [error TaskCancelledError]
          (raise))
        (except [error Exception]
          (:= outcome (failed-from error)))))
  (.write-text (Path (get (task-args spec.args) "result")) (encode-outcome outcome) :encoding "ascii")
  (.add worker.done spec.name)
  True)


(defk worker-tick [worker]
  {:pre [(: worker RigWorker)] :post [(: % bool)]}
  (setv desired (.poll worker.link))
  (when (isinstance desired DesiredJobs)
    (setv specs (dfor s desired.jobs :if s.once s.name s))
    (for [#(name spec) (.items specs)]
      (when (not-in name worker.handles)
        (<- handle (Spawn (run-rig-task worker spec)))
        (setv (get worker.handles name) handle)))
    ;; 宣言から外れた task(取り消し・lost・結果を受け取り終えた)は止める。
    (for [name (list worker.handles)]
      (when (not-in name specs)
        (<- (Cancel (.pop worker.handles name)))
        (.discard worker.done name))))
  (setv worker.link.state.statuses
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
  (defn #^ None __init__ [self #^ SimClock clock #^ ClusterTiming [timing (ClusterTiming)]]
    (setv self.clock clock self.state (ClusterState) self.timing timing))

  (defn #^ httpx.Response handle [self #^ httpx.Request request]
    (setv now (clock-ms self.clock)
          split (urlsplit (str request.url))
          body (if request.content (json.loads request.content) None))
    (setv self.state (tick self.state now self.timing))
    (setv #(state status reply) (responded self.state (http-request request.method split.path (dict (parse-qsl split.query)) body
                                                             :actor (.get request.headers "x-actor"))
                                         now self.timing))
    (setv self.state state)
    (httpx.Response status :json reply)))


