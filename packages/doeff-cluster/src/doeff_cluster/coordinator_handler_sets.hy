;;; coordinator の handler の組 — 環境の違いは、ここに並ぶ「handler の組」という値だけで表す(operator 2026-09-25 逐語
;;; "such environmental changes are to be all done via handler swapping")。調停ループの Program(coordinator.run-coordinator)は
;;; 環境を知らない。
;;;
;;;   production-handlers  本番: HTTP の受付(RequestInbox — 別 thread の HTTP server)・追記の log の置き場(WalStore — file と fsync)・
;;;                        k8s の API(ServiceAccount の token が在る時)・registry の HTTP API・doeff-time の async-time-handler。
;;;   emulated-handlers    手元のまねた環境(業務の側の模擬環境): 要求は process の中の列(RequestQueue — 模擬の
;;;                        worker・client が並べ、返事は promise で受ける)・置き場は memory(MemoryWalStore — 再起動の模擬は同じ
;;;                        置き場から load-state で読み直す)・k8s と registry は memory の偽物。時計(GetTime / Delay)は組の外側の
;;;                        仮想の時計(doeff-time の sim-time-handler)が答えるので、この組は時計を持たない。
;;;
;;; 組は with_handlers に渡す list(外側が先)。選ぶのは composition root(coordinator.main・業務の側の模擬環境)だけ。
;;; 本番の受付の handler は coordinator_inbox.hy(coordinator.hy から分けた — この module と coordinator.hy の循環を作らない)。
(require doeff-hy.macros [defhandler <-])
(import copy)
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [CompletePromise])
(import doeff_time [Delay async-time-handler])
(import .cluster_model [NextRequests Reply])
(import .wal_store [WalStore MAX-LOG-BYTES wal-store apply-delta])
(import .kube_handlers [KubeMemory kube-memory])
(import .image_handlers [RegistryClient registry-http image-memory])
(import .coordinator_inbox [RequestInbox StopState http-requests stop-flag])

;; 列が空の間、NextRequests が列を見直す間隔(秒)。本番の受付は要求が届いた瞬間に起きるので、模擬の遅れはこの間隔まで。
(setv QUEUE-POLL-SECONDS 0.05)


(defn #^ list production-handlers [#^ RequestInbox inbox #^ WalStore store #^ StopState stop #^ object kube]
  "本番の組(外側が先)。kube = kube-api か kube-unavailable(資格の有無は composition root が決める)。"
  [(await-handler) (async-time-handler) (stop-flag stop) (wal-store store) (http-requests inbox)
   ;; image の LABEL(土台の版の追随)は registry の HTTP API を読むだけ。新しい image の時だけ・3 秒で打ち切る。
   (registry-http (RegistryClient :timeout 3.0)) kube])


;; --- まねた環境 -------------------------------------------------------------------------------

(defclass RequestQueue []
  "process の中の要求の列(HTTP の受付の代わり)。送り手は Request の slot に doeff の Promise を入れて並べ、Wait で返事
   #(status 本文)を受ける。up = 受け付けているか(coordinator の process が止まっている間は偽 — 送り手は接続の失敗として扱う)。"
  (defn #^ None __init__ [self]
    (setv self.pending [] self.up False)
    None))


(defhandler queued-requests [#^ RequestQueue queue]
  ;; 本番の http-requests と同じ意味: 最初の 1 件を timeout 秒まで待ち、その時点で並んでいる要求を limit 件まで一緒に取る。
  (NextRequests [timeout-seconds limit]
    (setv waited 0.0)
    (while (and (not queue.pending) (< waited timeout-seconds))
      (<- (Delay QUEUE-POLL-SECONDS))
      (+= waited QUEUE-POLL-SECONDS))
    (setv batch (cut queue.pending 0 limit))
    (setv queue.pending (cut queue.pending limit None))
    (resume batch))
  (Reply [request status body]
    (<- (CompletePromise request.slot #(status body)))
    (resume None)))


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


(defn #^ list emulated-handlers [#^ RequestQueue queue #^ MemoryWalStore store #^ StopState stop #^ KubeMemory kube #^ dict images]
  "まねた環境の組(外側が先)。時計は持たない — 外側の仮想の時計(sim-time-handler)が答える。stop = 停止の合図(coordinator_inbox.StopState)。
   images = image → LABEL の dict(registry の偽物)。"
  [(stop-flag stop) (wal-store store) (queued-requests queue) (image-memory images) (kube-memory kube)])
