;;; 本番の土台が並べる「coordinator に話す handler の組」(ADR-DOE-CLUSTER-001・計画 2.3)。
;;;
;;; job の Program は自分の土台(本体を受けて自分の handler と scheduler の下で走らせる defk)を持ち、実行先は handler を足さない(R2)。
;;; 土台のうち、クラスタの約束の effect(ReportReady・ReportMetrics・ReadShared / WriteShared・LeaseOp と名前付きの semaphore・
;;; RemoteJob・切り離した task・温める表)に答える handler は、宿の契約の run-context(HOST-CONTRACT — 本番は host-reader が答える)から
;;; client を作る。その組み立ての定義点をここ 1 つにする(業務の土台ごとに client の作り方を写さない)。
;;;
;;;   (defk my-foundation [body]
;;;     …
;;;     (<- answer (scheduled (with-handlers [(await-handler) (state) (environ-reader) host-reader (async-time-handler)]
;;;                             (with-cluster-handlers body))))
;;;     answer)
;;;
;;; sim-cluster では、sim の土台はこの組を並べない — 同じ effect に sim の宿が同じ要求の形で答える(local.hy の coordinator-answers)。
(require doeff-hy.macros [defk <- val])
(import doeff [Program EffectBase with-handlers])
(import doeff_core_effects.effects [Ask])
(import .host_contract [HOST-CONTRACT])
(import .job_context [RunContext runtime-env-of-context])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import .report_client [report-client])
(import .readiness_handlers [readiness-http])
(import .metrics_handlers [metrics-http])
(import .shared_handlers [shared-http SharedClient])
(import doeff_cluster.shared.core.semaphore_handlers [cluster-semaphore SemaphoreSession])
(import doeff_cluster.shared.core.lease_rules [lease-holder])
(import .remote [remote-cluster TaskClient])
(import .detached [detached-cluster DetachedClient warm-cluster WarmClient])


(defk lease-holder-of [ctx]
  {:pre [(: ctx RunContext)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "名前付きの lease の担い手の名 = job と process の世代(cluster で一意 — 同じ job の新旧の世代を分ける)。綴りは worker が終わった
   process の lease を外す時と同じ定義 lease_rules.lease-holder。"
  (lease-holder ctx.job (or ctx.instance ctx.worker)))


(defk cluster-handlers []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster" :role "foundation"}}
  "本番の土台が並べる、coordinator に話す handler の組(外側が先)を、宿の run-context から client を作って返す。
   並び: 報告(readiness・計器)→ 盤と lease → task(RemoteJob)→ 切り離した task → 温める表 → 名前付きの semaphore(lease の写し —
   盤の lease より内側・scheduler より内側)。送る task は、この process と同じ実行環境で走る(run-context の runtime-env)。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- env (| RuntimeEnv None) (runtime-env-of-context ctx))
  (<- holder str (lease-holder-of ctx))
  (val report (report-client ctx))
  [(readiness-http report)
   (metrics-http report)
   (shared-http (SharedClient ctx.coordinator-url))
   (remote-cluster (TaskClient ctx.coordinator-url ctx.revision :runtime-env env))
   (detached-cluster (DetachedClient ctx.coordinator-url ctx.revision :runtime-env env))
   (warm-cluster (WarmClient ctx.coordinator-url :actor ctx.job))
   (cluster-semaphore (SemaphoreSession holder))])


(defk with-cluster-handlers [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster" :role "foundation"}}
  "本体を coordinator に話す handler の組(cluster-handlers)の下で走らせる — 土台はこれを scheduler と宿の読みの内側で呼ぶ。
   組の作り方を module の最上位の関数に置くのは、doeff-effect-analyzer(foundation_check)が組の中身を追えるようにするため
   (本体の中の無名の Program は追えない)。"
  (<- cluster list (cluster-handlers))
  (<- answer (with-handlers cluster body))
  answer)
