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
;;; 本番の受付の handler は shared/protocol/inbox.hy(箱は foundation/coordinator_inbox.hy・CoordinatorFault は coordinator/protocol/faults.hy — coordinator.hy から分けた — この module と coordinator.hy の循環を作らない)。
(require doeff-hy.macros [defhandler defk <- val var])
(import doeff_cluster.coordinator.protocol.request_bodies [request-bodies])
(val MODULE-TAGS {:context "coordinator" :role "main"})
(import copy)
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_time [async-time-handler])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue queued-requests])
(import doeff_cluster.foundation.wal_store [WalStore] doeff_cluster.coordinator.protocol.store [durable-states wal-store])
(import doeff_cluster.coordinator.protocol.replies [reply-bodies])
(import doeff_cluster.coordinator.protocol.kube [KubeMemory MemoryFollows kube-memory])
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox StopState] doeff_cluster.shared.protocol.inbox [http-requests stop-flag] doeff_cluster.coordinator.protocol.faults [coordinator-faults])
(import doeff_events [MemoryBroker broker-back-by-retry memory-notice-handler notice-events-handler redis-notice-handler])
(import doeff_cluster.coordinator.protocol.worker_notices [WORKER-NOTICE-ROUTES])

;; coordinator が出来事(worker の生死 — #3864)を出す包みの源の名。出すだけ(channel を読まない)なので、broker の戻りを待つ上限
;; NOTICE-PATIENCE-SECONDS は使われない(doeff-events の notice-events-handler が引数として求める — 既定値を持たない)。
(val NOTICE-SOURCE "doeff-cluster-coordinator")
(val NOTICE-PATIENCE-SECONDS 60.0)


(defn #^ list redis-notices [#^ str url #^ float timeout-seconds #^ float retry-seconds]
  "本番の知らせの組(外側が先): Redis(url)へ出し(繋ぐ・送るの答えは timeout-seconds まで待つ — 答えない Redis が coordinator を
   止めない)、届かない間は欠けの印を持ち、戻りは待っている間だけ retry-seconds ごとに繋がるかを試して知る(ADR-DOE-EVENTS-002 R5・R6)。
   await-handler と時計(Delay)は組の外側の本番の組が答える。"
  [(redis-notice-handler url timeout-seconds) (broker-back-by-retry retry-seconds)
   (notice-events-handler NOTICE-SOURCE WORKER-NOTICE-ROUTES NOTICE-PATIENCE-SECONDS)])


(defn #^ list memory-notices [#^ MemoryBroker broker]
  "process の中の知らせの組(外側が先): 同じ broker を持つ受け手へ出す(まねた環境・Redis の無い機体の本物の process のテスト)。"
  [(memory-notice-handler broker) (notice-events-handler NOTICE-SOURCE WORKER-NOTICE-ROUTES NOTICE-PATIENCE-SECONDS)])


(defn #^ list production-handlers [#^ RequestInbox inbox #^ WalStore store #^ StopState stop #^ object kube #^ list notices]
  "本番の組(外側が先)。kube = kube-api か kube-unavailable(資格の有無は composition root が決める)。notices = 知らせの組
   (redis-notices か memory-notices — composition root が起動の引数で選ぶ・既定なし)。slog-handler = 調停ループの 1 行の
   報告(k8s の読みが答えない時の名指し — #2807)を stderr へ出す。"
  [slog-handler (await-handler) (async-time-handler) (stop-flag stop) (wal-store store) (http-requests inbox) coordinator-faults kube request-bodies
   durable-states reply-bodies #* notices])


;; --- まねた環境 -------------------------------------------------------------------------------

(defclass MemoryWalStore []
  "memory の置き場 — 差分の口の置き場(protocol/store の DeltaStore の形)なので、load-state と wal-store が置き場の口
   (durable-load・durable-persist ほか)を通してそのまま使える。file を持たないので行と写しの形を綴らない(#2785)。
   kv = 耐久になった全部のキー・deltas = Persist の列(書いた順)・recovered = 読み直しで捨てた行の記録(memory では捨てないので None)。
   fail-at = 失敗させる Persist の番号(1 から — fsync の失敗の注入。その番号の Persist は何も書かずに OSError)。"
  (defn #^ None __init__ [self]
    (setv self.kv {} self.seq 0 self.deltas [] self.fail-at (set) self.recovered None)
    None)

  (defn #^ bool exists [self] (or (> self.seq 0) (bool self.kv)))

  (defn #^ dict load [self] self.kv)

  ;; 置き場の口が表と読み直しの記録を受け渡す method(protocol/store の DeltaStore の形 — 写像を欄に持たない・DOEFF172)。
  (defn #^ (get dict #(str object)) table [self] self.kv)

  (defn #^ None replace-table [self #^ (get dict #(str object)) kv] (setv self.kv kv))

  (defn #^ (| (get dict #(str (| int str))) None) recovery [self] self.recovered)

  (defn #^ None persist [self #^ (get dict #(str object)) delta]
    (when (not delta) (return None))
    (when (in (+ self.seq 1) self.fail-at)
      (.discard self.fail-at (+ self.seq 1))
      (raise (OSError (.format "memory の置き場: {} 番目の Persist を失敗させた(注入)" (+ self.seq 1)))))
    (+= self.seq 1)
    (setv copied (copy.deepcopy delta))
    (.append self.deltas copied)
    ;; 当て方は file の置き場と同じ(消えたキーは None — protocol/wal_format の apply-delta)。この method は Program の外なので
    ;; defk を呼ばずに同じ 2 通りをここで当てる。
    (for [#(k v) (.items (copy.deepcopy copied))]
      (if (is v None) (.pop self.kv k None) (setv (get self.kv k) v)))
    None)

  (defn #^ None checkpoint [self] None))


(defn #^ list emulated-handlers [#^ RequestQueue queue #^ MemoryWalStore store #^ StopState stop #^ KubeMemory kube #^ MemoryBroker broker #^ list [watchers []]]
  "まねた環境の組(外側が先)。時計は持たない — 外側の sim の時計(sim-time-handler か async-time-handler)が答える。stop = 停止の合図
   (coordinator_inbox.StopState)。watchers = 置き場への書き(Persist)と要求の受け渡しを見張る handler の列(sim の落ちの注入と呼び鈴 —
   本番の組と同じく保存の綴り durable-states をいちばん内側に置くので、見張りはその外で KV の差分を見る)。slog-handler = 本番と同じ
   1 行の報告の答え手。broker = 知らせの broker(worker の生死の出来事を出す — 模擬の受け手が同じ broker を読む)。Deployment の見張り
   (MemoryFollows)は coordinator の process の物なので、組を作るたび(coordinator の起き直しごと)に作り直す(#3868)。"
  [slog-handler (stop-flag stop) (wal-store store) (queued-requests queue) (kube-memory kube (MemoryFollows)) request-bodies #* watchers
   durable-states reply-bodies
   #* (memory-notices broker)])
