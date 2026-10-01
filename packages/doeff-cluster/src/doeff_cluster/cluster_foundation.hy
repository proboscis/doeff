;;; 本番の土台が並べる「coordinator に話す handler の組」(ADR-DOE-CLUSTER-001・計画 2.3)。
;;;
;;; job の Program は自分の土台(本体を受けて自分の handler と scheduler の下で走らせる defk)を持ち、実行先は handler を足さない(R2)。
;;; 土台のうち、クラスタの約束の effect(ReportReady・ReportMetrics・ReadShared / WriteShared・LeaseOp と名前付きの semaphore・
;;; RemoteJob・切り離した task・温める表)に答える handler は、宿の契約の run-context(HOST-CONTRACT — 本番は host-reader が答える)から
;;; client を作る。その組み立ての定義点をここ 1 つにする(業務の土台ごとに client の作り方を写さない)。
;;;
;;;   (defk my-foundation [body]
;;;     …
;;;     (<- answer (scheduled (with-handlers [(await-handler) slog-handler (http-production-handler) (state) (environ-reader) host-reader (async-time-handler)]
;;;                             (with-cluster-handlers body))))
;;;     answer)
;;;
;;; sim-cluster では、sim の土台はこの組を並べない — 同じ effect に sim の宿が同じ要求の形で答える(local.hy の coordinator-answers)。
(require doeff-hy.macros [defk <- val])
(import dataclasses [replace])
(import doeff [Program EffectBase with-handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import .job_context [RunContext runtime-env-of-context])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.foundation.report_client [service-report-of])
(import doeff_cluster.shared.protocol.service_report [ServiceReport])
(import doeff_cluster.shared.protocol.readiness_handlers [readiness-http])
(import doeff_cluster.shared.protocol.metrics_handlers [metrics-http])
(import .shared_handlers [shared-http])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions route-of])
(import doeff_cluster.foundation.coordinator_http [REPLY-SECONDS CONNECT-SECONDS PREFERRED-RECHECK-SECONDS IDEMPOTENT-DEADLINE-SECONDS RESEND-PAUSE-SECONDS default-actor])
(import doeff_cluster.shared.core.semaphore_handlers [cluster-semaphore SemaphoreSession])
(import doeff_cluster.shared.core.lease_rules [lease-holder])
(import doeff_cluster.shared.protocol.remote [remote-cluster TaskSender])
(import doeff_cluster.shared.protocol.detached [detached-cluster DetachedSender warm-cluster])


(defk lease-holder-of [ctx]
  {:pre [(: ctx RunContext)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "名前付きの lease の担い手の名 = job と process の世代(cluster で一意 — 同じ job の新旧の世代を分ける)。綴りは worker が終わった
   process の lease を外す時と同じ定義 lease_rules.lease-holder。"
  (lease-holder ctx.job (or ctx.instance ctx.worker)))


(defk coordinator-route-options []
  {:pre [] :post [(: % RouteOptions)] :tags {:context "doeff-cluster" :role "foundation"}}
  "coordinator への宛先の部品の送り方(返事の上限・一巡し直す回数・先頭を試し直す間・書きの送り手)を、この process の値で作るため。"
  (RouteOptions :reply-seconds REPLY-SECONDS :connect-seconds CONNECT-SECONDS :resend-deadline-seconds IDEMPOTENT-DEADLINE-SECONDS :resend-pause-seconds RESEND-PAUSE-SECONDS :connect-retries 4 :recheck-ms (int (* PREFERRED-RECHECK-SECONDS 1000))
                :actor (default-actor)))


(defk cluster-handlers []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster" :role "foundation"}}
  "本番の土台が並べる、coordinator に話す handler の組(外側が先)を、宿の run-context から client を作って返す。
   並び: 報告(readiness・計器)→ 盤と lease → task(RemoteJob)→ 切り離した task → 温める表 → 名前付きの semaphore(lease の写し —
   盤の lease より内側・scheduler より内側)。送る task は、この process と同じ実行環境で走る(run-context の runtime-env)。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- env (| RuntimeEnv None) (runtime-env-of-context ctx))
  (<- holder str (lease-holder-of ctx))
  (<- versions dict (Ask HOST-CONTRACT.versions-key))
  (<- report ServiceReport (service-report-of ctx))
  (<- options RouteOptions (coordinator-route-options))
  (<- now int (now-epoch-ms))
  (<- route CoordinatorRoute (route-of ctx.coordinator-url now))
  ;; 宛先の状態は組の handler が 1 つの入れ物で分ける(どの口が宛先を替えても次の口はそこから試す)。報告は観測なので、全部の宛先に
  ;; 届かない時の一巡し直しは 1 回だけ(業務を長く止めない — 前の ServiceReportClient と同じ)。
  (val cell (RouteCell route))
  (val report-options (replace options :connect-retries 1))
  [(readiness-http cell report-options report)
   (metrics-http cell report-options report)
   (shared-http cell options)
   (remote-cluster cell options (TaskSender :revision ctx.revision :versions versions :runtime-env env))
   (detached-cluster cell options (DetachedSender :revision ctx.revision :versions versions :runtime-env env
                                                 :deadline-seconds IDEMPOTENT-DEADLINE-SECONDS))
   (warm-cluster cell (replace options :actor ctx.job))
   (cluster-semaphore (SemaphoreSession holder))])


(defk with-cluster-handlers [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster" :role "foundation"}}
  "本体を coordinator に話す handler の組(cluster-handlers)の下で走らせる — 土台はこれを scheduler と宿の読みの内側で呼ぶ。
   組の作り方を module の最上位の関数に置くのは、doeff-effect-analyzer(foundation_check)が組の中身を追えるようにするため
   (本体の中の無名の Program は追えない)。"
  (<- cluster list (cluster-handlers))
  (<- answer (with-handlers cluster body))
  answer)
