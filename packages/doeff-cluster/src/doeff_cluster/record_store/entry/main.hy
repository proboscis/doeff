;;; effect の記録の置き場の入口(composition root)。hy -m doeff_cluster.record_store.entry.main --root DIR [--port 8080] [--retention-days 30]
;;; 入口の組み立ては 2 つに分ける: handler の組を選ぶ(handler_sets の production-handlers / emulated-handlers)と、
;;; その組の上で置き場の Program を回す(record-store-on)。模擬の環境は同じ record-store-on を emulated-handlers の上で回す。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "record-store" :role "main"})
(import argparse)
(import signal)
(import sys)
(import types [FrameType])
(import doeff [run with-handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.foundation.coordinator_inbox [StopState])
(import doeff_cluster.record_store.core.program [store-loop])
(import doeff_cluster.foundation.record_inbox [RecordInbox])
(import doeff_cluster.record_store.entry.handler_sets [production-handlers])


(defk record-store-on [handlers retention-ms idle-ms]
  {:pre [(: handlers list) (: retention-ms int) (: idle-ms int)] :post [(: % int)]}
  "handler の組 handlers(外側が先)の上で置き場の Program(store-loop)を回すため — 停止の合図で抜け、答えた要求の数を返す。
   組を選ぶのは composition root(本番 = main の production-handlers・模擬の環境 = emulated-handlers)。"
  (<- served int (with-handlers handlers (store-loop retention-ms idle-ms)))
  served)


(defn #^ None main []
  (setv parser (argparse.ArgumentParser :description "effect の記録の置き場"))
  (.add-argument parser "--root" :required True)
  (.add-argument parser "--port" :type int :default 8080)
  (.add-argument parser "--retention-days" :type float :default 30.0)
  (.add-argument parser "--idle-seconds" :type float :default 900.0)
  (setv args (.parse-args parser))
  (setv stop (StopState))
  (defn #^ None on-signal [#^ int signum #^ (| FrameType None) frame] (setv stop.requested True))
  (signal.signal signal.SIGTERM on-signal)
  (signal.signal signal.SIGINT on-signal)
  (setv inbox (RecordInbox args.port))
  (.start inbox)
  (print (.format "records: :{} で受けます(置き場 {}・保持 {} 日)" args.port args.root args.retention-days) :file sys.stderr :flush True)
  ;; handler の組を選び(本番の組)、その組の上で置き場の Program を回す。
  (setv handlers (run (production-handlers args.root inbox stop)))
  (run (scheduled (record-store-on handlers (int (* args.retention-days 86400000)) (int (* args.idle-seconds 1000)))))
  (print "records: 止まりました" :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
