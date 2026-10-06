;;; coordinator が次に起きる刻と、落ち着くまでの約束の純粋な判断(#3865)。
;;;
;;; 調停ループは、要求が無い間、次に起きる刻(next-wake)まで受付を 1 本で待つ。状態を変えた歩か要求を受けた歩の後は待たずに
;;; もう 1 歩進め(after-step)、何も変えない歩の後だけ次に起きる刻まで待つ。今すぐが上限を越えて続けば、変わり続けた欄を名指して
;;; 落ちる(count-unsettled)。判断は期限ちょうどの刻に出る(1 秒の格子に丸めない)。
;;; ここは判断だけで、調停ループ(core/program.hy の coordinator-step)へは #3865 の単位 2 で繋ぐ。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [fields])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.intent.due_model [CoordinatorUnsettled])
(import doeff_cluster.shared.core.due_policy [earliest-due])
(import doeff_cluster.coordinator.core.api_policy [tick-due])
(import doeff_cluster.coordinator.core.idle_policy [rollout-due])
(import doeff_cluster.coordinator.core.watch_policy [all-waiting-unchanged])

;; 今すぐ(DueNow)が続いてよい歩の数。1 歩ごとに状態の欄の 1 つが落ち着く(生死の求め直し → task → 掃除 → 置き先)ので、正しい判断なら
;; 数歩で落ち着く。越えたら回り続けていると見て落ちる。
(val UNSETTLED-STEP-LIMIT 100)


(defk watchers-due [watchers state now]
  {:pre [(: watchers tuple) (: state ClusterState) (: now int)] :post [(: % (| DueAt DueNow DueNever))]
   :tags {:context "coordinator" :role "judgment"}}
  "返事を待たせている待ち(GET /watch の Watcher)が答えを変える最初の刻を知るため: どの待ちも落ち着いていれば(版が after のまま・名指した
   待ちは見え方を覚え済み — watch_policy.all-waiting-unchanged)いちばん早い期限 deadline-ms ちょうど(watch-deadline は now >= deadline-ms で
   「変わっていない」と答える)、落ち着いていない待ちが在れば今すぐ(settle-watch が今の刻で答える・見え方を覚える)、待ちが無ければ無し。"
  (<- unchanged bool (all-waiting-unchanged watchers state now))
  (cond
    (not watchers) (DueNever)
    (not unchanged) (DueNow)
    True (DueAt :at (min (gfor watcher watchers watcher.deadline-ms)))))


(defk next-wake [state now timing naming watchers]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming) (: naming ClusterNaming) (: watchers tuple)]
   :post [(: % (| DueAt DueNow DueNever))] :tags {:context "coordinator" :role "judgment"}}
  "何も変えない歩の後に、調停ループが次に起きる刻を知るため: 要求の無い歩の期限(api_policy.tick-due)・Rollout の期限(idle_policy.rollout-due —
   Rollout の進行中は Kubernetes を読む周期を含む・#3868)・待ちの期限(watchers-due)のいちばん早い答え。"
  (<- ticking (| DueAt DueNow DueNever) (tick-due state now timing))
  (<- rolling (| DueAt DueNow DueNever) (rollout-due state now timing naming))
  (<- waiting (| DueAt DueNow DueNever) (watchers-due watchers state now))
  (<- answer (| DueAt DueNow DueNever) (earliest-due #(ticking rolling waiting)))
  answer)


(defk after-step [due changed took]
  {:pre [(: due (| DueAt DueNow DueNever)) (: changed bool) (: took bool)] :post [(: % (| DueAt DueNow DueNever))]
   :tags {:context "coordinator" :role "judgment"}}
  "歩の後にどこまで待つかを決めるため: 状態を変えた歩(changed)か要求を受けた歩(took)の後は今すぐもう 1 歩(その歩の答えが次の判断の
   答えを変えうる)。何も変えない歩の後は、その時に求めた次に起きる刻 due まで。"
  (if (or changed took) (DueNow) due))


(defk wait-seconds [due now]
  {:pre [(: due (| DueAt DueNow DueNever)) (: now int)] :post [(: % (| float None))] :tags {:context "coordinator" :role "judgment"}}
  "次に起きる刻 due を、受付を待つ秒(NextRequests の timeout)にするため: 刻までの秒(過ぎていれば 0)・今すぐは 0・無しは期限なし(None
   — 要求か停止の合図か外の出来事でだけ起きる)。"
  (match due
    (DueAt :at at) (/ (max 0 (- at now)) 1000.0)
    (DueNow) 0.0
    (DueNever) None))


(defk count-unsettled [streak due before after]
  {:pre [(: streak int) (: due (| DueAt DueNow DueNever)) (: before ClusterState) (: after ClusterState)] :post [(: % int)]
   :tags {:context "coordinator" :role "judgment"}}
  "今すぐ(DueNow)が続いた歩の数を数え、回り続ける調停ループを名指して止めるため: 今すぐなら 1 足し、それ以外は 0 に戻す。足した数が
   UNSETTLED-STEP-LIMIT を越えたら、歩の前後の状態 before・after で違う欄の名を書いて CoordinatorUnsettled で落ちる。"
  (val counted (match due (DueNow) (+ streak 1) (DueAt) 0 (DueNever) 0))
  (when (> counted UNSETTLED-STEP-LIMIT)
    (raise (CoordinatorUnsettled
             (.format "調停ループが {} 歩 落ち着かない(今すぐが続いた)— 変わり続けた欄: {}" counted
                      (or (lfor f (fields before) :if (!= (getattr before f.name) (getattr after f.name)) f.name) "無し")))))
  counted)
