;;; record-store の handler の組 — 環境の違いは、ここに並ぶ「handler の組」という値だけで表す(coordinator の
;;; coordinator/entry/handler_sets.hy と同じ形)。置き場の Program(record_store.core.program の store-loop)は環境を
;;; 知らない。
;;;
;;;   production-handlers  本番: HTTP の受付(RecordInbox — 別 thread の HTTP server)・本物の file system(os-file-handler)・
;;;                        doeff-time の async-time-handler。
;;;   emulated-handlers    手元のまねた環境: 要求は process の中の箱(ScriptedRecordInbox — 先に並べた生の要求を渡し、並べた物を渡し
;;;                        終えたら停止の合図を立てる)・file system は memory(memory-file-handler)。受付の言い換え(http-requests)・
;;;                        停止の合図(stop-flag)・置き場の言い換え(record-files)は本番と同じ handler。時計(GetTime)は組の外側の
;;;                        sim の時計が答えるので、この組は時計を持たない。
;;;
;;; 組は with-handlers に渡す list(外側が先)。選ぶのは composition root(record_store.entry.main と模擬の環境)だけ。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "record-store" :role "main"})
(import doeff_core_effects.handlers [await-handler slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_time [async-time-handler])
(import doeff_cluster.shared.protocol.inbox [http-requests stop-flag])
(import doeff_cluster.foundation.coordinator_inbox [StopState])
(import doeff_cluster.foundation.record_inbox [RecordInbox])
(import doeff_cluster.record_store.protocol.record_files [record-files])


(defk production-handlers [root inbox stop]
  {:pre [(: root str) (: inbox RecordInbox) (: stop StopState)] :post [(: % list)]}
  "本番の組(外側が先)。root = 置き場の dir・inbox = 起動済みの HTTP の受付・stop = SIGTERM の handler と共有する停止の合図。"
  [(await-handler) (async-time-handler) slog-handler (stop-flag stop) (http-requests inbox) os-file-handler (record-files root)])


;; --- まねた環境 -------------------------------------------------------------------------------

(defclass ScriptedRecordInbox []
  "まねた受付の箱(RecordInbox と同じ take の口 — 本番の http-requests がそのまま読む)。pending = 先に並べた生の要求(RawRequest の
   list — 返事は各要求の ReplySlot に置かれる)。並べた物を渡し終えた後の take で停止の合図 stop を立てる(置き場の Program は次の拍の
   頭で止まる)。待たない(要求が無ければすぐ空を返す — 時計は組の外側の sim の時計)。"
  (defn #^ None __init__ [self #^ list pending #^ StopState stop]
    (setv self.pending pending self.stop stop)
    None)

  (defn #^ list take [self #^ float timeout #^ int limit]
    (setv batch (cut self.pending 0 limit)
          self.pending (cut self.pending limit None))
    (when (not batch)
      (setv self.stop.requested True))
    batch))


(defk emulated-handlers [inbox stop root files]
  {:pre [(: inbox ScriptedRecordInbox) (: stop StopState) (: root str) (: files MemoryFiles)] :post [(: % list)]}
  "まねた環境の組(外側が先)。時計は持たない — 外側の sim の時計(sim-time-handler か async-time-handler)が答える。files = memory の
   置き場の初めの中身(root の dir を含める)。memory の置き場は session の値なので、その外側に state を置く。"
  [slog-handler (stop-flag stop) (http-requests inbox) (state) (memory-file-handler files) (record-files root)])
