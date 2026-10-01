;;; 実行先の子 process の文脈(worker が環境変数で渡す RunContext)と、その読み(2026-09-26)。
;;;
;;; 入口(job_entry)とは別の module に置く理由: 子は `hy -m doeff_cluster.job_entry` で起きるので job_entry は __main__ として読まれる。
;;; env の組み立て(業務の側の関数)が job_entry から RunContext や runtime-env-of-context を import すると、同じ file が
;;; doeff_cluster.job_entry としてもう 1 回読まれて class が 2 つになり、__main__ の RunContext を渡された :pre の型の検めが必ず落ちた
;;; (実験用の namespace で再現)。入口でないこの module の class は 1 つだけ読まれる。job_entry はここから import し、今の名は
;;; job_entry からも引ける。
(require doeff-hy.macros [defk <-])
(import collections.abc [Mapping])
(import dataclasses [dataclass])
(import json)
(import os)
(import doeff [run])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv runtime-env-of-json])
(import doeff_cluster.shared.intent.job_model [JobSpec] doeff_cluster.shared.core.job_rules [spec-hash])


(defclass [(dataclass :frozen True)] RunContext []
  "実行先の文脈(worker が環境変数で渡す)。env の組み立てだけが読む。"
  (#^ str coordinator-url)
  (#^ str worker)
  (#^ str revision)
  (#^ str job)
  ;; この process の世代(worker が起こした時に振った名・試行の番号・起こした spec の指紋・割り当ての世代)。
  ;; readiness と計器の報告に載せ、coordinator は今の宣言で今動いている process の報告だけを数える。
  (setv #^ str instance "")
  (setv #^ str attempt "")
  (setv #^ str spec-hash "")
  (setv #^ str placement "")
  ;; 実行環境の宣言(JSON の文字列)とキー。env の task でなければ空。子がさらに task を送る時の既定の env になる。
  (setv #^ str runtime-env "")
  (setv #^ str env-key "")

  (defn #^ dict identity [self]
    "報告に載せる process の世代(coordinator の resource_policy.report-matches が比べる欄)。"
    {"instance" self.instance "attempt" self.attempt "specHash" self.spec-hash
     "placement" (if self.placement (int self.placement) None)}))


(defk worker-context-environ [coordinator worker]
  {:pre [(: coordinator str) (: worker str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "worker が自分の子 process 全部へ渡す文脈の環境変数(coordinator の URL と worker の名)を作るため。本番の worker の入口(main)と
   sim の宿(local.run-context-of)が同じ名で作り、context-of-environ が読む(名の定義点はこの module)。"
  {"DOEFF_WORKER_COORDINATOR" coordinator "DOEFF_WORKER_NAME" worker})


(defk process-context-environ [spec instance attempt]
  {:pre [(: spec JobSpec) (: instance str) (: attempt int)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "worker が起こす子 process 1 つへ渡す文脈の環境変数(job の名・版・試行・世代・spec の指紋・割り当て、実行環境の job なら宣言の
   JSON とキー)を作るため。本番の ProcessHost.launch と sim の宿(local.run-context-of)が同じ関数で作る — 実行環境の job の子だけが
   DOEFF_RUNTIME_ENV・DOEFF_RUNTIME_ENV_KEY を受ける(env の job でなければ置かない)。"
  (| {"DOEFF_WORKER_JOB" spec.name
      "DOEFF_WORKER_REVISION" spec.revision
      "DOEFF_WORKER_ATTEMPT" (str attempt)
      "DOEFF_WORKER_INSTANCE" instance
      "DOEFF_WORKER_SPEC_HASH" (spec-hash spec)
      "DOEFF_WORKER_PLACEMENT" (if (is spec.placement None) "" (str spec.placement))}
     (if spec.runtime-env
         {"DOEFF_RUNTIME_ENV" spec.runtime-env "DOEFF_RUNTIME_ENV_KEY" (or spec.env-key "")}
         {})))


(defk context-of-environ [environ]
  {:pre [(: environ Mapping)] :post [(: % RunContext)] :tags {:context "doeff-cluster" :role "protocol"}}
  "子 process の環境変数(worker-context-environ と process-context-environ が置いた名)から RunContext を読むため(無い名は空)。
   本番の子(context-from-env — os.environ)と sim の宿(local.run-context-of — 同じ関数が作った dict)が同じ読みを通る。"
  (RunContext (.get environ "DOEFF_WORKER_COORDINATOR" "")
              (.get environ "DOEFF_WORKER_NAME" "")
              (.get environ "DOEFF_WORKER_REVISION" "")
              (.get environ "DOEFF_WORKER_JOB" "")
              :instance (.get environ "DOEFF_WORKER_INSTANCE" "")
              :attempt (.get environ "DOEFF_WORKER_ATTEMPT" "")
              :spec-hash (.get environ "DOEFF_WORKER_SPEC_HASH" "")
              :placement (.get environ "DOEFF_WORKER_PLACEMENT" "")
              :runtime-env (.get environ "DOEFF_RUNTIME_ENV" "")
              :env-key (.get environ "DOEFF_RUNTIME_ENV_KEY" "")))


(defn #^ RunContext context-from-env []
  (run (context-of-environ os.environ)))


(defk runtime-env-of-context [ctx]
  {:pre [(: ctx RunContext)] :post [(: % (| RuntimeEnv None))]}
  ;; この process が走っている実行環境の宣言(無ければ None)— env の組み立てが送り手(TaskClient・DetachedClient)へ渡し、
  ;; service や task がさらに送る task を同じ env で走らせるため(2026-09-26 — 送り手の版が image に固定されない)。
  (if ctx.runtime-env
      (do (<- env RuntimeEnv (runtime-env-of-json (json.loads ctx.runtime-env))) env)
      None))
