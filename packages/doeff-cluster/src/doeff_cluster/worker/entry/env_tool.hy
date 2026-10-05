;;; 実行環境(root)1 つの準備の process の入口(#2028 — env_handlers.hy の main をここへ移した。翻訳 env-translation は
;;; worker/protocol/env_translation)。worker(worker/protocol/env_store の env-host)はこの入口を worker 自身の環境の別の process として起こす。
;;; 送る名 ENV-TOOL はこの入口の名(旧い path の doeff_cluster.env_handlers は #2113 で消した — 同じ commit の worker が送る名と揃う):
;;;
;;;   hy -m doeff_cluster.worker.entry.env_tool --request <要求の JSON> --result <答えの JSON> --state <state dir>
;;;      --repo-keys <鍵の表の JSON> --code-prepare <worker の code_prepare.hy> [--uv uv] [--progress <印の file>]
(require doeff-hy.macros [defk deff val <-])
(val MODULE-TAGS {:context "worker" :role "main"})
(import argparse)
(import dataclasses [replace])
(import json)
(import doeff_core_effects.file_effects [PathKind PathStat FileFailed StatPath ReadText WriteText file-done])
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [reader state])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster.worker.core.env_prepare [prepare-env])
(import doeff_cluster.worker.protocol.env_translation [env-translation request-of-json answer-json])
(import doeff_cluster.worker.intent.env_prepare_model [PrepareRequest])


(defk json-file [path]
  {:pre [(: path str)] :post [(: % dict)] :tags {:context "worker" :role "main" :reads "json"}}
  "worker が渡した JSON の file(要求・許可表)を読むため — 読みは file の効果(答え手 = 入口の os-file-handler)・読めなければ OSError。"
  (<- text (file-done (ReadText path)))
  (json.loads text))


(defk requested-ms [path]
  {:pre [(: path str)] :post [(: % (| int None))] :tags {:context "worker" :role "main"}}
  "worker が要求の JSON を書いた刻(file の mtime・epoch ミリ秒)を返すため — worker はこの file を書いた直後に準備の process を起こすので、
   この刻から最初の処理ステージまでが「起こして準備を始めるまで」の秒(process の起こし・Hy と module の import — #3676)。読めなければ None。"
  (<- seen (| PathStat FileFailed) (StatPath path))
  (match seen
    (PathStat) (if (= seen.kind PathKind.FILE) (int (* 1000 seen.modified)) None)
    _ None))


(defk prepared-to-file [request result]
  {:pre [(: request PrepareRequest) (: result str)] :post [(: % None)] :tags {:context "worker" :role "main" :spells "json"}}
  "実行環境を準備し、答えの JSON を result へ置き換えで書くため(worker は置き換わった file だけを読む — 書きかけを読ませない)。"
  (<- answer (prepare-env request))
  (<- content (answer-json answer))
  (<- (file-done (WriteText result (json.dumps content :ensure-ascii False) :replace True)))
  None)


(deff main []  ; defk にできない: process の入口(`hy -m` の __main__ が Program の外で handler の組を並べて走らせる)
  {:pre [] :post [(: % None)] :tags {:context "worker" :role "main" :reads "json" :spells "json"}}
  "実行環境(root)1 つを準備して答えの JSON を書く入口(並び = 土台の本物の答え手 + 翻訳)。"
  (setv parser (argparse.ArgumentParser :description "実行環境(root)1 つの準備"))
  (.add-argument parser "--request" :required True)
  (.add-argument parser "--result" :required True)
  (.add-argument parser "--state" :required True)
  (.add-argument parser "--repo-keys" :default "")
  (.add-argument parser "--code-prepare" :required True)
  (.add-argument parser "--uv" :default "uv")
  (.add-argument parser "--progress" :default "" :help "処理ステージの進みの印の file(worker が停滞を見分ける)")
  (setv args (.parse-args parser))
  ;; 渡された file は本物の file の答え手(os-file-handler)の下で読む。
  (setv keys (if args.repo-keys (run (with-handlers [os-file-handler] (json-file args.repo-keys))) {}))
  (setv settings {"runtime-env.state" args.state "runtime-env.repo-keys" keys
                  "runtime-env.code-prepare" args.code-prepare "runtime-env.uv" args.uv
                  "runtime-env.progress" args.progress "runtime-env.notes" "/dev/stderr"})
  ;; 要求の JSON を書いた刻を準備の起こしの刻として要求に添える(印の startupSeconds — #3676)。
  (setv request (replace (run (request-of-json (run (with-handlers [os-file-handler] (json-file args.request)))))
                         :launched-ms (run (with-handlers [os-file-handler] (requested-ms args.request)))))
  (run (with-handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation]
                      (prepared-to-file request args.result)))
  None)


(when (= __name__ "__main__")
  (main))
