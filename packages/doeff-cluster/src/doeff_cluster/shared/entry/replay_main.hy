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
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import argparse)
(import json)
(import sys)
(import time)
(import doeff [Program run with_handlers])
(import doeff_core_effects.handlers [reader])
(import doeff_cluster.foundation.record_log [Recording read-recording ReplayFinished ReplayDiverged])
(import doeff_cluster.foundation.record_handlers [ReplayState replay-report RECORD-MODE-KEY REPLAY-STATE-KEY])
(import doeff_cluster.worker.entry.job_entry [read-program])


(defn #^ list read-lines [#^ str path]  ; defk にできない: 道具の入口(Program の外)が file を読む
  "記録の file(JSON の行)を読む。"
  (with [h (open path "r" :encoding "utf-8")]
    (lfor line h :if (.strip line) (json.loads line))))


(defk replayed [#^ Recording rec #^ Program program #^ argparse.Namespace args]
  {:pre [(: rec Recording) (: program Program) (: args argparse.Namespace)] :post [(: % int)] :tags {:context "doeff-cluster" :role "main" :spells "json"}}
  "読んだ記録の上で解いた Program を再生の mode で走らせ、再生の報告を --out へ書くため。答え = process の終わりの code(0)。"
  (val state (ReplayState rec :from-ms args.from-ms :to-ms args.to-ms))
  (val started (time.monotonic))
  ;; 終わり方 = #(end failure): 値で終わった・記録の終わりに着いた・記録と食い違った・それ以外の例外(業務コードが記録の終わりや
  ;; 分岐の例外を捕まえて別の例外に変えた時も、再生の状態から終わり方を読む)。failure = 例外の型と文(500 字まで)。
  (var ending #("program-returned" None))
  (try
    (<- (with_handlers [(reader {RECORD-MODE-KEY "replay" REPLAY-STATE-KEY state})] program))
    (except [ReplayFinished] (:= ending #("finished" None)))
    (except [ReplayDiverged] (:= ending #("diverged" None)))
    (except [error Exception]
      (:= ending #((cond (is-not state.divergence None) "diverged" state.finished "finished" True "program-failed")
                   (.format "{}: {}" (. (type error) __name__) (cut (str error) 0 500))))))
  (<- replay dict (replay-report state (get ending 0)))
  (val report (| replay {"seconds" (round (- (time.monotonic) started) 3) "failure" (get ending 1) "program" rec.header.program}))
  (with [h (open args.out "w" :encoding "utf-8")]
    (json.dump report h :ensure-ascii False :default str))
  (print (.format "replay: {}・出来事 {} のうち {}・判断の違い {}・分岐 {}" (get ending 0) (get report "events") (get report "consumed")
                  (get report "decisionDiffCounts") (if (get report "divergence") "あり" "なし"))
         :file sys.stderr :flush True)
  0)


(defk replay [#^ argparse.Namespace args]
  {:pre [(: args argparse.Namespace)] :post [(: % int)] :tags {:context "doeff-cluster" :role "main" :spells "json"}}
  "再生の道具の入口の Program: 記録と記録した Program を読み、Program を解けたら再生して報告を書くため。答え = process の終わりの
   code(0 = 報告を書いた・3 = Program を解けない — 版の違いなど)。"
  (<- rec Recording (read-recording (read-lines args.recording) :until-ms args.to-ms))
  ;; env のキーは空(再生の道具は実行環境の job ではない)。断り(VersionMismatch / RemoteJobFailed)は例外の値なので文へ整える。
  (val loaded (read-program args.program ""))
  (if (is-not (get loaded 1) None)
      (do (print (.format "replay: {}" (get loaded 1)) :file sys.stderr :flush True)
          3)
      (! (replayed rec (get loaded 0) args))))


(deff main []  ; defk にできない: console script の main(`hy -m doeff_cluster.shared.entry.replay_main` の __main__ が素の関数として呼ぶ)
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster" :role "main" :spells "json"}}
  "再生の道具の引数を読み、入口の Program(replay)を 1 度走らせて、その code で終わるため。"
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
  (setv code (run (replay args)))
  (when (!= code 0)
    (sys.exit code)))


(when (= __name__ "__main__")
  (main))
