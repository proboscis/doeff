;; worker の停止は核の止めの効果(doeff_core_effects の StopRequested・AwaitStop)で知る(#3871 の単位 3)。worker 独自の
;; 止めの印(worker/protocol/stop.hy の StopState・stop-flag)と止めの問い(WorkerStopRequested)は消した。本物の process に本物の
;; SIGTERM を送る確かめは test_shared_wake.hy の test-a-sigterm-ends-the-worker-tick-wait。
(require doeff-hy.macros [deftest defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import importlib.util)
(import doeff_core_effects.handlers [slog-discard-handler state])
(import doeff_core_effects.stop_signal_effects [RaiseStop])
(import doeff_core_effects.stop_signal_handlers [scripted-stop-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.worker.intent.worker_model :as worker-model)
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState DesiredJobs ReadDesired ObserveWorld WorldView PublishStatus
                                                  EnvReport])
(import doeff_cluster.worker.core.program [run-worker])
(import tests.wake_fixtures [wakes-every])


(defhandler empty-host
  ;; 宣言は空・子は無い宿(止めの問いには答えない — 外側の核の答え手が答える)。
  (ReadDesired [] (resume (DesiredJobs #())))
  (ObserveWorld [] (resume (WorldView #() #())))
  (EnvReport [] (resume None))
  (PublishStatus [statuses note] (resume None)))


(defk stopped-worker [policy]
  {:pre [(: policy WorkerPolicy)] :post [(: % WorkerState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "核の止めを先に立ててから run-worker を回し、その答えを返すため。"
  (<- (RaiseStop "signal 15"))
  (<- final WorkerState (run-worker policy))
  final)


(deftest test-the-worker-stops-on-the-core-stop-effect
  ;; 失敗ケース 3: 核の止め(scripted-stop-handler の RaiseStop — 本番は os-signal-stop-handler の SIGTERM)が立っていれば、run-worker は
  ;; 最初の拍で止まりの手順に入り、子が無いので抜ける。直す前は run-worker が worker 独自の WorkerStopRequested を問い、答え手が無い。
  (val policy (WorkerPolicy))
  (<- final WorkerState
      ((state) ((sim-time-handler :clock (SimClock)) (scripted-stop-handler
        ((wakes-every 100) (slog-discard-handler (empty-host (stopped-worker policy))))))))
  (assert (= final.records {}) final))


(deftest test-the-worker-own-stop-names-are-gone
  ;; 失敗ケース 2: 消した名の使い手は 0 — module worker/protocol/stop と、worker_model の WorkerStopRequested が無い。
  (assert (is (importlib.util.find-spec "doeff_cluster.worker.protocol.stop") None))
  (assert (not (hasattr worker-model "WorkerStopRequested"))))
