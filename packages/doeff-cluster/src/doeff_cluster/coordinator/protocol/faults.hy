;;; coordinator の中の欠陥(CoordinatorFault)を 1 行出す handler — 本番の組で shared/protocol/inbox.hy の http-requests に重ねる
;;; (受付の handler は coordinator と記録の置き場が共に使うので、coordinator だけの effect はここに分けた — #2563)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(require doeff-hy.macros [defhandler])
(import sys)
(import doeff_cluster.coordinator.intent.cluster_model [CoordinatorFault])


(defhandler coordinator-faults
  ;; coordinator の中の欠陥を 1 行出す(送り手には 500 — 中の欠陥が送り手の誤りに見えないように・#1024)。
  (CoordinatorFault [fault]
    (print (.format "coordinator: 中の欠陥: {} {}: {}: {}({})" fault.method fault.path fault.error-type fault.message fault.where)
           :file sys.stderr :flush True)
    (resume None)))
