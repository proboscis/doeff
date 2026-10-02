;;; worker の Pod の drain の入口(composition root・2026-09-25)。Program は drain_client.hy。
;;;
;;;   hy -m doeff_cluster.worker.entry.drain_main drain --coordinator URL --name NAME [--deadline 90] [--interval 2]
;;;       preStop: drain を頼み、自分の上の job が他へ移るか上限まで待つ。いつも 0 で終わる(結末は stderr の 1 行)。
;;;   hy -m doeff_cluster.worker.entry.drain_main ready --coordinator URL --name NAME
;;;       readinessProbe: coordinator から見て生きていて drain 中でなければ 0、それ以外は 1。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "worker" :role "main"})
(import argparse)
(import json)
(import sys)
(import time)
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_cluster.foundation.coordinator_http [CONNECT-SECONDS PREFERRED-RECHECK-SECONDS RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions route-of])
(import doeff_cluster.worker.core.drain_client [await-drained worker-ready DRAIN-DEADLINE-SECONDS DRAIN-INTERVAL-SECONDS])
(import doeff_cluster.worker.protocol.drain_requests [coordinator-calls])

;; 要求 1 つの返事を待つ上限(秒)。coordinator の fsync の詰まり(最長 13 秒 — coordinator_http.REPLY-SECONDS)より短くはしない。
;; readinessProbe は timeoutSeconds の内で終わるよう短くする。
(setv CALL-SECONDS 15.0 PROBE-CALL-SECONDS 5.0)


(defn #^ (| str None) read-boot [#^ (| str None) path]
  "この Pod の worker の世代(file の 1 行)。path が無い・file が無い・読めない = None。"
  (when (not path) (return None))
  (try
    (setv text (.strip (.read-text (Path path) :encoding "utf-8")))
    (if text text None)
    (except [OSError] None)))


(defn #^ None main []
  (setv parser (argparse.ArgumentParser :description "worker の Pod の drain(preStop)と readinessProbe"))
  (.add-argument parser "mode" :choices ["drain" "ready"])
  (.add-argument parser "--coordinator" :required True :help "URL を `,` で並べると前から順に試す")
  (.add-argument parser "--name" :required True :help "worker の名前(= node の名前)")
  (.add-argument parser "--deadline" :type float :default DRAIN-DEADLINE-SECONDS)
  (.add-argument parser "--interval" :type float :default DRAIN-INTERVAL-SECONDS)
  (.add-argument parser "--boot-file" :default None
                 :help (+ "この Pod の worker が起動の時に世代を書く file。ready: 無い・読めない = まだ起動していない = Ready でない。"
                          "drain: 頼みに世代を載せる(同じ名の新しい Pod の worker が名乗った後は、この世代の task だけを待つ)"))
  (setv args (.parse-args parser))
  (setv cell (RouteCell (run (route-of args.coordinator (int (* 1000 (time.time))))))
        options (RouteOptions :reply-seconds (if (= args.mode "ready") PROBE-CALL-SECONDS CALL-SECONDS) :connect-seconds CONNECT-SECONDS :resend-deadline-seconds IDEMPOTENT-DEADLINE-SECONDS :resend-pause-seconds RESEND-PAUSE-SECONDS
                              :connect-retries 0 :recheck-ms (int (* PREFERRED-RECHECK-SECONDS 1000)) :actor (.format "drain@{}" args.name)))
  (defn #^ (| dict bool) on-coordinator [#^ object program]
    ;; 並びは外側から: 待ち・本物の HTTP の答え手・時計・coordinator への口(drain の頼みの言い換えも持つ)。
    (run (scheduled (with-handlers [(await-handler) (http-production-handler) (sync-time-handler) (coordinator-calls cell options)] program))))
  (if (= args.mode "ready")
      (sys.exit (if (on-coordinator (worker-ready args.name (read-boot args.boot-file))) 0 1))
      (do (setv result (on-coordinator (await-drained args.name args.deadline args.interval (read-boot args.boot-file))))
          (print (+ "drain: " (json.dumps result :ensure-ascii False)) :file sys.stderr :flush True)
          (sys.exit 0))))


(when (= __name__ "__main__")
  (main))
