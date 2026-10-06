;;; 偽の宿で本物の run-worker を回す検の、周の間の待ちを起こす物の答え手(#3871 の単位 4)。本番は送り手の口(coordinator_link)が
;;; heartbeat の期限を WorkerWakes に足すが、偽の宿の検は送り手の口を並べないので、その代わりに「今から every-ms 後」の期限を答える
;;; (前の形で検が WorkerPolicy の tick-seconds に渡していた刻みと同じ間で周が回る)。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "foundation"})
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.due_model [DueAt])
(import doeff_cluster.worker.intent.worker_model [WakeSet WorkerWakes])


(defhandler wakes-every [#^ int every-ms]
  ;; 引数に残す理由: 検ごとに別の間で並べる(Ask で区別できない)。
  (WorkerWakes []
    (<- now int (now-epoch-ms))
    (resume (WakeSet :due (DueAt :at (+ now every-ms)) :bells #() :exits #()))))
