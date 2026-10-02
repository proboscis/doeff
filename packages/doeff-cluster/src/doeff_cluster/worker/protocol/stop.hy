;;; worker の止めの印 — main の信号の handler が StopState を立て、stop-flag が調整ループの WorkerStopRequested に答える(I/O を持たない
;;; 言い換え)。handlers.hy から分けた(#2026)。
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster.worker.intent.worker_model [WorkerStopRequested])


(defclass StopState []
  "worker の止めの印(main の信号の handler が立て、stop-flag が WorkerStopRequested に答える)。"
  (defn #^ None __init__ [self] (setv self.requested False)))


(defhandler stop-flag [#^ StopState state]
  (WorkerStopRequested [] (resume state.requested)))
