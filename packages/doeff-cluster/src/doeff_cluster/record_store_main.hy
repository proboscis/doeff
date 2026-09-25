;;; effect の記録の置き場の入口(composition root)。hy -m doeff_cluster.record_store_main --root DIR [--port 8080] [--retention-days 30]
(import argparse)
(import signal)
(import sys)
(import doeff [run])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [async-time-handler])
(import doeff_cluster.coordinator [http-requests stop-flag StopState])
(import doeff_cluster.record_store [store-loop])
(import doeff_cluster.record_store_handlers [record-files RecordInbox])


(defn main []
  (setv parser (argparse.ArgumentParser :description "effect の記録の置き場"))
  (.add-argument parser "--root" :required True)
  (.add-argument parser "--port" :type int :default 8080)
  (.add-argument parser "--retention-days" :type float :default 30.0)
  (.add-argument parser "--idle-seconds" :type float :default 900.0)
  (setv args (.parse-args parser))
  (setv stop (StopState))
  (defn on-signal [signum frame] (setv stop.requested True))
  (signal.signal signal.SIGTERM on-signal)
  (signal.signal signal.SIGINT on-signal)
  (setv inbox (RecordInbox args.port))
  (.start inbox)
  (print (.format "records: :{} で受けます(置き場 {}・保持 {} 日)" args.port args.root args.retention-days) :file sys.stderr :flush True)
  (setv program (store-loop (int (* args.retention-days 86400000)) (int (* args.idle-seconds 1000))))
  (for [h [(record-files args.root) (http-requests inbox) (stop-flag stop) (async-time-handler) (await-handler)]]
    (setv program (h program)))
  (run (scheduled program))
  (print "records: 止まりました" :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
