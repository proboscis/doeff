;;; worker の Pod の drain の入口(composition root・2026-09-25)。Program は drain_client.hy。
;;;
;;;   hy -m doeff_cluster.drain_main drain --coordinator URL --name NAME [--deadline 90] [--interval 2]
;;;       preStop: drain を頼み、自分の上の job が他へ移るか上限まで待つ。いつも 0 で終わる(結末は stderr の 1 行)。
;;;   hy -m doeff_cluster.drain_main ready --coordinator URL --name NAME
;;;       readinessProbe: coordinator から見て生きていて drain 中でなければ 0、それ以外は 1。
(require doeff-hy.macros [defhandler])
(import argparse)
(import json)
(import sys)
(import pathlib [Path])
(import httpx)
(import doeff [run])
(import doeff_time [sync-time-handler])
(import .coordinator_http [CoordinatorEndpoint])
(import .drain_client [CoordinatorCall await-drained worker-ready DRAIN-DEADLINE-SECONDS DRAIN-INTERVAL-SECONDS])

;; 要求 1 つの返事を待つ上限(秒)。coordinator の fsync の詰まり(最長 13 秒 — coordinator_http.REPLY-SECONDS)より短くはしない。
;; readinessProbe は timeoutSeconds の内で終わるよう短くする。
(setv CALL-SECONDS 15.0 PROBE-CALL-SECONDS 5.0)


(defn #^ dict call [#^ CoordinatorEndpoint endpoint #^ str method #^ str path #^ (| dict None) body]
  (try
    (setv response (if (is body None)
                       (.request endpoint method path)
                       (.request endpoint method path :json body)))
    (setv parsed (try (.json response) (except [ValueError] {})))
    {"status" response.status-code "body" (if (isinstance parsed dict) parsed {})}
    (except [error httpx.HTTPError]
      {"error" (repr error)})))


(defhandler coordinator-calls [#^ CoordinatorEndpoint endpoint]
  (CoordinatorCall [method path body] (resume (call endpoint method path body))))


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
  (setv endpoint (CoordinatorEndpoint args.coordinator (if (= args.mode "ready") PROBE-CALL-SECONDS CALL-SECONDS) 0
                                      :actor (.format "drain@{}" args.name)))
  (setv handlers [(coordinator-calls endpoint) (sync-time-handler)])
  (if (= args.mode "ready")
      (do (setv program (worker-ready args.name (read-boot args.boot-file)))
          (for [h handlers] (setv program (h program)))
          (sys.exit (if (run program) 0 1)))
      (do (setv program (await-drained args.name args.deadline args.interval (read-boot args.boot-file)))
          (for [h handlers] (setv program (h program)))
          (setv result (run program))
          (print (+ "drain: " (json.dumps result :ensure-ascii False)) :file sys.stderr :flush True)
          (sys.exit 0))))


(when (= __name__ "__main__")
  (main))
