;;; /proc の無い機体の測りの問い ReadHostCounters(worker/intent/worker_model)の本番の答え手 os-host-counters。入口 main が本番の組に
;;; 並べ、coordinator への口(worker/protocol/coordinator_link の load-noted)が印 HostLoad の worker で LOAD-READ-EVERY-MS ごとに問う。
;;;
;;; 生の OS の呼びは foundation/host_counters(mach の host_statistics・sysctl)が持ち、ここはその答えの dict を intent の型 HostCounters に
;;; 写すだけ(層 foundation は intent の型を読まない — worker/entry/retirement_notices と同じ分け方)。読めない機体の答えは訳の str のまま
;;; 返す(口が測りを名乗らない訳として log に出す)。
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "worker" :role "main"})
(import doeff_cluster._foundation.host_counters [read-host-counters])
(import doeff_cluster._worker.intent.worker_model [HostCounters ReadHostCounters])


(defhandler os-host-counters
  {:needs #{} :tags {:context "worker" :role "main"}}
  ;; この機体の OS の口から答える実 I/O の答え手(頭の註)。
  (ReadHostCounters []
    (val read (read-host-counters))
    (resume (if (isinstance read str) read (HostCounters #** read)))))
