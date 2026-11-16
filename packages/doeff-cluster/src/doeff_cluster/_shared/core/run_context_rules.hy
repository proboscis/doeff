;;; 実行先の子 process の文脈(RunContext)と環境変数の名の綴りと読み — 根の job_context から移した(#2981・#2167 の子)。
;;; worker が子へ渡す環境変数の組を作る(worker-context-environ・process-context-environ)、名 → 値の置き場から RunContext を読む
;;; (context-of-environ)、文脈が持つ実行環境の宣言を解く(runtime-env-of-context)。環境変数の名の定義点はこの module。
;;; 型は doeff_cluster._shared.intent.run_context。この process の os.environ を読むのは入口(doeff_cluster._shared.entry.run_context_env)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import collections.abc [Mapping])
(import json)
(import doeff_cluster._shared.intent.run_context [RunContext])
(import doeff_cluster._shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster._shared.core.runtime_env_rules [runtime-env-of-json])
(import doeff_cluster._shared.intent.job_model [JobSpec] doeff_cluster._shared.core.job_rules [spec-hash])


;; task の要り(MiB の 10 進の整数)を task の子 process へ渡す名(ADR-DOE-CLUSTER-005 R4): coordinator が task を置いた時に固定した申告で、
;; heartbeat の返事の task の行の memoryMiB — worker の memory の見張りが task の使い(job の名の子の memory)と比べ、上に載る系の残す線を
;; 割った回に越えた task を止める数そのもの。起こした時の値で、task が走る間は動かない。要りを名乗らない task(行に memoryMiB が無い —
;; 見張りが越えで止めない)と常駐の job には置かない。task は中で並べる本数をこの数の内に縛るために読む。
(val TASK-MEMORY-MIB-VAR "DOEFF_WORKER_TASK_MEMORY_MIB")


(defk worker-context-environ [coordinator worker]
  {:pre [(: coordinator str) (: worker str)] :post [(: % (get dict #(str str)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker が自分の子 process 全部へ渡す文脈の環境変数(coordinator の URL と worker の名)を作るため。本番の worker の入口(main)と
   sim の宿(local.run-context-of)が同じ名で作り、context-of-environ が読む(名の定義点はこの module)。"
  {"DOEFF_WORKER_COORDINATOR" coordinator "DOEFF_WORKER_NAME" worker})


(defk process-context-environ [spec instance attempt]
  {:pre [(: spec JobSpec) (: instance str) (: attempt int)] :post [(: % (get dict #(str str)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker が起こす子 process 1 つへ渡す文脈の環境変数(job の名・版・試行・世代・spec の指紋・割り当て、実行環境の job なら宣言の
   JSON とキー、要りを名乗る task なら要り)を作るため。本番の ProcessHost.launch と sim の宿(local.run-context-of)が同じ関数で作る —
   実行環境の job の子だけが DOEFF_RUNTIME_ENV・DOEFF_RUNTIME_ENV_KEY を受け(env の job でなければ置かない)、要りを名乗る task の子だけが
   TASK-MEMORY-MIB-VAR を受ける(spec.memory-mib が None なら置かない)。"
  (| {"DOEFF_WORKER_JOB" spec.name
      "DOEFF_WORKER_REVISION" spec.revision
      "DOEFF_WORKER_ATTEMPT" (str attempt)
      "DOEFF_WORKER_INSTANCE" instance
      "DOEFF_WORKER_SPEC_HASH" (spec-hash spec)
      "DOEFF_WORKER_PLACEMENT" (if (is spec.placement None) "" (str spec.placement))}
     (if spec.runtime-env
         {"DOEFF_RUNTIME_ENV" spec.runtime-env "DOEFF_RUNTIME_ENV_KEY" (or spec.env-key "")}
         {})
     (if (is spec.memory-mib None) {} {TASK-MEMORY-MIB-VAR (str spec.memory-mib)})))


(defk context-of-environ [environ]
  {:pre [(: environ (get Mapping #(str str)))] :post [(: % RunContext)] :tags {:context "doeff-cluster" :role "judgment"}}
  "子 process の環境変数(worker-context-environ と process-context-environ が置いた名)から RunContext を読むため(無い名は空)。
   本番の子(入口の context-from-env — os.environ)と sim の宿(local.run-context-of — 同じ関数が作った dict)が同じ読みを通る。"
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


(defk runtime-env-of-context [ctx]
  {:pre [(: ctx RunContext)] :post [(: % (| RuntimeEnv None))]}
  ;; この process が走っている実行環境の宣言(無ければ None)— env の組み立てが送り手(remote-cluster の TaskSender・DetachedSender)へ渡し、
  ;; service や task がさらに送る task を同じ env で走らせるため(2026-09-26 — 送り手の版が image に固定されない)。
  (if ctx.runtime-env
      (do (<- env RuntimeEnv (runtime-env-of-json (json.loads ctx.runtime-env))) env)
      None))
