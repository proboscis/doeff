;;; 実行環境(root)1 つの準備の process の入口(#2028 — env_handlers.hy の main をここへ移した。翻訳 env-translation は
;;; worker/protocol/env_translation)。worker(worker/protocol/env_store の env-host)はこの入口を worker 自身の環境の別の process として起こす。
;;; 送る名 ENV-TOOL は旧い path(doeff_cluster.env_handlers — 新しい入口へ渡すだけ)のまま(#2028 の 1 段目・消すのは #2113):
;;;
;;;   hy -m doeff_cluster.env_handlers --request <要求の JSON> --result <答えの JSON> --state <state dir>
;;;      --repo-keys <許可表の JSON> --code-prepare <worker の code_prepare.hy> [--uv uv] [--progress <印の file>]
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "worker" :role "main"})
(import argparse)
(import json)
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [reader state])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster.worker.core.env_prepare [prepare-env])
(import doeff_cluster.worker.protocol.env_translation [env-translation request-of-json answer-json])


(deff main []  ; defk にできない: process の入口(`hy -m` の __main__ が Program の外で handler の組を並べて走らせる)
  {:pre [] :post [(: % None)] :tags {:context "worker" :role "main"}}
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
  (setv keys (if args.repo-keys (json.loads (.read-text (Path args.repo-keys) :encoding "utf-8")) {}))
  (setv settings {"runtime-env.state" args.state "runtime-env.repo-keys" keys
                  "runtime-env.code-prepare" args.code-prepare "runtime-env.uv" args.uv
                  "runtime-env.progress" args.progress "runtime-env.notes" "/dev/stderr"})
  (setv request (run (request-of-json (json.loads (.read-text (Path args.request) :encoding "utf-8")))))
  (setv answer (run (with-handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation]
                                   (prepare-env request))))
  (setv content (run (answer-json answer)))
  (setv tmp (Path (+ args.result ".tmp")))
  (.write-text tmp (json.dumps content :ensure-ascii False) :encoding "utf-8")
  (.replace tmp args.result)
  None)


(when (= __name__ "__main__")
  (main))
