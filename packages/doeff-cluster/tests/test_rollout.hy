;; Rollout の reconciler を、仮想の時計の上の小さな世界(k8s の Deployment と Pod・worker 1 台・書き手の Service)で動かす。
;;
;; 世界の約束(本番の Deployment の書き手の置き換えに合わせる):
;;   - Deployment の Pod は宣言の台数を増やすと POD-START 後に ready、減らすと POD-STOP の間 終了中(SIGTERM を扱わないので書き続ける)。
;;   - worker は Service の宣言(replicas 1)を受けると JOB-START 後に running。healthy の間は毎拍 ReportReady(真)を送る。
;;   - 「書き手が居る」= Deployment の Pod(終了中を含む)か、新の process のどちらかが動いている。
;; 確かめること: どの段の失敗でも旧を先に戻してから新を止める(書き手が 0 の拍が無い)・coordinator が落ちても続きから進む・
;; 配備の流れが Deployment の台数を戻したら Rollout の状態に出る・dry-run は k8s に保存しない。
(require doeff-hy.macros [deftest])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming ClusterState])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.program [rollout-tick])
(import doeff_cluster.coordinator.protocol.kube [KubeMemory kube-memory])
(import doeff_cluster.shared.intent.job_model [JobSpec] doeff_cluster.shared.core.job_rules [spec-hash])
(import tests.program_rows [SAMPLE-RUN program-run])

(setv T (ClusterTiming))
(setv V {"python" "3.14.0"})
(setv DEP "prod/app-writer")
(setv POD-START 5000 POD-STOP 30000 JOB-START 3000 STOP-MS 2000 WINDOW 10)
(setv FORWARD {"from" {"kind" "Deployment" "namespace" "prod" "name" "app-writer"}
               "to" {"kind" "Service" "name" "writer-a"}
               "readyTimeoutSeconds" 60 "stopTimeoutSeconds" 90 "observeSeconds" 120 "failAfterSeconds" 15})
(setv REVERSE {"from" {"kind" "Service" "name" "writer-a"}
               "to" {"kind" "Deployment" "namespace" "prod" "name" "app-writer" "replicas" 1}
               "readyTimeoutSeconds" 60 "stopTimeoutSeconds" 90 "observeSeconds" 30 "failAfterSeconds" 15})


