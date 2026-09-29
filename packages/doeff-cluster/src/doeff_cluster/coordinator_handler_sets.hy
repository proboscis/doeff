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
;;; 本番の受付の handler は coordinator_inbox.hy(coordinator.hy から分けた — この module と coordinator.hy の循環を作らない)。
(require doeff-hy.macros [defhandler defk <- val])
(import copy)
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise])
(import doeff_time [async-time-handler])
(import .cluster_model [Request NextRequests Reply CoordinatorFault])
(import .wal_store [WalStore MAX-LOG-BYTES wal-store apply-delta])
(import .kube_handlers [KubeMemory kube-memory])
(import .coordinator_inbox [RequestInbox StopState http-requests stop-flag])
(import .promise_wait [promise-or-timeout])


(defn #^ list production-handlers [#^ RequestInbox inbox #^ WalStore store #^ StopState stop #^ object kube]
  "本番の組(外側が先)。kube = kube-api か kube-unavailable(資格の有無は composition root が決める)。"
  [(await-handler) (async-time-handler) (stop-flag stop) (wal-store store) (http-requests inbox) kube])


;; --- まねた環境 -------------------------------------------------------------------------------

(defclass RequestQueue []
  "process の中の要求の列(HTTP の受付の代わり)。送り手は Request の slot に doeff の Promise を入れて並べ、Wait で返事
   #(status 本文)を受ける。up = 受け付けているか(coordinator の process が止まっている間は偽 — 送り手は接続の失敗として扱う)。
   bells = 切り離した task の key → 呼び鈴(doeff の Promise)の tuple。送り手が task の終わりを読み直さずに待つため、読む前に掛ける。
   模擬の coordinator の Persist の見張り(local.hy の observe-requests)が、その key の task の終わりの phase を書いた時に鳴らす。
   takers = 列の取り手(queued-requests の NextRequests)が、列が空の間に掛けた呼び鈴(doeff の Promise の list — 掛けた順)。送り手が
   列に積んだ時(enqueue-request)に全部鳴らして外す。列は読み直さない(前は仮想の 0.05 秒ごとに見直していた — 使い手の仮想の
   1700 秒の検で 37,222 回眠り、所要の大半になった)。
   faults = coordinator の中の欠陥の log の行(CoordinatorFault の Fault — 出た順)。本番の受付が stderr へ出す 1 行の代わり。"
  (defn #^ None __init__ [self]
    (setv self.pending [] self.up False self.bells {} self.takers [] self.faults [])
    None))


(defk enqueue-request [queue request]
  {:pre [(: queue RequestQueue) (: request Request)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "要求を列の後ろに積み、列が空の間に待っていた取り手の呼び鈴を全部鳴らして外すため(取り手は積んだのと同じ仮想の刻で起きる)。
   積む順 = 取る順(列は先頭から取る)。鳴らすのは積んだ後 — 起きた取り手は必ず積んだ要求を見る。"
  (.append queue.pending request)
  (val waiting (tuple queue.takers))
  (.clear queue.takers)
  (for [bell waiting]
    (<- (CompletePromise bell True)))
  None)


(defk await-first-request [queue timeout-seconds]
  {:pre [(: queue RequestQueue) (: timeout-seconds (| float int))] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "列が空なら、送り手が積む(enqueue-request が呼び鈴を鳴らす)か timeout 秒が過ぎるまで 1 回だけ眠るため(読み直さない)。列に何か
   在れば眠らない。起きた時(時間切れ・取り消しを含む)は自分の呼び鈴を取り手の list から外す。鳴らすのは積む時だけなので、鳴って
   起きた時の列は空でない(時間切れで起きた時だけ空のまま)。"
  (when (and (not queue.pending) (> timeout-seconds 0))
    (<- bell Promise (CreatePromise))
    (.append queue.takers bell)
    (try
      (<- (promise-or-timeout bell.future timeout-seconds))
      (finally
        (when (in bell queue.takers)
          (.remove queue.takers bell)))))
  None)


(defhandler queued-requests [#^ RequestQueue queue]
  ;; 本番の http-requests と同じ意味: 最初の 1 件を timeout 秒まで待ち、その時点で並んでいる要求を limit 件まで一緒に取る。待ちは列への
  ;; 書き(enqueue-request)で起きる — 本番の受付が要求の届いた瞬間に起きるのと同じ刻。
  (NextRequests [timeout-seconds limit]
    (<- (await-first-request queue timeout-seconds))
    (setv batch (cut queue.pending 0 limit))
    (setv queue.pending (cut queue.pending limit None))
    (resume batch))
  (Reply [request status body]
    (<- (CompletePromise request.slot #(status body)))
    (resume None))
  (CoordinatorFault [fault]
    (.append queue.faults fault)
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


(defn #^ list emulated-handlers [#^ RequestQueue queue #^ MemoryWalStore store #^ StopState stop #^ KubeMemory kube]
  "まねた環境の組(外側が先)。時計は持たない — 外側の sim の時計(sim-time-handler か async-time-handler)が答える。stop = 停止の合図
   (coordinator_inbox.StopState)。"
  [(stop-flag stop) (wal-store store) (queued-requests queue) (kube-memory kube)])
