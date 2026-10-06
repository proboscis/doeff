;;; worker の止めの印 — main の信号の handler が StopState を立て、stop-flag が調整ループの WorkerStopRequested に答える(I/O を持たない
;;; 言い換え)。handlers.hy から分けた(#2026)。
;;; 拍の間の眠り(#3834)は止めの合図で起きる: 眠りの起こし方のまとめが StopWake で眠りの bell を掛け、信号の handler が立てる時に満たす
;;; (ExternalPromise.complete は信号の受け手からも呼んでよい口)。拍の上限まで眠り続けずに止まり始める。
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_core_effects.scheduler [ExternalPromise])
(import doeff_cluster.worker.intent.worker_model [WorkerStopRequested])
(import doeff_cluster.worker.protocol.observations [StopWake])


(defclass StopState []
  "worker の止めの印(main の信号の handler が request で立て、stop-flag が WorkerStopRequested に答える)。bell = 拍の間の眠りの bell
   (StopWake が掛ける — 立てた時に満たす・None = 眠っていない)。"
  (defn #^ None __init__ [self]
    (setv self.requested False)
    (setv #^ (| ExternalPromise None) self.bell None))

  (defn #^ None request [self]
    "止めの印を立て、眠っている拍を起こす(信号の受け手から呼ばれる — 満たすのは thread と信号をまたいでよい口)。"
    (setv self.requested True)
    (setv bell self.bell)
    (when (is-not bell None)
      (.complete bell True))))


(defhandler stop-flag [#^ StopState state]
  (WorkerStopRequested [] (resume state.requested))
  (StopWake [bell]
    ;; 眠りの bell を掛ける・外す(None)。既に立っていれば、掛けたその場で満たす(掛ける前に来た合図を取りこぼさない)。
    (setv state.bell bell)
    (when (and state.requested (is-not bell None))
      (.complete bell True))
    (resume None)))