(defclass Sim []
  (defn #^ None __init__ [self #^ int [window WINDOW] #^ int [first-report-ms 0]]
    (setv self.naming (ClusterNaming))   ; 外の系と取り交わす名(検が差し替える)
    (setv self.now 1000000
          self.kube (KubeMemory {DEP {"specReplicas" 1 "replicas" 1 "readyReplicas" 1 "availableReplicas" 1
                                      "updatedReplicas" 1 "generation" 1 "observedGeneration" 1 "annotations" {}}})
          ;; Pod = {"ready-at" ms "gone-at" ms | None}
          self.pods [{"ready-at" 0 "gone-at" None}]
          ;; 新の process {"since" ms "instance" 名 "attempt" n "spec" JobSpec "stopping-until" ms | None}。
          ;; worker と同じく、spec(引数 = 設定・版)が変われば前の process を止めてから(STOP-MS)新しい process を起こす。
          self.proc None
          self.attempts 0
          self.first-report-ms first-report-ms  ; running になってから最初の拍を終える(最初の ReportReady)まで
          self.first-ready {}                   ; 世代の名 → 最初の ReportReady の時刻
          self.old-stopped-at None              ; 旧(Deployment)の宣言の台数が 0 になった時刻
          self.healthy True
          self.gaps 0 self.log [])
    (setv self.state (ClusterState :started-ms (- self.now 60000)))
    (setv self.state (self.call "POST" "/resources/Service"
                                {"name" "writer-a"
                                 "spec" {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN "replicas" 0
                                         "readiness" {"windowSeconds" window}}})))

  (defn #^ ClusterState call [self #^ str method #^ str path #^ (| dict None) [body None] #^ (| str None) [actor "c-test"]]
    (setv #(state status reply) (responded self.state (http-request method path {} body :actor actor) self.now T))
    (assert (< status 300) #(method path status reply))
    (setv self.state state)
    state)

  (defn #^ ClusterState rollout [self #^ str name #^ dict spec]
    (self.call "POST" "/resources/Rollout" {"name" name "spec" spec}))

  (defn #^ str phase [self #^ str name] (. (get self.state.rollouts name) status phase))

  (defn #^ None advance-pods [self]
    (setv want (get self.kube.deployments DEP "specReplicas")
          live (lfor p self.pods :if (is (get p "gone-at") None) p))
    (while (< (len live) want)
      (setv pod {"ready-at" (+ self.now POD-START) "gone-at" None})
      (.append self.pods pod) (.append live pod))
    (for [pod (cut (list (reversed live)) 0 (max 0 (- (len live) want)))]
      (setv (get pod "gone-at") (+ self.now POD-STOP)))
    (setv self.pods (lfor p self.pods :if (or (is (get p "gone-at") None) (> (get p "gone-at") self.now)) p))
    (setv live (lfor p self.pods :if (is (get p "gone-at") None) p))
    (.update (get self.kube.deployments DEP)
             {"replicas" (len live) "updatedReplicas" (len live) "availableReplicas" (len live)
              "readyReplicas" (len (lfor p live :if (<= (get p "ready-at") self.now) p))}))

  (defn #^ str proc-phase [self]
    (assert (is-not self.proc None) "proc-phase は動いている process の段を読む")
    (cond
      (get self.proc "stopping-until") "stopping"
      (>= self.now (+ (get self.proc "since") JOB-START)) "running"
      True "starting"))

  (defn #^ None worker-beat [self]
    ;; 状態の行は worker_policy.statuses → handlers.status-row と同じ欄(process の世代を載せる)。
    (setv statuses (if self.proc
                       (do (setv spec (get self.proc "spec"))
                           [{"name" "writer-a" "phase" (self.proc-phase)
                             "runningRevision" spec.revision "desiredRevision" spec.revision "pid" 100
                             "attempts" (get self.proc "attempt") "instance" (get self.proc "instance")
                             "specHash" (spec-hash spec) "placement" spec.placement}])
                       []))
    (setv #(state _ reply) (responded self.state (http-request "POST" "/heartbeat" {}
                                                        {"name" "atlas" "provides" ["net"] "capacity" 10 "versions" V
                                                         "statuses" statuses}) self.now T))
    (setv self.state state)
    (setv want (next (gfor j (get reply "jobs") :if (= (get j "name") "writer-a")
                           (JobSpec (get j "name") (get j "entry") (tuple (get j "args")) (get j "revision")
                                    :placement (.get j "placement")))
                     None))
    (cond
      ;; 止めている process は STOP-MS の後に消える
      (and self.proc (get self.proc "stopping-until"))
        (when (>= self.now (get self.proc "stopping-until")) (setv self.proc None))
      ;; 宣言から外れた・spec(設定・版)が変わった → 前の process を止める(旧新を同時に動かさない)
      (and self.proc (or (is want None) (!= want (get self.proc "spec"))))
        (setv (get self.proc "stopping-until") (+ self.now STOP-MS))
      (and want (is self.proc None))
        (do (+= self.attempts 1)
            (setv self.proc {"since" self.now "instance" (.format "{}-sim{}" self.attempts self.now) "attempt" self.attempts
                             "spec" want "stopping-until" None})))
    ;; 止めている間も process は拍を回す(前の世代の報告として届く — coordinator は数えない)
    (when (and self.proc self.healthy
               (>= self.now (+ (get self.proc "since") JOB-START self.first-report-ms)))
      (setv spec (get self.proc "spec"))
      (.setdefault self.first-ready (get self.proc "instance") self.now)
      (self.call "POST" "/resources/Service/writer-a/readiness"
                 {"worker" "atlas" "pid" 1 "revision" spec.revision "instance" (get self.proc "instance")
                  "attempt" (str (get self.proc "attempt")) "specHash" (spec-hash spec) "placement" spec.placement
                  "ready" True "reason" "拍を終えた"} :actor None))
    None)

  (defn #^ None step [self]
    (+= self.now 1000)
    (self.advance-pods)
    (self.worker-beat)
    (setv self.state (run (scheduled (with_handlers [(kube-memory self.kube)] (rollout-tick self.state T self.naming self.now)))))
    (when (and (is self.old-stopped-at None) (= (get self.kube.deployments DEP "specReplicas") 0))
      (setv self.old-stopped-at self.now))
    (setv old-up (> (len self.pods) 0) new-up (is-not self.proc None))
    (when (not (or old-up new-up)) (+= self.gaps 1))
    (.append self.log #(self.now (len self.pods) new-up)))

  (defn #^ str run-until [self #^ str name #^ tuple phases #^ int [limit 600]]
    (for [_ (range limit)]
      (self.step)
      (when (in (self.phase name) phases) (return (self.phase name))))
    (raise (AssertionError (.format "{} が {} にならない: {}" name phases (. (get self.state.rollouts name) status)))))

  (defn #^ None restart-coordinator [self]
    "coordinator の作り直し: 保存した形から読み直す(worker の報告・readiness・k8s の観測は失う)。"
    (setv self.state (state-from-json (state-to-json self.state) self.now))))


(defn #^ None assert-old-restored-before-new-stopped [#^ Sim sim #^ str name]
  "戻しの順: 新の Service を 0 にした出来事は、旧が Ready に戻った(restoredOldMs)後で、その時 k8s の Pod が ready だった。"
  (setv status (. (get sim.state.rollouts name) status))
  (setv stops (lfor e sim.state.audit
                    :if (and (= e.kind "Service") (= (.get e.changes "spec.replicas") [1 0])) e))
  (assert stops "新を止めていない")
  (for [e stops]
    (assert (>= e.at status.restored-old-ms) #(e status))
    (setv pods-at (next (gfor #(at pods _) sim.log :if (= at e.at) pods)))
    (assert (> pods-at 0) #(e sim.log))))


(defn #^ list phases-of [#^ Sim sim #^ str name]
  (lfor h (. (get sim.state.rollouts name) status history) h.phase))


(deftest test-forward-rollout-stops-the-deployment-only-after-the-service-is-ready
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (assert (= (sim.run-until "to-worker" #("Complete" "RolledBack")) "Complete"))
  (assert (= (phases-of sim "to-worker") ["WaitingNewReady" "StoppingOld" "Observing" "Complete"]))
  (assert (= (get sim.kube.deployments DEP "specReplicas") 0))
  (assert (= (. (get sim.state.jobs 0) replicas) 1))
  (assert (= sim.gaps 0))
  ;; 旧を止めた(k8s の scale 0)時刻には、新はもう Ready だった
  (setv stop-old (next (gfor c sim.kube.calls :if (= (get c "replicas") 0) c)))
  (assert (= stop-old {"op" "scale" "key" DEP "replicas" 0 "dryRun" False}))
  ;; 出来事の記録: 新を起こしたのは rollout/to-worker(送り手)
  (assert (in "rollout/to-worker" (lfor e sim.state.audit :if (= e.kind "Service") e.actor))))


(deftest test-new-that-never-becomes-ready-is-rolled-back-without-touching-the-deployment
  (setv sim (Sim) sim.healthy False)
  (sim.rollout "to-worker" FORWARD)
  (assert (= (sim.run-until "to-worker" #("Complete" "RolledBack")) "RolledBack"))
  (assert (in "Ready にならなかった" (. (get sim.state.rollouts "to-worker") status failure)))
  (assert (= (get sim.kube.deployments DEP "specReplicas") 1))
  (assert (= (lfor c sim.kube.calls :if (= (get c "op") "scale") c) []))   ; 旧は一度も止めていない
  (assert (= (. (get sim.state.jobs 0) replicas) 0))                        ; 新は止めた
  (assert (= sim.gaps 0)))


(deftest test-failure-while-stopping-the-old-restores-the-old-before-stopping-the-new
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (sim.run-until "to-worker" #("StoppingOld"))
  (setv sim.healthy False)   ; 旧を止め始めた直後に新が準備できなくなる
  (assert (= (sim.run-until "to-worker" #("Complete" "RolledBack")) "RolledBack"))
  (assert (= (get sim.kube.deployments DEP "specReplicas") 1))
  ;; 新を止めたのは、旧が Ready に戻った後(RollingBack の restoredOldMs の後に新の replicas が 0)
  (assert-old-restored-before-new-stopped sim "to-worker")
  (assert (= sim.gaps 0)))


(deftest test-failure-while-observing-restores-the-old-first
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (sim.run-until "to-worker" #("Observing"))
  (for [_ (range 40)] (sim.step))       ; 旧の Pod は止め終えた
  (assert (= (len sim.pods) 0))
  (setv sim.healthy False)
  (assert (= (sim.run-until "to-worker" #("Complete" "RolledBack")) "RolledBack"))
  (assert (in "観察の間に" (. (get sim.state.rollouts "to-worker") status failure)))
  (assert (= (get sim.kube.deployments DEP "specReplicas") 1))
  (assert (= (. (get sim.state.jobs 0) replicas) 0))
  (assert-old-restored-before-new-stopped sim "to-worker")
  (assert (= sim.gaps 0)))


(deftest test-abort-rolls-back-in-the-same-order
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (sim.run-until "to-worker" #("Observing"))
  (setv version (. (get sim.state.meta "Rollout/to-worker") resource-version))
  (sim.call "PUT" "/resources/Rollout/to-worker" {"spec" (| FORWARD {"abort" True}) "resourceVersion" version})
  (assert (= (sim.run-until "to-worker" #("Complete" "RolledBack")) "RolledBack"))
  (assert (= (get sim.kube.deployments DEP "specReplicas") 1))
  (assert-old-restored-before-new-stopped sim "to-worker")
  ;; 中止の間も新は healthy のまま動き続け、旧が戻るまで止めなかった = 書き手が 0 の拍も、Ready の書き手が 0 の拍も無い
  (assert (= sim.gaps 0)))


(deftest test-coordinator-restart-mid-rollout-continues-from-the-saved-phase
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (sim.run-until "to-worker" #("StoppingOld"))
  (sim.restart-coordinator)
  (setv after-restart (. (get sim.state.rollouts "to-worker") status phase))
  (assert (= after-restart "StoppingOld"))
  (sim.run-until "to-worker" #("Observing"))
  (sim.restart-coordinator)                     ; 観察の途中でもう一度(報告が揃うまで Unknown = 失敗と数えない)
  (assert (= (sim.run-until "to-worker" #("Complete" "RolledBack")) "Complete"))
  (assert (= (phases-of sim "to-worker") ["WaitingNewReady" "StoppingOld" "Observing" "Complete"]))
  (assert (= (lfor c sim.kube.calls (get c "replicas")) [0]))   ; 旧を止める命令は 1 度だけ(冪等・観測が 0 なら出さない)
  (assert (= sim.gaps 0)))


(deftest test-deploy-flow-reapplying-replicas-is-reported-as-drift
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (sim.run-until "to-worker" #("Complete"))
  ;; 本番の配備の流れが Deployment の manifest(replicas: 1)を当て直す
  (setv (get sim.kube.deployments DEP "specReplicas") 1)
  (for [_ (range 12)] (sim.step))
  (setv drift (. (get sim.state.rollouts "to-worker") status drift))
  (assert (= #(drift.expected drift.observed) #(0 1)) drift)
  ;; 直さない(配備の流れと取り合わない)
  (assert (= (get sim.kube.deployments DEP "specReplicas") 1))
  ;; 戻ると消える
  (setv (get sim.kube.deployments DEP "specReplicas") 0)
  (for [_ (range 12)] (sim.step))
  (assert (is (. (get sim.state.rollouts "to-worker") status drift) None)))


(deftest test-reverse-rollout-brings-the-deployment-back-before-stopping-the-service
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (sim.run-until "to-worker" #("Complete"))
  (for [_ (range 40)] (sim.step))
  (sim.rollout "back" REVERSE)
  (assert (= (sim.run-until "back" #("Complete" "RolledBack")) "Complete"))
  (assert (= (get sim.kube.deployments DEP "specReplicas") 1) "rev-spec")
  (assert (= (. (get sim.state.jobs 0) replicas) 0) "rev-svc")
  (assert (= sim.gaps 0) sim.log)
  ;; 台数の持ち主は後の Rollout(back)へ移り、期待は 1。古い to-worker は食い違いを出さない
  (for [_ (range 12)] (sim.step))
  (assert (is (. (get sim.state.rollouts "to-worker") status drift) None) (. (get sim.state.rollouts "to-worker") status)))


(deftest test-dry-run-deployment-is-never-scaled-for-real
  (setv sim (Sim))
  (setv dry (| FORWARD {"from" (| (get FORWARD "from") {"dryRun" True})}))
  (sim.rollout "dry" dry)
  (assert (= (sim.run-until "dry" #("Complete" "RolledBack")) "Complete"))
  (assert (= (get sim.kube.deployments DEP "specReplicas") 1))           ; 本物の台数は変わらない
  (assert (= (lfor c sim.kube.calls #((get c "replicas") (get c "dryRun"))) [#(0 True)]))
  (assert (= (. (get sim.state.rollouts "dry") status simulated) {"Deployment:prod/app-writer" 0}))
  ;; 逆向きも dry-run で往復できる
  (sim.rollout "dry-back" (| REVERSE {"to" (| (get REVERSE "to") {"dryRun" True})}))
  (assert (= (sim.run-until "dry-back" #("Complete" "RolledBack")) "Complete"))
  (assert (= (get sim.kube.deployments DEP "specReplicas") 1))
  (assert (= (. (get sim.state.jobs 0) replicas) 0)))


(deftest test-rollouts-on-the-same-target-cannot-overlap-and-running-ones-cannot-be-deleted
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (setv #(_ overlap-status overlap-body) (responded sim.state (http-request "POST" "/resources/Rollout" {} {"name" "second" "spec" FORWARD}
                                                                     :actor "c-test") sim.now T))
  (assert (= overlap-status 409) overlap-body)
  (setv #(_ delete-status delete-body) (responded sim.state (http-request "DELETE" "/resources/Rollout/to-worker" {} None :actor "c-test")
                                                sim.now T))
  (assert (= delete-status 409) delete-body)
  ;; Rollout が扱っている Service は、所有者でも消せない(force なら消せる)
  (setv #(_ owned-status owned-body) (responded sim.state (http-request "DELETE" "/resources/Service/writer-a" {} None :actor "c-test")
                                              sim.now T))
  (assert (= owned-status 409) owned-body))


(deftest test-deploy-flow-reapplying-replicas-while-observing-is-reported-as-drift
  ;; 2026-09-24 00:58 の実弾: 観察の途中(Rollout は Observing)に本番の配備の流れが Deployment を当て直し、台数が 1 へ戻った。
  ;; 観察中の Rollout を「進行中」に数えていたので食い違いが出なかった。
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (sim.run-until "to-worker" #("Observing"))
  (for [_ (range 3)] (sim.step))
  (setv (get sim.kube.deployments DEP "specReplicas") 1)
  (for [_ (range 3)] (sim.step))
  (assert (= (sim.phase "to-worker") "Observing"))
  (setv drift (. (get sim.state.rollouts "to-worker") status drift))
  (assert (= #(drift.expected drift.observed) #(0 1)) drift))



(deftest test-a-config-only-change-before-the-rollout-does-not-stop-the-deployment-early
  ;; 2026-09-24 05:11 の実弾の形: 書き手を dry-run で動かして Ready にした後、設定だけを変えて(版は同じ)replicas 0 にし、
  ;; 20 秒後に Rollout を作った。新しい process は running になってから最初の拍を終える(最初の ReportReady)まで 20 秒かかる。
  ;; 止めた dry-run の process の Ready の報告は window(120 秒)の中に残っているが、数えてはならない — 本番(旧)を止めるのは
  ;; 新しい process が最初の報告をした後。
  (setv sim (Sim :window 120 :first-report-ms 20000))
  (setv spec {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN "replicas" 1 "readiness" {"windowSeconds" 120}})
  ;; dry-run の書き手を動かし、Ready の報告を出させる
  (sim.call "PUT" "/resources/Service/writer-a" {"spec" spec "resourceVersion" (. (get sim.state.meta "Service/writer-a") resource-version)})
  (for [_ (range 30)] (sim.step))
  (assert (is-not sim.proc None) "dry-run の書き手が動いている")
  (setv dry-instance (get sim.proc "instance"))
  (assert (in dry-instance sim.first-ready) sim.first-ready)
  ;; 設定だけを変えて(Program の引数 = 同一性だけが変わる・版は同じ)止める(05:10:53)
  (sim.call "PUT" "/resources/Service/writer-a"
            {"spec" (| spec {"run" (run (program-run "m:f" "--apply")) "replicas" 0})
             "resourceVersion" (. (get sim.state.meta "Service/writer-a") resource-version)})
  (for [_ (range 17)] (sim.step))
  (assert (is sim.proc None) sim.proc)
  ;; Rollout を作る(05:11:10)
  (sim.rollout "to-worker" (| FORWARD {"readyTimeoutSeconds" 120}))
  (assert (= (sim.run-until "to-worker" #("Observing" "Complete" "RolledBack")) "Observing"))
  (assert (is-not sim.proc None) "新しい process が動いている")
  (setv new-instance (get sim.proc "instance"))
  (assert (!= new-instance dry-instance))
  (assert (in new-instance sim.first-ready) sim.first-ready)
  ;; 本番(旧)を止めたのは、新しい process の最初の報告より後(前の dry-run の process の報告では止めない)
  (assert (>= sim.old-stopped-at (get sim.first-ready new-instance)) #(sim.old-stopped-at sim.first-ready))
  (assert (>= (- sim.old-stopped-at (get sim.proc "since")) (+ JOB-START 20000)) #(sim.old-stopped-at (get sim.proc "since")))
  (assert (= sim.gaps 0)))
