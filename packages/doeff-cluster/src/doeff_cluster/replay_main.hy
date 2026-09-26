;;; 再生の子 process の入口(composition root)。業務コードの版の木の中で動く(業務の側の backtest の道具が起こす)。
;;;
;;;   hy -m doeff_cluster.replay_main --recording FILE [--from-ms N] [--to-ms N] [--config JSON] --out FILE
;;;
;;; 記録の run の行(factory・設定)から業務の Program を作り、scheduler と再生の handler 1 つだけで走らせる(外の I/O をする handler は
;;; 1 つも組まない — 業務の Program の effect は全部、記録か本物の scheduler が答える)。結果(一致・判断の違い・分岐)を JSON で書く。
(import argparse)
(import json)
(import sys)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.service_model [resolve program-arguments RECORD-KEY])
(import doeff_cluster.record_model [read-recording ReplayFinished ReplayDiverged])
(import doeff_cluster.record_handlers [ReplayState effect-replayer replay-report])


(defn #^ list read-lines [#^ str path]
  (with [h (open path "r" :encoding "utf-8")]
    (lfor line h :if (.strip line) (json.loads line))))


(defn main []
  (setv parser (argparse.ArgumentParser :description "記録の上で業務の Program を再生する"))
  (.add-argument parser "--recording" :required True)
  (.add-argument parser "--from-ms" :type int :default None)
  (.add-argument parser "--to-ms" :type int :default None)
  (.add-argument parser "--config" :default "{}" :help "記録の設定に上書きする欄(JSON)")
  (.add-argument parser "--out" :required True)
  (setv args (.parse-args parser))
  (setv rec (read-recording (read-lines args.recording) :until-ms args.to-ms))
  (setv header rec.header)
  (setv config (| (dict (.get header "config" {})) (json.loads args.config)))
  (.pop config RECORD-KEY None)
  ;; env の module は handler を組まない(再生は記録だけが答える)が、import はする — 業務の effect の型の記録の登録
  ;; (effect_codec.register)は業務の側の env の module が import の時に足すので、読まないと業務の型が登録の無い型になる。
  (when (.get header "env")
    (resolve (get header "env")))
  (setv factory (resolve (get header "factory")))
  (setv program (factory #** (program-arguments config)))
  (setv state (ReplayState rec :from-ms args.from-ms :to-ms args.to-ms))
  (setv started (time.monotonic) end "program-returned" failure None)
  (try
    (run (scheduled (with_handlers [(effect-replayer state)] program)))
    (except [e ReplayFinished] (setv end "finished"))
    (except [e ReplayDiverged] (setv end "diverged"))
    (except [e Exception]
      (setv end (if (is-not state.divergence None) "diverged" (if state.finished "finished" "program-failed"))
            failure (.format "{}: {}" (. (type e) __name__) (cut (str e) 0 500)))))
  (setv report (| (replay-report state end)
                  {"seconds" (round (- (time.monotonic) started) 3) "failure" failure "config" config
                   "factory" (get header "factory")}))
  (with [h (open args.out "w" :encoding "utf-8")]
    (json.dump report h :ensure-ascii False :default str))
  (print (.format "replay: {}・出来事 {} のうち {}・判断の違い {}・分岐 {}" end (get report "events") (get report "consumed")
                  (get report "decisionDiffCounts") (if (get report "divergence") "あり" "なし"))
         :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
