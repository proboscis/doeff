;;; 実行先の子 process の文脈(worker が環境変数で渡す RunContext)と、その読み(2026-09-26)。
;;;
;;; 入口(job_entry)とは別の module に置く理由: 子は `hy -m doeff_cluster.job_entry` で起きるので job_entry は __main__ として読まれる。
;;; env の組み立て(業務の側の関数)が job_entry から RunContext や runtime-env-of-context を import すると、同じ file が
;;; doeff_cluster.job_entry としてもう 1 回読まれて class が 2 つになり、__main__ の RunContext を渡された :pre の型の検めが必ず落ちた
;;; (実験用の namespace で再現)。入口でないこの module の class は 1 つだけ読まれる。job_entry はここから import し、今の名は
;;; job_entry からも引ける。
(require doeff-hy.macros [defk <-])
(import dataclasses [dataclass])
(import json)
(import os)
(import .runtime_env_model [RuntimeEnv runtime-env-of-json])


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


(defn #^ RunContext context-from-env []
  (RunContext (os.environ.get "DOEFF_WORKER_COORDINATOR" "")
              (os.environ.get "DOEFF_WORKER_NAME" "")
              (os.environ.get "DOEFF_WORKER_REVISION" "")
              (os.environ.get "DOEFF_WORKER_JOB" "")
              :instance (os.environ.get "DOEFF_WORKER_INSTANCE" "")
              :attempt (os.environ.get "DOEFF_WORKER_ATTEMPT" "")
              :spec-hash (os.environ.get "DOEFF_WORKER_SPEC_HASH" "")
              :placement (os.environ.get "DOEFF_WORKER_PLACEMENT" "")
              :runtime-env (os.environ.get "DOEFF_RUNTIME_ENV" "")
              :env-key (os.environ.get "DOEFF_RUNTIME_ENV_KEY" "")))


(defk runtime-env-of-context [ctx]
  {:pre [(: ctx RunContext)] :post [(: % (| RuntimeEnv None))]}
  ;; この process が走っている実行環境の宣言(無ければ None)— env の組み立てが送り手(TaskClient・DetachedClient)へ渡し、
  ;; service や task がさらに送る task を同じ env で走らせるため(2026-09-26 — 送り手の版が image に固定されない)。
  (if ctx.runtime-env
      (do (<- env RuntimeEnv (runtime-env-of-json (json.loads ctx.runtime-env))) env)
      None))
