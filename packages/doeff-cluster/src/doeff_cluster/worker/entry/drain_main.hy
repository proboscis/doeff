;;; worker の Pod の drain の入口(composition root・2026-09-25)。Program は drain_client.hy。
;;;
;;;   hy -m doeff_cluster.worker.entry.drain_main drain --coordinator URL --name NAME [--deadline 90] [--interval 2]
;;;       preStop: 止まりの印を世代の file の隣に書き(AL-D2p — worker/protocol/leaving_mark)、drain を頼み、自分の上の job が他へ移るか
;;;       上限まで待つ。いつも 0 で終わる(結末は stderr の 1 行)。
;;;   hy -m doeff_cluster.worker.entry.drain_main ready --coordinator URL --name NAME
;;;       readinessProbe: coordinator から見て生きていて drain 中でなければ 0、それ以外は 1。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "worker" :role "main"})
(import argparse)
(import json)
(import sys)
(import doeff_core_effects.file_effects [ReadText])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff [run with-handlers])
(import doeff_core_effects [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.foundation.coordinator_http [CONNECT-SECONDS PREFERRED-RECHECK-SECONDS RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions route-of])
(import doeff_cluster.worker.core.drain_client [await-drained worker-ready DRAIN-DEADLINE-SECONDS DRAIN-INTERVAL-SECONDS])
(import doeff_cluster.worker.protocol.drain_requests [coordinator-calls])
(import doeff_cluster.worker.protocol.leaving_mark [leaving-mark-written])

;; 要求 1 つの返事を待つ上限(秒)。coordinator の fsync の詰まり(最長 13 秒 — ClusterTiming.client-reply-ms の訳)より短くはしない。
;; readinessProbe は timeoutSeconds の内で終わるよう短くする。
(setv CALL-SECONDS 15.0 PROBE-CALL-SECONDS 5.0)


(defk read-boot [path]
  {:pre [(: path str)] :post [(: % (| str None))] :tags {:context "worker" :role "main"}}
  "この Pod の worker の世代(file の 1 行)。file が無い・読めない = None。読みは file の効果(答え手 = 入口の os-file-handler)。"
  (<- text (ReadText path))
  (val line (if (isinstance text str) (.strip text) ""))
  (if line line None))


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
  ;; 宛先の表を作る時刻は時計の effect で読む(答え手 = sync-time-handler — 入口の層で time.time を直に読まない・DOEFF106・#3014)。
  (setv cell (RouteCell (run (route-of args.coordinator (run (with-handlers [(sync-time-handler)] (now-epoch-ms))))))
        options (RouteOptions :reply-seconds (if (= args.mode "ready") PROBE-CALL-SECONDS CALL-SECONDS)
                              :watch-seconds (/ (. (ClusterTiming) watch-max-ms) 1000.0) :connect-seconds CONNECT-SECONDS :resend-deadline-seconds IDEMPOTENT-DEADLINE-SECONDS :resend-pause-seconds RESEND-PAUSE-SECONDS
                              :connect-retries 0 :recheck-ms (int (* PREFERRED-RECHECK-SECONDS 1000)) :actor (.format "drain@{}" args.name)))
  (defn #^ (| dict bool) on-coordinator [#^ object program]
    ;; 並びは外側から: 待ち・本物の HTTP の答え手・時計・coordinator への口(drain の頼みの言い換えも持つ)。
    (run (scheduled (with-handlers [await-handler (http-production-handler) (sync-time-handler) (coordinator-calls cell options)] program))))
  ;; この Pod の worker の世代は、本物の file の答え手を被せて 1 度だけ読む(--boot-file が無ければ None)。
  (setv boot (if args.boot-file (run (with-handlers [os-file-handler] (read-boot args.boot-file))) None))
  (when (and (= args.mode "drain") args.boot-file boot)
    ;; preStop の頭で、この Pod の止まりが始まった印を世代つきで書く(AL-D2p — worker の本体は印の在る拍から handoff の job の新しい試行を
    ;; 起こさない)。coordinator へ頼む前に書く — 頼みが届かない間も Pod の止まりは始まっている。書けなくても drain は続ける。
    (setv refused (run (with-handlers [os-file-handler] (leaving-mark-written args.boot-file boot))))
    (when (is-not refused None)
      (print (.format "drain: 止まりの印を書けない(worker は coordinator の印でだけ退く): {}: {}" refused.path refused.detail)
             :file sys.stderr :flush True)))
  (if (= args.mode "ready")
      (sys.exit (if (on-coordinator (worker-ready args.name boot)) 0 1))
      (do (setv result (on-coordinator (await-drained args.name args.deadline args.interval boot)))
          (print (+ "drain: " (json.dumps result :ensure-ascii False)) :file sys.stderr :flush True)
          (sys.exit 0))))


(when (= __name__ "__main__")
  (main))
