;;; coordinator の handler の組 — 環境の違いは、ここに並ぶ「handler の組」という値だけで表す(operator 2026-09-25 逐語
;;; "such environmental changes are to be all done via handler swapping")。調停ループの Program(coordinator.run-coordinator)は
;;; 環境を知らない。
;;;
;;;   production-handlers  本番: HTTP の受付(RequestInbox — 別 thread の HTTP server)・追記の log の置き場(WalStore — file と fsync)・
;;;                        k8s の API(ServiceAccount の token が在る時)・doeff-time の async-time-handler。
;;;   emulated-handlers    手元のまねた環境(業務の側の模擬環境): 要求は process の中の列(RequestQueue — 模擬の
;;;                        worker・client が並べ、返事は promise で受ける)・置き場は memory(MemoryWalStore — 再起動の模擬は同じ
;;;                        置き場から load-state で読み直す)・k8s は memory の偽物。時計(GetTime / Delay)は組の外側の
;;;                        sim の時計(doeff-time の仮想の sim-time-handler か、壁の async-time-handler)が答えるので、この組は時計を
;;;                        持たない。
;;;
;;; 組は with_handlers に渡す list(外側が先)。選ぶのは composition root(coordinator.main・業務の側の模擬環境)だけ。
;;; 本番の受付の handler は foundation/coordinator_inbox.hy(coordinator.hy から分けた — この module と coordinator.hy の循環を作らない)。
(require doeff-hy.macros [defhandler defk <- val var])
(import doeff_cluster.coordinator.protocol.request_bodies [request-bodies])
(val MODULE-TAGS {:context "coordinator" :role "main"})
(import copy)
(import doeff_core_effects.handlers [await-handler])
(import doeff_time [async-time-handler])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue queued-requests])
(import doeff_cluster.foundation.wal_store [WalStore MAX-LOG-BYTES wal-store apply-delta])
(import doeff_cluster.foundation.kube_handlers [KubeMemory kube-memory])
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox StopState http-requests stop-flag])


(defn #^ list production-handlers [#^ RequestInbox inbox #^ WalStore store #^ StopState stop #^ object kube]
  "本番の組(外側が先)。kube = kube-api か kube-unavailable(資格の有無は composition root が決める)。"
  [(await-handler) (async-time-handler) (stop-flag stop) (wal-store store) (http-requests inbox) kube request-bodies])


;; --- まねた環境 -------------------------------------------------------------------------------

(defclass MemoryWalStore [WalStore]
  "memory の置き場(WalStore と同じ口 — load-state と wal-store がそのまま使える)。deltas = Persist の列(書いた順)。
   fail-at = 失敗させる Persist の番号(1 から — fsync の失敗の注入。その番号の Persist は何も書かずに OSError)。"
  (defn #^ None __init__ [self]
    (setv self.kv {} self.seq 0 self.deltas [] self.fail-at (set) self.recovered None self.handle None
          self.fsync-seconds [] self.max-log-bytes MAX-LOG-BYTES)
    None)

  (defn #^ bool exists [self] (or (> self.seq 0) (bool self.kv)))

  (defn #^ dict load [self] self.kv)

  (defn #^ None persist [self #^ dict delta]
    (when (not delta) (return None))
    (when (in (+ self.seq 1) self.fail-at)
      (.discard self.fail-at (+ self.seq 1))
      (raise (OSError (.format "memory の置き場: {} 番目の Persist を失敗させた(注入)" (+ self.seq 1)))))
    (+= self.seq 1)
    (setv copied (copy.deepcopy delta))
    (.append self.deltas copied)
    (apply-delta self.kv (copy.deepcopy copied))
    None)

  (defn #^ None checkpoint [self] None))


(defn #^ list emulated-handlers [#^ RequestQueue queue #^ MemoryWalStore store #^ StopState stop #^ KubeMemory kube]
  "まねた環境の組(外側が先)。時計は持たない — 外側の sim の時計(sim-time-handler か async-time-handler)が答える。stop = 停止の合図
   (coordinator_inbox.StopState)。"
  [(stop-flag stop) (wal-store store) (queued-requests queue) (kube-memory kube) request-bodies])
