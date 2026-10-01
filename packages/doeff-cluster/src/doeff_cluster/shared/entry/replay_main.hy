;;; 再生の道具の入口(ADR-DOE-CLUSTER-001 R5・R5b)。業務の側の backtest の道具が起こす。
;;;
;;;   hy -m doeff_cluster.shared.entry.replay_main --recording FILE --program FILE [--from-ms N] [--to-ms N] --out FILE
;;;
;;; --program = 記録した job の詰めた Program の file(coordinator の /programs/<sha> から取った JSON {"blob" "versions"} — 記録の
;;; header の program が同じキー)。版を検めて Program を解き、再生の mode で走らせるだけ: Program の中の境目の記録係
;;; (record_handlers.boundary-recorder)が、Ask RECORD-MODE-KEY に replay と答えられると effect-replayer を置き、その状態は
;;; Ask REPLAY-STATE-KEY の答え(この道具が記録から作った ReplayState)。この 2 つの Ask にだけ、この道具が Program の外から答える
;;; (Program の土台は、再生の process の環境にこの 2 つが無いので答えずに外へ通す)。業務の effect は境目で記録係が答えるので、
;;; 土台の本物の handler には届かない。記録係より内側の handler は決定的でなければならない(R5b — 破れは再生の分岐として出る)。
;;;
;;; 詰めた Program を運ぶので、記録を再生できるのは同じ版(Python・cloudpickle・doeff)と、記録した commit の code が揃う間だけ
;;; (改訂 1 の O — R3b の代償)。版が違えば理由つきで止まる。結果(一致・判断の違い・分岐)を JSON で書く。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import argparse)
(import json)
(import sys)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [reader])
(import doeff_cluster.shared.core.record_model [read-recording ReplayFinished ReplayDiverged])
(import doeff_cluster.shared.protocol.record_handlers [ReplayState replay-report RECORD-MODE-KEY REPLAY-STATE-KEY])
(import doeff_cluster.worker.entry.job_entry [read-program])


(defn #^ list read-lines [#^ str path]  ; defk にできない: 道具の入口(Program の外)が file を読む
  "記録の file(JSON の行)を読む。"
  (with [h (open path "r" :encoding "utf-8")]
    (lfor line h :if (.strip line) (json.loads line))))


(defn #^ None main []  ; defk にできない: 道具の入口
  "記録と Program を読み、再生の mode で走らせて、再生の報告を書く。"
  (setv parser (argparse.ArgumentParser :description "記録の上で job の Program を再生する"))
  (.add-argument parser "--recording" :required True)
  (.add-argument parser "--program" :required True :help "記録した job の詰めた Program の file(/programs/<sha> の JSON)")
  (.add-argument parser "--from-ms" :type int :default None)
  (.add-argument parser "--to-ms" :type int :default None)
  (.add-argument parser "--out" :required True)
  (.add-argument parser "--config" :default None :help "受け付けない(旧い形 — 設定は Program の中の Ask で読む)")
  (setv args (.parse-args parser))
  (when (is-not args.config None)
    (.error parser "--config は受け付けない — 再生は記録した Program をそのまま走らせる(設定は Program の中の Ask)"))
  (setv rec (read-recording (read-lines args.recording) :until-ms args.to-ms))
  ;; env のキーは空(再生の道具は実行環境の job ではない)。断り(VersionMismatch / RemoteJobFailed)は例外の値なので文へ整える。
  (setv #(program problem) (read-program args.program ""))
  (when (is-not problem None)
    (print (.format "replay: {}" problem) :file sys.stderr :flush True)
    (sys.exit 3))
  (setv state (ReplayState rec :from-ms args.from-ms :to-ms args.to-ms))
  (setv started (time.monotonic) end "program-returned" failure None)
  (try
    (run (with_handlers [(reader {RECORD-MODE-KEY "replay" REPLAY-STATE-KEY state})] program))
    (except [e ReplayFinished] (setv end "finished"))
    (except [e ReplayDiverged] (setv end "diverged"))
    (except [e Exception]
      (setv end (if (is-not state.divergence None) "diverged" (if state.finished "finished" "program-failed"))
            failure (.format "{}: {}" (. (type e) __name__) (cut (str e) 0 500)))))
  (setv report (| (replay-report state end)
                  {"seconds" (round (- (time.monotonic) started) 3) "failure" failure
                   "program" (.get rec.header "program")}))
  (with [h (open args.out "w" :encoding "utf-8")]
    (json.dump report h :ensure-ascii False :default str))
  (print (.format "replay: {}・出来事 {} のうち {}・判断の違い {}・分岐 {}" end (get report "events") (get report "consumed")
                  (get report "decisionDiffCounts") (if (get report "divergence") "あり" "なし"))
         :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
